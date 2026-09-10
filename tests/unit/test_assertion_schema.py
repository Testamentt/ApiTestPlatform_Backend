# AssertionItem schema 单元测试：path 白名单正则 / op 白名单 / extra=forbid——
# 渲染进生成代码前的唯一注入防线（RULES §11.2）。
from __future__ import annotations

import pytest
from app.schemas.assertion import AssertionItem
from pydantic import ValidationError


@pytest.mark.parametrize(
    "path",
    ["status_code", "data", "data.id", "items.0.id", "a.b.0.c", "_x", "resp_1.body"],
)
def test_valid_path_accepted(path):
    item = AssertionItem(path=path, op="eq", value=1)
    assert item.path == path


@pytest.mark.parametrize(
    "path",
    [
        "",  # 空路径
        "a b",  # 空格
        "a;b",  # 分号
        'a"b',  # 引号
        "a'b",  # 单引号
        "a)",  # 括号
        ".lead",  # 点开头
        "a.",  # 点结尾（悬空段）
        "a..b",  # 空段
        "0abc",  # 数字开头
        "a\tb",  # 控制字符
        "a\nb",  # 换行（注入换行逃逸字符串字面量）
    ],
)
def test_invalid_path_rejected(path):
    with pytest.raises(ValidationError):
        AssertionItem(path=path, op="eq", value=1)


def test_op_whitelist():
    with pytest.raises(ValidationError):
        AssertionItem(path="a", op="gt", value=1)  # 未开放比较操作符
    with pytest.raises(ValidationError):
        AssertionItem(path="a", op="EQ", value=1)  # 大小写敏感


def test_extra_field_rejected():
    # why：extra=forbid——LLM 输出夹带未知字段整条拒绝，不让噪声进渲染层
    with pytest.raises(ValidationError):
        AssertionItem.model_validate({"path": "a", "op": "eq", "value": 1, "desc": "x"})


def test_value_accepts_any_json_type():
    for v in (None, 0, "s", True, [1, 2], {"k": "v"}):
        assert AssertionItem(path="a", op="contains", value=v).value == v


def test_path_max_length_enforced():
    with pytest.raises(ValidationError):
        AssertionItem(path="a" * 300, op="eq", value=1)
