# diff 纯函数单测：added/removed/changed/identical + breaking 联合判定四场景 + hash_version 演进 + suggested_remap。
from __future__ import annotations

from app.utils.impact_diff import build_suggested_remap, diff_operations


def _hashes(ids, req="1", resp="1"):
    return {oid: {"request": req, "response": resp} for oid in ids}


def _contract(required=None, signature=None, response_codes=None):
    c = {"required": required or [], "signature": signature or {}}
    if response_codes is not None:
        c["response_status_codes"] = response_codes
    return c


# ---------- 基础 diff ----------


def test_identical_when_hashes_equal():
    d = diff_operations(
        ["a"], _hashes(["a"]), {"a": _contract()}, ["a"], _hashes(["a"]), {"a": _contract()}
    )
    assert d.identical and d.changed == [] and d.added == [] and d.removed == []


def test_added_removed_detected():
    old_ids, old_h, old_c = (
        ["a", "b", "c"],
        _hashes(["a", "b", "c"]),
        {"a": _contract(), "b": _contract(), "c": _contract()},
    )
    new_ids, new_h, new_c = (
        ["b", "c", "d"],
        _hashes(["b", "c", "d"]),
        {"b": _contract(), "c": _contract(), "d": _contract()},
    )
    d = diff_operations(old_ids, old_h, old_c, new_ids, new_h, new_c)
    assert d.removed == ["a"]
    assert d.added == ["d"]
    assert d.changed == []
    assert not d.identical


def test_hash_change_detected():
    old = ["b", "c"]
    old_h = _hashes(["b", "c"])
    new_h = {
        "b": {"request": "2", "response": "1"},
        "c": {"request": "1", "response": "1"},
    }  # 仅 b 变
    d = diff_operations(
        old, old_h, {x: _contract() for x in old}, old, new_h, {x: _contract() for x in old}
    )
    assert d.changed == ["b"]


def test_hash_version_mismatch_marks_all_changed():
    # 哈希算法升级：公共接口保守全标变更（不重建旧快照）
    d = diff_operations(
        ["a", "b"],
        _hashes(["a", "b"]),
        {"a": _contract(), "b": _contract()},
        ["a", "b"],
        _hashes(["a", "b"]),
        {"a": _contract(), "b": _contract()},
        hash_version_equal=False,
    )
    assert d.changed == ["a", "b"]


# ---------- breaking 联合判定（F2） ----------


def _diff_breaking(old_c, new_c):
    return diff_operations(
        ["op"], _hashes(["op"]), {"op": old_c}, ["op"], _hashes(["op"], req="2"), {"op": new_c}
    )


def test_breaking_required_tighten():
    d = _diff_breaking(_contract(["a"]), _contract(["a", "b"]))
    assert d.breaking_changed == ["op"]


def test_breaking_required_relax_is_non_breaking():
    d = _diff_breaking(_contract(["a", "b"]), _contract(["a"]))
    assert d.breaking_changed == []
    assert d.non_breaking_changed == ["op"]


def test_breaking_type_change():
    d = _diff_breaking(
        _contract(signature={"body.x": {"type": "string", "enum": []}}),
        _contract(signature={"body.x": {"type": "integer", "enum": []}}),
    )
    assert d.breaking_changed == ["op"]


def test_breaking_field_deleted():
    d = _diff_breaking(
        _contract(signature={"body.name": {"type": "string", "enum": []}}),
        _contract(),
    )
    assert d.breaking_changed == ["op"]


def test_breaking_enum_removed():
    d = _diff_breaking(
        _contract(signature={"body.status": {"type": "string", "enum": ["active", "pending"]}}),
        _contract(signature={"body.status": {"type": "string", "enum": ["active"]}}),
    )
    assert d.breaking_changed == ["op"]


def test_enum_added_is_non_breaking():
    d = _diff_breaking(
        _contract(signature={"body.status": {"type": "string", "enum": ["active"]}}),
        _contract(signature={"body.status": {"type": "string", "enum": ["active", "pending"]}}),
    )
    assert d.breaking_changed == []
    assert d.non_breaking_changed == ["op"]


def test_breaking_status_code_change():
    # F1：200→202（响应状态码集合变化，schema 相同）也必须判 breaking——下游按旧码断言会静默失败
    d = _diff_breaking(
        _contract(response_codes=["200"]),
        _contract(response_codes=["202"]),
    )
    assert d.breaking_changed == ["op"]


def test_breaking_response_status_code_added():
    # 新增响应码（如补 409）不破坏已有断言，判 non-breaking
    d = _diff_breaking(
        _contract(response_codes=["200"]),
        _contract(response_codes=["200", "409"]),
    )
    assert d.breaking_changed == []


def test_contract_missing_is_breaking():
    # 从无请求体变为有请求体（contract 由 None 变为有）保守判 breaking
    d = diff_operations(
        ["op"],
        _hashes(["op"]),
        {"op": None},
        ["op"],
        _hashes(["op"], req="2"),
        {"op": _contract(["x"])},
    )
    assert d.breaking_changed == ["op"]


# ---------- suggested_remap（D2，只建议不自动重绑） ----------


def test_suggested_remap_similar_names():
    assert build_suggested_remap(["listUsers"], ["getUsers"]) == {"listUsers": "getUsers"}


def test_suggested_remap_ignores_unrelated():
    # 0.706(命中) / 0.545(fetchAllUsers，保守漏配是设计选择) / 0.30(不相关排除)
    assert build_suggested_remap(["listUsers"], ["createOrder"]) == {}


def test_suggested_remap_empty_on_missing_side():
    assert build_suggested_remap([], ["getUsers"]) == {}
    assert build_suggested_remap(["listUsers"], []) == {}
