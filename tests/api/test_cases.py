# 用例接口测试：CRUD / operation_id 必填 / confirm 防幻觉 / 物理删除。
from __future__ import annotations


def _create_case(client, **over):
    payload = {"name": "get", "operation_id": "httpbin_get", "method": "GET", "path": "/get"}
    payload.update(over)
    return client.post("/api/v1/cases", json=payload)


def test_create_case_201_default_draft(client):
    r = _create_case(client)
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "draft"
    assert r.json()["data"]["operation_id"] == "httpbin_get"


def test_create_case_exposes_trust_score(client):
    # trust_score 可观测（手工=100），审核按血缘可信度优先 Review（P1-4 修复）
    r = _create_case(client)
    assert r.json()["data"]["trust_score"] == 100


def test_operation_id_required(client):
    assert _create_case(client, operation_id="").status_code == 422


def test_method_whitelist(client):
    assert _create_case(client, method="TRACE").status_code == 422


def test_path_must_start_with_slash(client):
    assert _create_case(client, path="get").status_code == 422


def test_confirm_draft_to_active(client):
    cid = _create_case(client).json()["data"]["id"]
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "active"


def test_confirm_records_reviewer(client, session_factory):
    # why：批准人审计（RULES §11.2「需传入 reviewer」）——reviewer 必须落库可追溯（review R3-2）
    from app.models.test_case import TestCase

    cid = _create_case(client).json()["data"]["id"]
    client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "alice"})
    with session_factory() as s:
        assert s.get(TestCase, cid).reviewer == "alice"


def test_confirm_empty_reviewer_422(client):
    # 契约：reviewer min_length=1——批准人不可为空（防幻觉护栏的审计前提）
    cid = _create_case(client).json()["data"]["id"]
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "  "})
    assert r.status_code == 422


def test_confirm_revalidates_schema(client, session_factory):
    # why：RULES §11.2「转 active 时重新执行 schema 校验」——draft 期间数据被改坏必须拦在
    # 状态迁移前（review R3-2）；绕过 API 校验器直接落库的坏数据在此被激活闸拦下
    from app.models.test_case import TestCase

    cid = _create_case(client).json()["data"]["id"]
    with session_factory() as s:
        case = s.get(TestCase, cid)
        case.path = "no-slash"  # 模拟绕过 API 校验的坏数据（path 必须以 / 开头）
        s.commit()
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r.status_code == 422
    assert r.json()["code"] == "CASE_INVALID_FOR_ACTIVE"
    with session_factory() as s:
        assert s.get(TestCase, cid).status == "draft"  # 校验失败不迁移状态


def test_update_case_fields(client):
    # why：PUT 端点此前零测试（review R3-2）——正常更新路径 + 字段落库
    cid = _create_case(client).json()["data"]["id"]
    r = client.put(
        f"/api/v1/cases/{cid}",
        json={"name": "renamed", "method": "POST", "path": "/users", "expected_status": 201},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert (data["name"], data["method"], data["path"], data["expected_status"]) == (
        "renamed",
        "POST",
        "/users",
        201,
    )


def test_update_case_rejects_bad_path(client):
    # why：CaseUpdate 校验器（path 前缀）须被测试锁定（review R3-2）
    cid = _create_case(client).json()["data"]["id"]
    r = client.put(f"/api/v1/cases/{cid}", json={"path": "users"})
    assert r.status_code == 422


def test_update_missing_404(client):
    r = client.put("/api/v1/cases/99999", json={"name": "x"})
    assert r.status_code == 404


# ---------- 更新两层闸（review C3）：形状闸 + 语义闸 ----------


def _active_case(client, reviewer="alice"):
    cid = _create_case(client).json()["data"]["id"]
    client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": reviewer})
    return cid


def test_update_active_case_downgrades_to_draft(client):
    # why：语义闸（review C3）——active 是"执行资格"而非永久标签，内容一变即失效，必须回 draft 重审
    cid = _active_case(client)
    r = client.put(f"/api/v1/cases/{cid}", json={"expected_status": 201})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "draft"
    # 批准人签名随内容失效被清空（旧批准不再代表新内容）
    assert client.get(f"/api/v1/cases/{cid}").json()["data"]["reviewer"] is None


def test_update_active_case_noop_keeps_active(client):
    # why：前端编辑弹窗会整表提交——同值更新不算语义变更，否则每次打开保存都无谓降级（误伤面收敛）
    cid = _active_case(client)
    current = client.get(f"/api/v1/cases/{cid}").json()["data"]
    r = client.put(
        f"/api/v1/cases/{cid}",
        json={"name": current["name"], "path": current["path"], "expected_status": 200},
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "active"


def test_update_active_case_assertions_downgrade(client):
    # 断言也是语义字段：改断言同样失去执行资格（AI 草稿的断言最需要人复审）
    cid = _active_case(client)
    r = client.put(
        f"/api/v1/cases/{cid}", json={"assertions": [{"path": "data.id", "op": "exists"}]}
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "draft"


def test_update_case_shape_gate_rejects_corrupted_row(client, session_factory):
    # why：形状闸——**active** 用例在库里被 out-of-band 写坏后，更新不得"带病续命"，
    # 且拒绝时不落半截数据（P3-4 后闸门只对 active 生效，故此处先激活）
    from app.models.test_case import TestCase

    cid = _active_case(client)
    with session_factory() as s:
        case = s.get(TestCase, cid)
        case.path = "no-slash"  # 模拟绕过 API 校验的坏数据
        s.commit()
    r = client.put(f"/api/v1/cases/{cid}", json={"name": "renamed"})
    assert r.status_code == 422
    assert r.json()["code"] == "CASE_INVALID"
    with session_factory() as s:
        assert s.get(TestCase, cid).name == "get"  # 校验失败 → 一个字段都没改


def test_update_draft_case_allows_repair_style_update(client, session_factory):
    """P3-4：draft 不做形状闸——"历史坏行 + 只想改名字"这类修复式更新必须放行。

    why：闸门只为「执行池」服务；draft 的安全性由 confirm 的激活闸兜底（它是进执行池的唯一入口）。
    """
    from app.models.test_case import TestCase

    cid = _create_case(client).json()["data"]["id"]  # 保持 draft
    with session_factory() as s:
        case = s.get(TestCase, cid)
        case.path = "no-slash"  # 坏数据
        s.commit()
    r = client.put(f"/api/v1/cases/{cid}", json={"name": "renamed"})
    assert r.status_code == 200  # 只改名字不再被自己的闸门卡死
    assert r.json()["data"]["name"] == "renamed"
    blocked = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "alice"})
    assert blocked.status_code == 422  # 但坏数据仍进不了执行池
    assert blocked.json()["code"] == "CASE_INVALID_FOR_ACTIVE"


def test_update_active_case_repairable_by_including_bad_field(client, session_factory):
    """active 用例的修复路径：把坏字段的正确值一起提交即可（合并后合法 → 放行并降级 draft）。"""
    from app.models.test_case import TestCase

    cid = _active_case(client)
    with session_factory() as s:
        case = s.get(TestCase, cid)
        case.path = "no-slash"
        s.commit()
    r = client.put(f"/api/v1/cases/{cid}", json={"path": "/users"})
    assert r.status_code == 200
    assert r.json()["data"]["path"] == "/users"
    assert r.json()["data"]["status"] == "draft"  # 语义闸：改过内容 → 重新审核


def test_update_case_shape_gate_allows_repair(client):
    # 修复式更新（同一 PUT 里把坏字段改成合法值）必须放行，否则坏行无法通过 API 修回
    cid = _create_case(client).json()["data"]["id"]
    client.put(f"/api/v1/cases/{cid}", json={"path": "/users"})
    assert client.get(f"/api/v1/cases/{cid}").json()["data"]["path"] == "/users"


def test_expected_status_out_of_range_422(client):
    # why：状态码边界（review N6）——99999 此前可静默进执行池，断言必然失败且看不出是脏数据
    cid = _create_case(client).json()["data"]["id"]
    assert _create_case(client, expected_status=42).status_code == 422
    assert _create_case(client, expected_status=600).status_code == 422
    assert client.put(f"/api/v1/cases/{cid}", json={"expected_status": 99999}).status_code == 422


def test_blank_name_422(client):
    # 契约：strip 后不得为空（"   " 会进生成代码注释与列表检索）
    assert _create_case(client, name="   ").status_code == 422
    cid = _create_case(client).json()["data"]["id"]
    assert client.put(f"/api/v1/cases/{cid}", json={"name": " \t "}).status_code == 422


def test_name_is_stripped(client):
    r = _create_case(client, name="  login ok  ")
    assert r.status_code == 201
    assert r.json()["data"]["name"] == "login ok"


def test_confirm_idempotent(client):
    cid = _create_case(client).json()["data"]["id"]
    client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    r2 = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r2.json()["data"]["status"] == "active"


def test_physical_delete(client):
    cid = _create_case(client).json()["data"]["id"]
    assert client.delete(f"/api/v1/cases/{cid}").status_code == 204
    assert client.get(f"/api/v1/cases/{cid}").status_code == 404


def test_get_missing_404(client):
    assert client.get("/api/v1/cases/99999").status_code == 404


# ---------- 断言层（契约层 + 字段层）----------


def test_create_case_with_assertions_201(client, session_factory):
    from app.models.test_case import TestCase

    assertions = [
        {"path": "status_code", "op": "eq", "value": 200},
        {"path": "data.id", "op": "eq", "value": 1},
    ]
    r = _create_case(client, assertions=assertions)
    assert r.status_code == 201
    cid = r.json()["data"]["id"]
    with session_factory() as s:
        stored = s.get(TestCase, cid)
        # why：落库必须是纯 dict（JSON 列）——pydantic 模型对象会炸序列化
        assert stored.assertions == assertions


def test_create_case_bad_assertion_path_422(client):
    # path 白名单正则（注入防线）：空格/引号/分号一律拒绝
    r = _create_case(client, assertions=[{"path": "a b", "op": "eq", "value": 1}])
    assert r.status_code == 422


def test_create_case_bad_assertion_op_422(client):
    r = _create_case(client, assertions=[{"path": "data.id", "op": "gt", "value": 1}])
    assert r.status_code == 422


def test_create_case_assertion_extra_field_422(client):
    r = _create_case(
        client,
        assertions=[{"path": "data.id", "op": "eq", "value": 1, "desc": "x"}],
    )
    assert r.status_code == 422


def test_update_case_assertions_validated(client, session_factory):
    from app.models.test_case import TestCase

    cid = _create_case(client).json()["data"]["id"]
    ok = client.put(
        f"/api/v1/cases/{cid}", json={"assertions": [{"path": "data.id", "op": "exists"}]}
    )
    assert ok.status_code == 200
    bad = client.put(f"/api/v1/cases/{cid}", json={"assertions": [{"path": "a;b", "op": "eq"}]})
    assert bad.status_code == 422
    with session_factory() as s:
        # why：非法更新被 422 拦下后，库里必须仍是上次合法值（部分失败不落半截数据）
        assert s.get(TestCase, cid).assertions == [{"path": "data.id", "op": "exists"}]


def test_confirm_revalidates_corrupted_assertions(client, session_factory):
    # why：激活闸（§11.2）——绕过 API 直改库的坏断言在 draft→active 前被拦下
    from app.models.test_case import TestCase

    cid = _create_case(client).json()["data"]["id"]
    with session_factory() as s:
        case = s.get(TestCase, cid)
        case.assertions = [{"path": 'x") or (', "op": "eq"}]  # 模拟历史坏数据
        s.commit()
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r.status_code == 422
    assert r.json()["code"] == "CASE_INVALID_FOR_ACTIVE"
