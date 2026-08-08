# 影响分析路由。why：路由零逻辑，转发 ImpactService；regression 复用 Phase 1 执行引擎（Lookup-Create 幂等）。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.impact import AnalyzeRequest, AnalyzeResult, RegressionResult
from app.services.impact_service import ImpactService

router = APIRouter(tags=["impact"])


@router.post("/impact/analyze", response_model=ApiResponse[AnalyzeResult])
def analyze_impact(
    payload: AnalyzeRequest, db: Session = Depends(get_db)
) -> ApiResponse[AnalyzeResult]:
    result = ImpactService(db).analyze(payload.document, payload.new_version, payload.old_version)
    return ApiResponse(data=result)


@router.post(
    "/impact/{analysis_id}/regression",
    response_model=ApiResponse[RegressionResult],
    status_code=202,
)
def regression(analysis_id: int, db: Session = Depends(get_db)) -> ApiResponse[RegressionResult]:
    result = ImpactService(db).regression(analysis_id)
    return ApiResponse(data=result)
