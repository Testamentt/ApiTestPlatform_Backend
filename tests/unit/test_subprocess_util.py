# subprocess_util 单元测试：白名单 / 超时 / returncode。
from __future__ import annotations

import pytest
from app.core.exceptions import AppError
from app.utils.subprocess_util import run_cmd


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
