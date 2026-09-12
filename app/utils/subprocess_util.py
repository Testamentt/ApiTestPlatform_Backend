# 唯一 subprocess 入口。why：命令白名单 + 超时 + 进程树清理收敛在单一边界，
# 业务禁止裸 subprocess.run（RULES.md §2.4）。MVP 用 Popen + communicate（无需后台线程防爆）。
from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

# why：父进程统一按 UTF-8 解码子进程输出（review N2）——必须同时让子进程按 UTF-8 写，
# 否则 Windows（cp936 控制台）下父子编码错位，中文断言信息乱码甚至解码失败
_CHILD_ENV_OVERRIDES = {"PYTHONIOENCODING": "utf-8"}


def _child_env() -> dict[str, str]:
    return {**os.environ, **_CHILD_ENV_OVERRIDES}


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    pid: int


def _name_allowed(name: str, whitelist: list[str]) -> bool:
    """白名单匹配：支持尾缀 `*` 前缀模式。why：Linux/Docker 下 sys.executable 常为
    python3.12（非 python），精确匹配会误杀容器内全部执行任务（review H1）。"""
    for entry in whitelist:
        if entry.endswith("*"):
            if name.startswith(entry[:-1]):
                return True
        elif name == entry:
            return True
    return False


def _validate_args(args: list[str]) -> None:
    """why：命令白名单 + 参数逐项校验，防止任意命令执行。"""
    if not args:
        raise AppError("INVALID_ARGS", detail="args 不能为空")
    name = os.path.basename(args[0]).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if not _name_allowed(name, get_settings().execution.command_whitelist):
        raise AppError("COMMAND_NOT_ALLOWED", detail=f"命令 {args[0]} 不在白名单")
    for arg in args[1:]:
        if not isinstance(arg, str) or len(arg) > 1024:
            raise AppError("INVALID_ARGS", detail="参数必须为字符串且≤1024 字符")


def _kill_tree_windows(pid: int) -> bool:
    # why：taskkill /T 递归杀子树（Windows 无进程组语义）；rc!=0 即失败，不能当成功
    return os.system(f"taskkill /T /F /PID {int(pid)}") == 0


def _kill_tree_posix(pid: int) -> bool:
    """POSIX 杀树：优先杀进程组（run_cmd 以 start_new_session 启动，故 pgid==pid）。

    why：`taskkill /T` 只在 Windows 存在——此前实现是 os.system("taskkill ...") + suppress(OSError)，
    在 Linux/Docker 上 os.system 既不抛异常也不杀进程，整个杀树静默失效（review N5）。
    额外守卫：pgid != pid 说明该 pid 不属于自建会话，killpg 会连调用者自己的进程组一起杀
    （Worker 自杀）——此时退化为单进程击杀。
    """
    try:
        pgid = os.getpgid(pid)
    except OSError as e:
        logger.warning("杀树失败 pid=%s（进程可能已退出）: %s", pid, e)
        return False
    try:
        if pgid == pid:
            os.killpg(pgid, signal.SIGKILL)
        else:
            logger.warning("pid=%s 非进程组长（pgid=%s），退化为单进程击杀", pid, pgid)
            os.kill(pid, signal.SIGKILL)
        return True
    except OSError as e:
        logger.warning("杀树失败 pid=%s: %s", pid, e)
        return False


def kill_process_tree(pid: int) -> bool:
    """best-effort 杀树，返回是否成功。why：超时后父进程可能已死（e.process.pid 失效），
    失败不影响终态；权威兜底是 scan_stale_tasks 从 DB 读 tasks.pid 再杀整棵树。
    失败必须留可见痕迹——静默失败会让排障者以为"已经杀干净了"（review N5）。"""
    if not pid:
        return False
    ok = _kill_tree_windows(pid) if os.name == "nt" else _kill_tree_posix(pid)
    if not ok:
        logger.warning("杀进程树失败 pid=%s（平台 %s），可能残留孤儿进程", pid, os.name)
    return ok


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
        # why：显式 UTF-8 + errors="replace"（review N2）——不指定编码时 Windows 用 cp936 解码，
        # 子进程输出含 cp936 不可解码字节会让读取线程抛 UnicodeDecodeError，communicate 返回 None，
        # 调用方随即在 result.stdout[-2000:] 上炸成 TypeError，真实失败原因被兜底成 internal
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
        cwd=cwd,
        shell=False,
        creationflags=flags,
        # why：POSIX 建独立会话 → 子进程成为组长，kill_process_tree 才能按组收掉整棵子树（review N5）
        start_new_session=os.name != "nt",
    )
    if on_start is not None:
        try:
            on_start(proc.pid)
        except Exception:
            # why：on_start 抛错时子进程已经在跑（如"任务已被取消"的竞态判定）——必须先杀再抛，
            # 否则进程孤儿化、无人回收（review C2/N5）
            kill_process_tree(proc.pid)
            proc.kill()
            proc.wait()
            raise
    start = time.monotonic()
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        kill_process_tree(proc.pid)  # best-effort
        proc.kill()
        proc.wait()
        raise AppError("SUBPROCESS_TIMEOUT", status_code=502, detail=f"timeout={timeout}s") from e
    duration_ms = int((time.monotonic() - start) * 1000)
    # why：errors="replace" 后 stdout/stderr 理论上不会是 None，但 Popen 在极少数平台/异常路径下仍可能
    # 回 None——统一兜底成空串，避免调用方对 None 做切片（review N2）
    stdout, stderr = stdout or "", stderr or ""
    if check and proc.returncode != 0:
        raise AppError("SUBPROCESS_FAILED", status_code=502, detail=stdout[-2000:])
    return CmdResult(proc.returncode, stdout, stderr, duration_ms, proc.pid)
