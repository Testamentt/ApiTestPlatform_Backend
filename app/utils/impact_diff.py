# diff 纯函数。why：集合差 + 分段 hash 比较 + breaking 联合判定，无 Session 便于单测；
# suggested_remap 只做相似名建议（difflib 粗筛），不自动重绑（保守防误绑）。
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DiffResult:
    added: list[str]
    removed: list[str]
    changed: list[str]
    breaking_changed: list[str]
    non_breaking_changed: list[str]
    identical: bool


def diff_operations(
    old_ids,
    old_hashes: dict,
    old_contracts: dict,
    new_ids,
    new_hashes: dict,
    new_contracts: dict,
    *,
    hash_version_equal: bool = True,
) -> DiffResult:
    """O(1) diff：集合差 + 哈希比较。hash_version 不一致时公共接口保守全标 changed（算法升级不重建旧快照）。"""
    old_set, new_set = set(old_ids), set(new_ids)
    removed = sorted(old_set - new_set)
    added = sorted(new_set - old_set)
    common = old_set & new_set
    if not hash_version_equal:
        changed = sorted(common)
    else:
        changed = sorted(op for op in common if old_hashes[op] != new_hashes[op])
    breaking = [op for op in changed if _is_breaking(old_contracts.get(op), new_contracts.get(op))]
    breaking_changed = sorted(breaking)
    non_breaking_changed = sorted(op for op in changed if op not in breaking_changed)
    identical = not added and not removed and not changed
    return DiffResult(added, removed, changed, breaking_changed, non_breaking_changed, identical)


def _is_breaking(old_c, new_c) -> bool:
    """breaking 联合判定（F2）：required 收紧 / type 变化 / 枚举删减 / 字段删除 / 响应状态码集合变化——
    任一命中即破坏性。"""
    if old_c is None or new_c is None:
        return True  # 契约缺失（如从无 body 变为有 body）保守视为 breaking
    if set(new_c.get("required", [])) - set(old_c.get("required", [])):
        return True  # ① required 收紧（false→true 或新增必需项）
    if set(old_c.get("response_status_codes", [])) - set(new_c.get("response_status_codes", [])):
        return True  # ⑤ 响应状态码删减/替换（200→202：旧码不再返回，下游按旧码断言失败，F1）；新增不破坏
    old_sig = old_c.get("signature", {})
    new_sig = new_c.get("signature", {})
    for field in old_sig.keys() & new_sig.keys():
        if old_sig[field].get("type") != new_sig[field].get("type"):
            return True  # ② type 变化（string→integer，下游 len(str)→int 异常）
        if set(old_sig[field].get("enum", [])) - set(new_sig[field].get("enum", [])):
            return True  # ③ 枚举删减（下游只认 "active"，收到 "pending" 崩溃）
    # ④ 字段被删除（断言 KeyError）——直接返回差值布尔
    return bool(set(old_sig.keys()) - set(new_sig.keys()))


def build_suggested_remap(removed_ops: list[str], added_ops: list[str]) -> dict:
    """removed→added 相似名配对。why：只做建议不自动重绑——Levenshtein 粗筛，避免「相似但不同」误绑（追问 2）。"""
    if not removed_ops or not added_ops:
        return {}
    remap: dict[str, str] = {}
    for r in removed_ops:
        best, best_score = None, 0.0
        for a in added_ops:
            score = difflib.SequenceMatcher(None, _norm_op(r), _norm_op(a)).ratio()
            if score > best_score:
                best, best_score = a, score
        if best is not None and best_score >= 0.6:
            remap[r] = best
    return remap


def _norm_op(op: str) -> str:
    """规范化：小写去非字母数字——listUsers 与 getUsers 可比。"""
    return re.sub(r"[^a-z0-9]", "", op.lower())
