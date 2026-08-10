# SwaggerService.parse_document 测试：解析落库 / auto 版本递增 / reparse 覆盖 / 超限 / 超阈值告警。
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from app.core.exceptions import AppError
from app.models.api_definition import ApiDefinition
from app.services.swagger_service import SwaggerService
from sqlalchemy import func, select


def _doc(operation_id="listUsers", **over):
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1.0"},
        "paths": {
            "/users": {
                "get": {
                    "operationId": operation_id,
                    "responses": {"200": {"description": "ok"}},
                    **over,
                }
            }
        },
    }


def test_parse_document_creates_definition(session_factory):
    with session_factory() as s:
        defn, warnings = SwaggerService(s).parse_document(_doc())
        assert defn.version == "v1"
        assert defn.operation_ids == ["listUsers"]
        assert defn.hash_version == 1
        assert warnings == []


def test_parse_auto_version_increments(session_factory):
    with session_factory() as s:
        svc = SwaggerService(s)
        d1, _ = svc.parse_document(_doc())
        d2, _ = svc.parse_document(_doc(operation_id="createUser"))
        assert d1.version == "v1"
        assert d2.version == "v2"


def test_parse_reparse_overwrites_same_version(session_factory):
    with session_factory() as s:
        svc = SwaggerService(s)
        svc.parse_document(_doc(operation_id="listUsers"), version="release-1")
        d2, _ = svc.parse_document(_doc(operation_id="otherOp"), version="release-1")
        assert d2.operation_ids == ["otherOp"]
        count = s.scalar(select(func.count()).select_from(ApiDefinition))
        assert count == 1  # 覆盖不膨胀版本表


def test_parse_too_large_raises(session_factory, monkeypatch):
    from app.services import swagger_service

    fake = SimpleNamespace(
        swagger=SimpleNamespace(max_upload_bytes=10, hash_version=1, max_operation_ids_warn=200)
    )
    monkeypatch.setattr(swagger_service, "get_settings", lambda: fake)
    with session_factory() as s:
        with pytest.raises(AppError) as exc:
            SwaggerService(s).parse_document(_doc())
        assert exc.value.code == "SWAGGER_TOO_LARGE"


def test_parse_warns_when_operation_count_exceeds(session_factory, monkeypatch, caplog):
    from app.services import swagger_service

    fake = SimpleNamespace(
        swagger=SimpleNamespace(
            max_upload_bytes=2_000_000, hash_version=1, max_operation_ids_warn=1
        )
    )
    monkeypatch.setattr(swagger_service, "get_settings", lambda: fake)
    doc = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1.0"},
        "paths": {
            "/a": {"get": {"operationId": "a", "responses": {"200": {"description": "ok"}}}},
            "/b": {"get": {"operationId": "b", "responses": {"200": {"description": "ok"}}}},
        },
    }
    with (
        caplog.at_level(logging.WARNING, logger="app.services.swagger_service"),
        session_factory() as s,
    ):
        defn, _ = SwaggerService(s).parse_document(doc)
    assert defn is not None  # 超阈值只 warning 继续入库
    assert any("超过阈值" in r.message for r in caplog.records)
