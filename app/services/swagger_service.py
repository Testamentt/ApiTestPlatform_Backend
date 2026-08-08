# Swagger 解析落库。why：大小校验 + Parser 提取（无 Session，CPU 密集不占连接）+ reparse 覆盖 upsert；
# operation 数超阈值只记 warning 继续入库（IN(...200) 检索仍无碍，仅分析响应变长）。
from __future__ import annotations

import json
import logging

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.api_definition import ApiDefinition
from app.repositories.api_definition_repository import ApiDefinitionRepository
from app.utils.openapi_parser import parse_openapi

logger = logging.getLogger(__name__)


class SwaggerService:
    def __init__(self, session) -> None:
        self.session = session
        self.repo = ApiDefinitionRepository(session)

    def parse_document(
        self, document: dict, version: str | None = None
    ) -> tuple[ApiDefinition, list[str]]:
        """解析 OpenAPI 3.x 入库。why：先纯校验/解析（无 Session）再落库，遵守短事务分界（RULES §2.1）。
        返回 (definition, warnings)——宽容解析的问题逐条透出，不静默（D4/D5）。"""
        settings = get_settings()
        if len(json.dumps(document)) > settings.swagger.max_upload_bytes:
            raise AppError(
                "SWAGGER_TOO_LARGE",
                status_code=422,
                detail=f"文档超过 {settings.swagger.max_upload_bytes} 字节上限",
            )
        parsed = parse_openapi(document)  # 非法文档在此抛 AppError（422）
        if len(parsed.operation_ids) > settings.swagger.max_operation_ids_warn:
            logger.warning(
                "Swagger 接口数 %s 超过阈值 %s，仅提示继续入库（IN(...) 检索仍无碍）",
                len(parsed.operation_ids),
                settings.swagger.max_operation_ids_warn,
            )
        resolved = self.repo.resolve_version(version)
        definition = self.repo.upsert(
            version=resolved,
            hash_version=settings.swagger.hash_version,
            operation_ids=parsed.operation_ids,
            operation_hashes=parsed.operation_hashes,
            operation_contracts=parsed.operation_contracts,
        )
        return definition, parsed.warnings
