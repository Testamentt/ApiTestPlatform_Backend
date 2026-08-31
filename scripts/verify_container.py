# H1 容器内白名单实测（CI docker-build job 专用）。why：白名单 `python3*` 前缀修复（review H1）
# 此前仅代码层验证，需在真实容器（python:3.12-slim，sys.executable 形态与宿主不同）内确认；
# 本机无 Docker 时由 CI 兜底：docker run 挂载本脚本执行，不改镜像内容。
from __future__ import annotations

import os
import sys

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.utils.subprocess_util import _name_allowed, run_cmd


def main() -> None:
    whitelist = get_settings().execution.command_whitelist
    exe_name = os.path.basename(sys.executable).lower()

    # ① 容器内真实可执行路径必须过白名单（H1 核心场景）
    assert _name_allowed(exe_name, whitelist), f"容器内 {exe_name} 未过白名单 {whitelist}"
    # ② python3.12 形态名命中 `python3*` 前缀模式（H1 修复点）
    assert _name_allowed("python3.12", whitelist), "python3* 前缀模式未命中 python3.12"
    # ③ run_cmd 真实执行一条子进程（端到端，不只是字符串匹配）
    result = run_cmd(
        [sys.executable, "-c", "print('h1-ok')"],
        timeout=get_settings().execution.pytest_timeout,
    )
    assert result.returncode == 0 and "h1-ok" in result.stdout, "容器内子进程执行失败"
    # ④ 白名单外命令被真实拒绝（防误放行的反向验证）
    try:
        run_cmd(["bash", "-c", "echo nope"], timeout=get_settings().execution.pytest_timeout)
    except AppError as e:
        assert e.code == "COMMAND_NOT_ALLOWED", f"预期 COMMAND_NOT_ALLOWED，实际 {e.code}"
    else:
        raise AssertionError("白名单外命令被放行")

    print(f"H1 OK: exe={sys.executable} name={exe_name} whitelist={whitelist}")


if __name__ == "__main__":
    main()
