# 本项目编码格式：UTF-8（无 BOM）。
# 本项目编码格式：UTF-8（无 BOM）。
# 用例业务编排。why：业务规则（draft→active 防幻觉护栏、两层更新闸、删除）收敛在 service，路由零逻辑。
from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.enums import CaseStatus
from app.models.test_case import TestCase
from app.repositories.api_definition_repository import ApiDefinitionRepository
from app.repositories.case_repository import CaseRepository
from app.schemas.case import CaseCreate, CaseUpdate
from app.services.generation_validators import (
    contract_from_snapshot,
    validate_case,
)

logger = logging.getLogger(__name__)


class CaseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CaseRepository(session)
        # why：confirm 是进执行池的唯一入口，而形状校验（CaseCreate）证明不了
        # 「这条用例属于它声明的那个接口」——method/path 与接口对不上也能过。契约快照是
        # confirm 阶段唯一的第二数据源（用例不存文档引用，confirm 拿不到 document）。
        self.api_defs = ApiDefinitionRepository(session)

    def create_case(self, payload: CaseCreate) -> TestCase:
        # why：mode="json" 把嵌套 AssertionItem 拍平成 dict——JSON 列无法序列化 pydantic 模型
        case = TestCase(**payload.model_dump(mode="json"))
        return self.repo.add(case)

    def get_case(self, case_id: int) -> TestCase:
        return self.repo.get_or_raise(case_id)

    def update_case(self, case_id: int, payload: CaseUpdate) -> TestCase:
        """两层闸更新。

        why：update 是 exclude_unset 部分更新——此前既不重校验也不降级，active 用例被改成坏数据后
        仍留在执行池（错误断言/非法 path 直到跑挂才暴露）。现补：
        ① 形状闸——**只对 active 用例**校验合并后的完整记录：执行池必须始终是合法数据。
           draft 不做形状闸：否则「历史坏行 + 只想改个名字」这类修复式更新会被自己的闸门卡死；
           draft 的安全性由 confirm 的激活闸 `_revalidate_for_active` 兜底（它是进执行池的唯一入口）。
        ② 语义闸——active 用例的语义字段一变即降级 draft，重新走人工审核（active 是执行资格，不是永久标签）。
        """
        case = self.repo.get_or_raise(case_id)
        # why：mode="json" 同 create_case——assertions 嵌套模型须落 dict 才能进 JSON 列，也便于与库中值比较
        changes = payload.model_dump(exclude_unset=True, mode="json")
        merged = {
            field: changes.get(field, getattr(case, field)) for field in CaseCreate.model_fields
        }
        if case.status == CaseStatus.ACTIVE.value:
            self._validate_merged(merged)
        semantic_changed = [
            field for field, value in changes.items() if value != getattr(case, field)
        ]
        for key, value in changes.items():
            setattr(case, key, value)
        if semantic_changed and case.status == CaseStatus.ACTIVE.value:
            # why：审核结论绑定的是"那一版内容"——内容变了，批准人签名随内容失效（reviewer 清空留痕于 updated_at/日志）
            case.status = CaseStatus.DRAFT.value
            case.reviewer = None
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
        self.session.refresh(case)
        return case

    def confirm_case(self, case_id: int, reviewer: str) -> TestCase:
        """防幻觉护栏：仅 draft→active；已 active 幂等返回；禁止任何自动化路径直接置 active。"""
        case = self.repo.get_or_raise(case_id)
        if case.status == CaseStatus.ACTIVE:
            return case
        if case.status != CaseStatus.DRAFT:
            raise AppError(
                "INVALID_CONFIRM", status_code=409, detail=f"仅 draft 可确认，当前 {case.status}"
            )
        self._revalidate_for_active(case)
        case.status = CaseStatus.ACTIVE
        case.reviewer = reviewer  # 批准人落库（责任可审计）
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
        self.session.refresh(case)
        return case

    def _validate_merged(self, merged: dict) -> None:
        """形状闸：合并后的记录必须能通过入参 schema（update 的唯一校验入口）。

        why：与 _revalidate_for_active 同一把尺子，但触发点更早——错在写入前，而不是等激活/执行时才发现。
        代价：若历史行被 out-of-band 写坏，任何不含该字段的更新都会被拒（422 detail 会点名坏字段，
        补上正确值即可修复）——这是有意的取舍：宁可拒绝一次更新，也不让坏数据继续流通。
        """
        try:
            CaseCreate(**merged)
        except ValidationError as e:
            fields = sorted({str(err["loc"][0]) for err in e.errors() if err.get("loc")})
            raise AppError(
                "CASE_INVALID",
                status_code=422,
                detail=f"更新后用例校验未通过，请修正字段 {fields}: {e}",
            ) from e

    def _revalidate_for_active(self, case: TestCase) -> None:
        """转 active 前重新校验：形状 + 语义。不通过则拒绝激活。

        why：draft 期间数据可能被改坏或来自未校验的历史写入——激活是进入执行池的最后一道闸，
        校验失败必须拦在状态迁移前（否则坏用例获得执行资格）。
        why 两把尺子都要：形状校验保证「结构合法」，语义校验保证「属于这个接口」。
        只有前者时，`method=DELETE, path=/users/1, operation_id=getUser` 能合法进执行池。
        """
        try:
            CaseCreate(
                name=case.name,
                method=case.method,  # type: ignore[arg-type]
                path=case.path,
                operation_id=case.operation_id,
                params=case.params,
                body=case.body,
                expected_status=case.expected_status,
                assertions=case.assertions,
            )
        except ValidationError as e:
            raise AppError(
                "CASE_INVALID_FOR_ACTIVE", status_code=422, detail=f"激活前校验未通过: {e}"
            ) from e
        self._validate_against_contract(case)

    def _validate_against_contract(self, case: TestCase) -> None:
        """用 operation 契约快照反向核对用例属于该接口。

        降级口径（三条都不阻断激活，只记日志）：
        - 没有契约快照（用例从未经 /parse 入库，或库里只有旧快照）→ 无从判定；
        - 快照缺 method/path（旧版本落库的数据）→ 同上；
        - 违规全是 warn 级（期望码未声明、断言字段不可判）→ 只影响质量评分，不影响可用性。

        why 不阻断：语义校验在生成链路已经拦过一遍，confirm 是第二道；把「文档根本没声明
        这个接口」也算成用例的错，会让手工用例全部无法激活。只有能明确判定「打错接口」的
        error 级违规才拒绝。
        """
        snapshot = self.api_defs.get_latest()
        if snapshot is None:
            logger.info("用例 %s 激活前跳过语义校验：库中无契约快照", case.id)
            return
        contract = contract_from_snapshot(
            (snapshot.operation_contracts or {}).get(case.operation_id) or {}
        )
        if contract is None:
            # why：operation_id 不在最新快照里有两种可能——文档已移除该接口，或该 op 是
            # 手工建的。两种情况都无从判定，不能据此拒绝激活（否则文档一迭代就锁死旧用例）
            logger.info(
                "用例 %s 激活前跳过语义校验：快照中无 operation_id=%s 的可用契约",
                case.id,
                case.operation_id,
            )
            return
        violations = validate_case(case, contract)
        errors = [v for v in violations if v.level == "error"]
        if errors:
            raise AppError(
                "CASE_CONTRACT_MISMATCH",
                status_code=422,
                detail=(
                    "用例与接口契约不符，拒绝激活："
                    + "; ".join(v.detail for v in errors)
                    + f"（operation_id={case.operation_id}，契约来自快照 {snapshot.version}）"
                ),
            )

    def delete_case(self, case_id: int) -> None:
        self.repo.delete(self.repo.get_or_raise(case_id))

    def list_cases(self, *, page: int, page_size: int, **filters) -> tuple[list[TestCase], int]:
        return self.repo.list_by_filters(page=page, page_size=page_size, **filters)
