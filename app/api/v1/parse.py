# Swagger 解析路由。why：路由零逻辑，转发 SwaggerService；ParseResult 透出 warnings（宽容解析不静默）。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.swagger import ParseRequest, ParseResult
from app.services.swagger_service import SwaggerService

router = APIRouter(tags=["swagger"])


@router.post("/parse", response_model=ApiResponse[ParseResult], status_code=201)
def parse_swagger(
    payload: ParseRequest, db: Session = Depends(get_db)
) -> ApiResponse[ParseResult]:
    definition, warnings = SwaggerService(db).parse_document(payload.document, payload.version)
    return ApiResponse(
        data=ParseResult(
            version_id=definition.id,
            version=definition.version,
            hash_version=definition.hash_version,
            operation_count=len(definition.operation_ids),
            operation_ids=definition.operation_ids,
            warnings=warnings,
        )
    )
