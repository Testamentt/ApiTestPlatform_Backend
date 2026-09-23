# 测试进程环境准备。why：必须在任何 app.* 导入之前设好 env（get_settings 是启动单例，导入期即固化配置）。
# 这里把执行 workspace 指到临时目录——测试会真实生成 test_*.py 与 report.html，不能污染仓库 .workspace，
# 更不能因跨测试/跨轮次复用同一 task_id 而读到上一轮遗留的报告文件（review N3 的 404 用例即被此坑到）。
from __future__ import annotations

import os
import tempfile

os.environ["TESTPLATFORM_EXECUTION_WORKSPACE_DIR"] = tempfile.mkdtemp(
    prefix="testplatform-test-ws-"
)
