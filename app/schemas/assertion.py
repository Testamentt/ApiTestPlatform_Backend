# 断言规则 schema。why：渲染进生成代码前必须过结构校验——path 白名单正则（status_code 或
# a.b.0.c 点路径）从源头消灭注入面（引号/括号/分号在校验层即拒绝，渲染层无需再防御）；
# op 固定 4 种映射到固定表达式模板；value 一律 repr 进字面量。LLM 输出与手工入参共用（RULES §11.2）。
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# why：只放行 status_code 或「字母开头标识符 + .数字/标识符」点路径——
# 数字段约定为数组下标（items.0.id），其余字符形态一律 422，正则即注入防线。
ASSERTION_PATH_PATTERN = r"^(?:status_code|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)$"

AssertionOp = Literal["eq", "ne", "contains", "exists"]


class AssertionItem(BaseModel):
    """单条响应断言（契约层/字段层共用）。

    - path=status_code：HTTP 状态码断言（契约层基线，执行引擎恒定渲染，重复不报错）
    - 其他 path：响应体 JSON 点路径取值（非 JSON 响应按取值 None 处理，断言失败而非报错）
    - op 语义：eq 等于 / ne 不等于 / contains 响应值包含 value / exists 响应值非 None
    """

    model_config = ConfigDict(extra="forbid")

    # max_length 防 prompt 注入超长路径/生成代码爆炸；正则已限制字符集
    path: str = Field(pattern=ASSERTION_PATH_PATTERN, max_length=255)
    op: AssertionOp
    value: Any = None  # exists 时忽略；value=None 本身也是合法期望（字段为 null）
