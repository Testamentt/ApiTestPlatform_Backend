# 多阶段构建（面试导向）：builder 装依赖（层可缓存）+ runtime 精简层、非 root（RULES §3.3）。
# 本机 Docker 未装时由 GitHub Actions 的 docker-build job 验证本文件。
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
# why：非 editable 安装——-e 会创建指向 builder 阶段 /app 的软链接，runtime 阶段无源码会 ModuleNotFoundError
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /install /usr/local
# why：runtime 再复制源码到 /app/app（与 site-packages 双份）——WORKDIR=/app 使 import app 命中源码副本，
# PROJECT_ROOT=Path(__file__).parents[2]=/app，prompts/ 与 config/ 才能被正确解析到
COPY --from=builder /app/app ./app
# prompts/ 是运行时 prompt 模板（PROMPT_VERSION 读取），必须随镜像
COPY prompts ./prompts
# settings.yaml 被 gitignore 不入库，容器用 example 兜底默认值，运行配置走 env 覆盖
COPY config/settings.example.yaml ./config/settings.yaml
# 预建数据/工作目录并 chown appuser：named volume 首次挂载继承该属主，非 root 可写（§3.3）
RUN mkdir -p /app/data /app/.workspace && useradd -m appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
