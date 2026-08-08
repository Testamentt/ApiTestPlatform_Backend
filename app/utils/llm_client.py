# 唯一 LLM 入口。why：超时/错误分类/重试/成本日志收敛在单一边界（RULES §9.1 评审红线，业务禁止裸调 SDK）；
# response_format 强制 JSON + _extract_json 格式清洗双重容错；校验失败抛 AppError 携带 raw_response 供 generation_log 落库。
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

from httpx import Timeout
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.exceptions import AppError

# 剥离 Markdown 代码块围栏（```json / ``` / ```JSON / ```python 等任意标签，大小写不敏感）
_MD_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_]*\s*\n?", re.MULTILINE)
_MD_FENCE_END_RE = re.compile(r"\n?```\s*$", re.MULTILINE)


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LlmClient:
    def __init__(self) -> None:
        s = get_settings().llm
        self.settings = s
        # why：api_key_env 是环境变量名（密钥只放 .env，RULES §3.1/§8），不在 config 明文
        self.client = OpenAI(
            api_key=os.environ.get(s.api_key_env, ""),
            base_url=s.base_url,
            timeout=Timeout(s.connect_timeout, read=s.read_timeout),
        )

    def _extract_json(self, raw: str) -> str:
        """从 LLM 响应提取 JSON：剥离代码块围栏/首尾空白。why：DeepSeek 偶发把 JSON 包在 ```json 外层，
        直接 json.loads 会误杀合法响应——先清洗再解析，提高成功率（细节 1）。大小写/任意标签均可剥离。"""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = _MD_FENCE_RE.sub("", cleaned)
        if cleaned.endswith("```"):
            cleaned = _MD_FENCE_END_RE.sub("", cleaned)
        return cleaned.strip() or raw

    def chat_json(self, system: str, user: str, *, schema: type[BaseModel]) -> tuple[BaseModel, LlmUsage]:
        """结构化输出调用：response_format 强制 JSON → _extract_json 清洗 → schema 严格校验。
        返回 (已校验模型, usage)；校验失败抛 LLM_VALIDATION_FAILED（携带 raw_response），瞬时异常重试后抛 LLM_FAILED。"""
        s = self.settings
        # why：§9.3 输入长度预检——超长上下文裸奔会被上游 400 拒绝且浪费 token，调用前明确拒绝
        total_chars = len(system) + len(user)
        if total_chars > s.max_input_chars:
            raise AppError(
                "LLM_INPUT_TOO_LONG",
                status_code=422,
                detail=f"输入超长（{total_chars} 字符 > 上限 {s.max_input_chars}），请精简 Swagger 或分批生成",
            )
        last_exc: Exception | None = None
        # why：range(max_retries + 1) = 首次尝试 + max_retries 次重试（§9.4「最多重试 3 次」）；
        # 退避 1s/3s/9s（3**attempt），仅瞬时异常重试
        for attempt in range(s.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=s.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=s.temperature,  # 确定性，可复现（RULES §9.3）
                    max_tokens=s.max_tokens,
                )
                raw = resp.choices[0].message.content or ""
                data = json.loads(self._extract_json(raw))
                parsed = schema.model_validate(data)  # 严格校验（extra 由 schema 决定）
                # why：SDK 的 resp.usage 为 Optional——兼容端点/代理可返回 usage:null，
                # 直接访问会抛裸 AttributeError 逃逸错误分类边界，须防御
                u = resp.usage
                if u is None:
                    usage = LlmUsage(0, 0, 0)
                else:
                    usage = LlmUsage(u.prompt_tokens or 0, u.completion_tokens or 0, u.total_tokens or 0)
                return parsed, usage
            except (RateLimitError, APIConnectionError, APITimeoutError) as e:
                last_exc = e  # 瞬时：限流/连接/超时 → 重试
            except APIStatusError as e:
                if e.status_code < 500:
                    raise AppError("LLM_FAILED", status_code=502, detail=f"LLM 拒绝: {e}") from e
                last_exc = e  # 5xx → 重试
            except (json.JSONDecodeError, ValidationError) as e:
                exc = AppError(
                    "LLM_VALIDATION_FAILED",
                    status_code=502,
                    detail=f"Pydantic 校验失败: {e}",  # 具体错误供 rejected_detail（间隙 2）
                )
                exc.raw_response = raw  # 原始响应随异常携带，generation_log 落库用
                raise exc from e  # 校验失败不可重试（RULES §9.4）
            if attempt < s.max_retries:
                time.sleep(s.retry_backoff * (3**attempt))  # 指数退避 1s/3s/9s（§9.4）
        raise AppError("LLM_FAILED", status_code=502, detail=f"LLM 重试耗尽: {last_exc}") from last_exc
