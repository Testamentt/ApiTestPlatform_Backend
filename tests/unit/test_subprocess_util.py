# subprocess_util 单元测试：白名单 / 超时 / returncode / 编码 / 杀树（review N2/N5）。
from __future__ import annotations

import subprocess
import sys

import pytest
from app.core.exceptions import AppError
from app.utils.subprocess_util import kill_process_tree, run_cmd


def test_whitelist_rejected():
    with pytest.raises(AppError) as exc:
        run_cmd(["rm", "-rf", "/"], timeout=5)
    assert exc.value.code == "COMMAND_NOT_ALLOWED"


def test_whitelist_prefix_pattern_python3():
    # why：Linux/Docker 下 sys.executable 是 python3.12，白名单 python3* 前缀必须放行（review H1）
    from app.utils.subprocess_util import _name_allowed

    whitelist = ["python", "python3*", "pytest"]
    assert _name_allowed("python3.12", whitelist)
    assert _name_allowed("python3", whitelist)
    assert _name_allowed("python", whitelist)
    assert _name_allowed("pytest", whitelist)  # 注：_validate_args 已先剥离 .exe
    assert not _name_allowed("bash", whitelist)
    assert not _name_allowed("python2", whitelist)  # python2 不在 python3* 前缀内


def test_ok_returns_cmd_result():
    res = run_cmd(["python", "-c", "print('hi')"], timeout=30)
    assert res.returncode == 0
    assert "hi" in res.stdout
    assert res.pid > 0


def test_nonzero_raises_when_check():
    with pytest.raises(AppError) as exc:
        run_cmd(["python", "-c", "import sys; sys.exit(1)"], timeout=30)
    assert exc.value.code == "SUBPROCESS_FAILED"


def test_nonzero_allowed_when_check_false():
    res = run_cmd(["python", "-c", "import sys; sys.exit(1)"], timeout=30, check=False)
    assert res.returncode == 1


def test_timeout_raises():
    with pytest.raises(AppError) as exc:
        run_cmd(["python", "-c", "import time; time.sleep(10)"], timeout=1)
    assert exc.value.code == "SUBPROCESS_TIMEOUT"


# ---------- 编码（review N2）：父子都按 UTF-8，不再依赖宿主 locale ----------


def test_utf8_chinese_output_round_trips():
    # why：Windows 默认 cp936 解码子进程输出——中文断言信息会乱码，cp936 不可解码的字符（弯引号）
    # 更会让读取线程抛 UnicodeDecodeError → stdout=None → 调用方 TypeError 掩盖真实失败
    res = run_cmd(
        ["python", "-c", "print('中文断言信息 “弯引号” — 破折号')"],
        timeout=30,
    )
    assert res.returncode == 0
    assert "中文断言信息" in res.stdout
    assert "“弯引号”" in res.stdout and "—" in res.stdout


def test_undecodable_bytes_do_not_break_caller():
    # errors="replace"：非法字节降级为替换字符，stdout 永远是 str（绝不是 None）
    res = run_cmd(
        [
            "python",
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\x80\\xff ok'); sys.stdout.flush()",
        ],
        timeout=30,
        check=False,
    )
    assert isinstance(res.stdout, str)
    assert res.stdout.endswith("ok")
    assert "\ufffd" in res.stdout


# ---------- 杀树（review N5）：跨平台分派 + 失败可见 + 真实杀进程 ----------


def test_kill_tree_dispatches_by_platform(monkeypatch):
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.utils.subprocess_util._kill_tree_windows",
        lambda pid: calls.append(("win", pid)) is None or True,
    )
    monkeypatch.setattr(
        "app.utils.subprocess_util._kill_tree_posix",
        lambda pid: calls.append(("posix", pid)) is None or True,
    )
    monkeypatch.setattr("app.utils.subprocess_util.os.name", "nt")
    assert kill_process_tree(4321) is True
    monkeypatch.setattr("app.utils.subprocess_util.os.name", "posix")
    assert kill_process_tree(4321) is True
    assert calls == [("win", 4321), ("posix", 4321)]


def test_kill_tree_failure_is_visible(monkeypatch, caplog):
    # why：此前 Linux 上 os.system("taskkill") 失败完全静默（suppress(OSError) 是空操作）——
    # 排障者会误以为"已经杀干净了"；失败必须留 warning 且返回 False
    monkeypatch.setattr("app.utils.subprocess_util._kill_tree_windows", lambda pid: False)
    monkeypatch.setattr("app.utils.subprocess_util.os.name", "nt")
    with caplog.at_level("WARNING"):
        assert kill_process_tree(999) is False
    assert any("杀进程树失败" in r.message for r in caplog.records)


def test_kill_tree_empty_pid_is_noop():
    assert kill_process_tree(0) is False


def test_kill_tree_really_kills_process():
    # 真实子进程（POSIX 建独立会话，与 run_cmd 一致）→ 杀树后父进程必须真的退出
    kw = {"start_new_session": True} if sys.platform != "win32" else {}
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kw)
    try:
        assert kill_process_tree(proc.pid) is True
        proc.wait(timeout=15)  # 未被杀则在此超时
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_on_start_failure_kills_child(monkeypatch):
    # why：on_start（如 mark_running 发现任务已被取消）抛错时子进程已在运行——必须先杀再抛，
    # 否则进程孤儿化（review C2/N5）
    killed: list[int] = []
    monkeypatch.setattr(
        "app.utils.subprocess_util.kill_process_tree",
        lambda pid: killed.append(pid) is None or True,
    )

    def _boom(pid: int) -> None:
        raise AppError("TASK_CANCELLED", status_code=409)

    with pytest.raises(AppError) as exc:
        run_cmd(["python", "-c", "import time; time.sleep(30)"], timeout=30, on_start=_boom)
    assert exc.value.code == "TASK_CANCELLED"
    assert killed and killed[0] > 0
