# 唯一 subprocess 入口。why：命令白名单 + 超时 + 进程树清理收敛在单一边界，
# 业务禁止裸 subprocess.run（RULES.md §2.4）。MVP 用 Popen + communicate（无需后台线程防爆）。
from __future__ import annotations

import os
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import AppError


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    pid: int


def _validate_args(args: list[str]) -> None:
    """why：命令白名单 + 参数逐项校验，防止任意命令执行。"""
    if not args:
        raise AppError("INVALID_ARGS", detail="args 不能为空")
    name = os.path.basename(args[0]).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name not in get_settings().execution.command_whitelist:
        raise AppError("COMMAND_NOT_ALLOWED", detail=f"命令 {args[0]} 不在白名单")
    for arg in args[1:]:
        if not isinstance(arg, str) or len(arg) > 1024:
            raise AppError("INVALID_ARGS", detail="参数必须为字符串且 ≤1024 字符")


def kill_process_tree(pid: int) -> None:
    """best-effort 杀树。why：超时后父进程可能已死（e.process.pid 失效），失败不影响终态；
    权威兜底是 scan_stale_tasks 从 DB 读 tasks.pid 再杀整棵树。"""
    if not pid:
        return
    with suppress(OSError):
        os.system(f"taskkill /T /F /PID {pid}")


def run_cmd(
    args: list[str],
    timeout: int,
    *,
    check: bool = True,
    cwd: str | Path | None = None,
    on_start=None,
) -> CmdResult:
    """唯一 subprocess 入口。on_start(pid) 在进程启动后立即回调（供落库，超时劫持依据）。"""
    _validate_args(args)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        shell=False,
        creationflags=flags,
    )
    if on_start is not None:
        on_start(proc.pid)
    start = time.monotonic()
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        kill_process_tree(proc.pid)  # best-effort
        proc.kill()
        proc.wait()
        raise AppError("SUBPROCESS_TIMEOUT", status_code=502, detail=f"timeout={timeout}s") from e
    duration_ms = int((time.monotonic() - start) * 1000)
    if check and proc.returncode != 0:
        raise AppError("SUBPROCESS_FAILED", status_code=502, detail=stdout[-2000:])
    return CmdResult(proc.returncode, stdout, stderr, duration_ms, proc.pid)
