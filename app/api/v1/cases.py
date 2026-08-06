# 用例路由。why：路由只做参数解析/转发，业务逻辑在 service（RULES.md §4）。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.case import CaseCreate, CaseRead, CaseUpdate, ConfirmBody, ConfirmResult
from app.schemas.common import ApiResponse, Page
from app.services.case_service import CaseService

router = APIRouter(tags=["cases"])


@router.get("/cases", response_model=ApiResponse[Page[CaseRead]])
def list_cases(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    method: str | None = None,
    operation_id: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
) -> ApiResponse[Page[CaseRead]]:
    items, total = CaseService(db).list_cases(
        page=page,
        page_size=page_size,
        status=status,
        method=method,
        operation_id=operation_id,
        keyword=keyword,
    )
    return ApiResponse(
        data=Page(items=items, total=total, page=page, page_size=page_size)
    )


@router.post("/cases", response_model=ApiResponse[CaseRead], status_code=201)
def create_case(
    payload: CaseCreate, db: Session = Depends(get_db)
) -> ApiResponse[CaseRead]:
    case = CaseService(db).create_case(payload)
    return ApiResponse(data=case)


@router.get("/cases/{case_id}", response_model=ApiResponse[CaseRead])
def get_case(case_id: int, db: Session = Depends(get_db)) -> ApiResponse[CaseRead]:
    case = CaseService(db).get_case(case_id)
    return ApiResponse(data=case)


@router.put("/cases/{case_id}", response_model=ApiResponse[CaseRead])
def update_case(
    case_id: int, payload: CaseUpdate, db: Session = Depends(get_db)
) -> ApiResponse[CaseRead]:
    case = CaseService(db).update_case(case_id, payload)
    return ApiResponse(data=case)


@router.delete("/cases/{case_id}", status_code=204, response_model=None)
def delete_case(case_id: int, db: Session = Depends(get_db)) -> None:
    CaseService(db).delete_case(case_id)


@router.post("/cases/{case_id}/confirm", response_model=ApiResponse[ConfirmResult])
def confirm_case(
    case_id: int, payload: ConfirmBody, db: Session = Depends(get_db)
) -> ApiResponse[ConfirmResult]:
    case = CaseService(db).confirm_case(case_id, payload.reviewer)
    return ApiResponse(data=ConfirmResult(case_id=case.id, status=case.status))
