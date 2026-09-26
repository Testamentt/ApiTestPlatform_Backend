# 统一错误响应测试（P2-1）：入参校验 422 与「服务层校验失败」都必须是 {code, message, detail} 结构。
# why：此前框架 422 返回 {"detail": [...]}，前端得为它单写兜底分支；统一后前端只认一种契约。
from __future__ import annotations

from app.main import format_validation_errors


def test_query_validation_error_is_unified(client):
    r = client.get("/api/v1/tasks?page_size=1000")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "REQUEST_VALIDATION_ERROR"
    assert body["message"]
    # detail 变成逐字段结构（field/message/type），而不是框架的原始错误数组
    assert isinstance(body["detail"], list) and body["detail"]
    assert body["detail"][0]["field"] == "query.page_size"


def test_body_validation_error_is_unified(client):
    # method 白名单外 → pydantic 校验失败：响应结构必须与业务错误一致
    r = client.post(
        "/api/v1/cases",
        json={"name": "x", "method": "TRACE", "path": "/get", "operation_id": "op"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "REQUEST_VALIDATION_ERROR"
    assert body["detail"][0]["field"] == "method"


def test_validation_detail_does_not_echo_input(client):
    # why：框架原始错误里有 input 字段（回显用户输入）——body 里可能是密码/大对象，不能透出
    r = client.post(
        "/api/v1/cases",
        json={"name": "x", "method": "GET", "path": "no-slash", "operation_id": "op"},
    )
    assert r.status_code == 422
    raw = r.text
    assert "no-slash" not in raw  # 输入值不回显
    assert set(r.json()["detail"][0]) == {"field", "message", "type"}


def test_format_validation_errors_strips_body_prefix():
    # 纯函数边界：loc 里的 "body" 段要去掉（前端拿到的 field 直接是字段名），定位为空时兜底 "body"
    formatted = format_validation_errors(
        [
            {"loc": ("body", "name"), "msg": "too short", "type": "value_error"},
            {"loc": (), "msg": "bad", "type": "value_error"},
        ]
    )
    assert formatted[0]["field"] == "name"
    assert formatted[1]["field"] == "body"
