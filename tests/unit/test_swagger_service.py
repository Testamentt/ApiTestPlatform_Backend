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
        # why=2：L3 指纹算法 md5→sha256 时 hash_version 递增，快照记录当前算法代际
        assert defn.hash_version == 2
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


def test_parse_version_collision_returns_existing(session_factory, monkeypatch):
    """P2-6：并发解析同一个新版本号时撞 UNIQUE(api_definitions.version)。

    正确行为 = 查回「赢家」行返回（并发语义 = 串行语义），而不是 IntegrityError 冒泡成 500。
    why：用 `get_by_version` 返回 None 模拟"另一个请求也以为版本号可用"的 TOCTOU 窗口——
    赢家行已提交，故收敛逻辑的第一次重查即可命中（不会真的 sleep）。
    """
    with session_factory() as s:
        svc = SwaggerService(s)
        svc.parse_document(_doc(operation_id="listUsers"), version="v-collide")
        real_lookup = svc.repo.get_by_version
        calls = {"n": 0}

        def stale_lookup(version):
            # 只让"插入前的那次前查"落空——精确复现 TOCTOU 窗口（收敛重查仍走真实查询）
            calls["n"] += 1
            return None if calls["n"] == 1 else real_lookup(version)

        monkeypatch.setattr(svc.repo, "get_by_version", stale_lookup)
        defn, _ = svc.parse_document(_doc(operation_id="createUser"), version="v-collide")
        assert defn.version == "v-collide"
        assert defn.operation_ids == ["listUsers"]  # 返回先落库的赢家，而不是报错/半截数据
        count = s.scalar(select(func.count()).select_from(ApiDefinition))
        assert count == 1  # 唯一约束 + 查回赢家 = 1 行、0 个 500
