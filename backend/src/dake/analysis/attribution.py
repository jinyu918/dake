"""正式增量归属、多标签分类、状态与优先级提案合约。"""

import hashlib
import json
from typing import Literal

from pydantic import Field

from ..domain.models import parse_time
from .continuity import ContinuityFact, normalize_uncertain_reference, recovery_is_safe, validate_continuity
from .contract import ContractError, EvidencePriorityProposal, ExtractedCandidate, StrictObject, parse_output, strict_schema
from .prompts import render_attribution_policy
from .quote_alignment import align_quotes
from .summary_changes import SummaryChange, complete_field_changes, validate_changes
from .validation import evidence_map

# 归属专属提示词统一从模块化 Markdown 分块加载，不再在 Python 代码中维护长段规则文本。
ATTRIBUTION_POLICY = render_attribution_policy()


class AttributionReference(StrictObject):
    """关联依据的原文引用；由程序补足版本、群和时间。"""

    message_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class AttributedCandidate(ExtractedCandidate):
    """正式四类决定；继承多标签、专属属性、状态提案与优先级信号结构。"""

    decision: Literal["new", "update", "duplicate", "review"]
    basis: list[AttributionReference]
    possible_event_ids: list[str]
    changes: list[SummaryChange]
    continuity: ContinuityFact | None


class AttributionOutput(StrictObject):
    """归属输出合约版本。"""

    schema_version: Literal["4.0"]
    candidates: list[AttributedCandidate]


class EvidenceAttributedCandidate(AttributedCandidate):
    priority_proposal: EvidencePriorityProposal | None = None


class EvidenceAttributionOutput(AttributionOutput):
    candidates: list[EvidenceAttributedCandidate]


def attribution_version(*, priority_preview: bool = False) -> str:
    """冻结执行策略及严格 Schema，用于任务与本地审计。"""
    model = EvidenceAttributionOutput if priority_preview else AttributionOutput
    policy = render_attribution_policy(priority_preview=True) if priority_preview else ATTRIBUTION_POLICY
    schema = strict_schema(model.model_json_schema())
    value = policy + json.dumps(schema, sort_keys=True)
    return "incremental-attribution-v1-" + hashlib.sha256(value.encode()).hexdigest()[:16]


def _find_exact_or_ws_quote(quote: str, body: str) -> str | None:
    """在单条消息正文中查找精确子串或唯一空白无关子串；短词不跨消息猜测。"""
    if not isinstance(quote, str) or not quote.strip():
        return None
    if quote in body:
        return quote
    needle = "".join(ch for ch in quote if not ch.isspace())
    if not needle:
        return None
    positions = [idx for idx, ch in enumerate(body) if not ch.isspace()]
    compact = "".join(body[idx] for idx in positions)
    matches = []
    start = compact.find(needle)
    while start >= 0:
        matches.append(body[positions[start] : positions[start + len(needle) - 1] + 1])
        start = compact.find(needle, start + 1)
    return matches[0] if len(matches) == 1 else None


def _unique_quote_owner(quote: str, pool: list[str], msg_bodies: dict[str, str]) -> tuple[str, str] | None:
    """仅当非短词引文在候选池中唯一命中某一条消息时，返回其真实消息键与对齐引文。"""
    if not isinstance(quote, str) or len("".join(ch for ch in quote if not ch.isspace())) < 4:
        return None
    hits: list[tuple[str, str]] = []
    for key in dict.fromkeys(pool):
        if not isinstance(key, str) or key not in msg_bodies:
            continue
        matched = _find_exact_or_ws_quote(quote, msg_bodies[key])
        if matched is not None:
            hits.append((key, matched))
            if len(hits) > 1:
                return None
    return hits[0] if len(hits) == 1 else None


def _pre_normalize_attribution_json(
    content: str,
    new_messages,
    context,
    history_items,
    normalizations: list[dict] | None = None,
) -> str:
    """对正式 JSON 输出执行严格保真的机械格式容错（邻近消息错号唯一对齐、新证据补入与旧上下文剥离）。"""
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return content
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        return content

    all_msgs = [*context, *new_messages]
    msg_bodies = {m.key: m.content for m in all_msgs}
    new_key_list = [m.key for m in new_messages]
    new_keys = set(new_key_list)
    ctx_keys = {m.key for m in context} - new_keys
    all_key_list = [m.key for m in all_msgs]
    hist_bodies = {
        item["id"]: [frag.get("text", "") for frag in item.get("fragments", [])]
        for item in (history_items or ())
        if isinstance(item, dict) and item.get("id")
    }

    changed_any = False
    for ci, cand in enumerate(payload["candidates"]):
        if not isinstance(cand, dict):
            continue
        raw_eids = cand.get("evidence_ids")
        if not isinstance(raw_eids, list) or not all(isinstance(k, str) for k in raw_eids):
            continue
        dedup_eids = list(dict.fromkeys(raw_eids))
        if len(dedup_eids) != len(raw_eids):
            cand["evidence_ids"] = dedup_eids
            changed_any = True

        def _ensure_in_top_evidence(key: str) -> None:
            nonlocal changed_any
            if key in new_keys and key not in cand["evidence_ids"]:
                cand["evidence_ids"].append(key)
                changed_any = True

        def _record_norm(field: str, reason: str, **extra) -> None:
            if normalizations is not None:
                normalizations.append(dict(candidate_index=ci, field=field, reason=reason, **extra))

        cand_new_pool = lambda: [k for k in cand["evidence_ids"] if k in new_keys]
        decision = cand.get("decision")

        # 1. 处理 changes：correction 漏填 replaces 归为 progress，修正邻近消息错号并补入顶层 evidence_ids
        if isinstance(cand.get("changes"), list):
            for ch_idx, change in enumerate(cand["changes"]):
                if not isinstance(change, dict):
                    continue
                if change.get("kind") == "correction" and change.get("replaces") is None:
                    change["kind"] = "progress"
                    changed_any = True
                    _record_norm(f"changes[{ch_idx}].kind", "correction_without_replaces_to_progress")
                ev_list = change.get("evidence")
                if not isinstance(ev_list, list):
                    continue
                for ref_idx, ref in enumerate(ev_list):
                    if not isinstance(ref, dict):
                        continue
                    rk, rq = ref.get("message_id"), ref.get("quote")
                    if isinstance(rk, str) and rk in msg_bodies and _find_exact_or_ws_quote(rq, msg_bodies[rk]):
                        _ensure_in_top_evidence(rk)
                        continue
                    resolved = _unique_quote_owner(rq, cand_new_pool(), msg_bodies) or _unique_quote_owner(
                        rq, new_key_list, msg_bodies
                    )
                    if resolved is not None:
                        ref["message_id"], ref["quote"] = resolved
                        _ensure_in_top_evidence(resolved[0])
                        changed_any = True
                        _record_norm(f"changes[{ch_idx}].evidence[{ref_idx}]", "unique_quote_message_aligned")

        # 2. 处理多键证据提案与字段：唯一对齐邻近错号，并将确认属于 new_messages 的引用补入顶层 evidence_ids
        current_only_lists = ("typed_attributes", "attributes", "extensions", "assignees", "removed_types")
        for list_name in current_only_lists:
            items = cand.get(list_name)
            if not isinstance(items, list):
                continue
            for idx, entry in enumerate(items):
                if not isinstance(entry, dict) or not isinstance(entry.get("evidence_ids"), list):
                    continue
                eq = entry.get("quote")
                valid_keys = [
                    k for k in entry["evidence_ids"]
                    if isinstance(k, str) and k in msg_bodies and _find_exact_or_ws_quote(eq, msg_bodies[k])
                ]
                if valid_keys:
                    for vk in valid_keys:
                        _ensure_in_top_evidence(vk)
                    continue
                resolved = _unique_quote_owner(eq, cand_new_pool(), msg_bodies) or _unique_quote_owner(
                    eq, new_key_list, msg_bodies
                )
                if resolved is not None:
                    entry["evidence_ids"], entry["quote"] = [resolved[0]], resolved[1]
                    _ensure_in_top_evidence(resolved[0])
                    changed_any = True
                    _record_norm(f"{list_name}[{idx}]", "unique_quote_message_aligned")

        for single_name in ("status_proposal", "priority_proposal"):
            prop = cand.get(single_name)
            if not isinstance(prop, dict) or not isinstance(prop.get("evidence_ids"), list):
                continue
            pq = prop.get("quote")
            valid_keys = [
                k for k in prop["evidence_ids"]
                if isinstance(k, str) and k in msg_bodies and _find_exact_or_ws_quote(pq, msg_bodies[k])
            ]
            if valid_keys:
                for vk in valid_keys:
                    _ensure_in_top_evidence(vk)
                continue
            resolved = _unique_quote_owner(pq, cand_new_pool(), msg_bodies) or _unique_quote_owner(
                pq, new_key_list, msg_bodies
            )
            if resolved is not None:
                prop["evidence_ids"], prop["quote"] = [resolved[0]], resolved[1]
                _ensure_in_top_evidence(resolved[0])
                changed_any = True
                _record_norm(single_name, "unique_quote_message_aligned")

        allow_ctx_for_cls = decision in {"update", "duplicate"}
        cls_fallback_pool = all_key_list if allow_ctx_for_cls else new_key_list
        if isinstance(cand.get("type_labels"), list):
            for idx, lbl in enumerate(cand["type_labels"]):
                if not isinstance(lbl, dict) or not isinstance(lbl.get("evidence_ids"), list):
                    continue
                lq = lbl.get("quote")
                valid_keys = [
                    k for k in lbl["evidence_ids"]
                    if isinstance(k, str) and k in msg_bodies and _find_exact_or_ws_quote(lq, msg_bodies[k])
                ]
                if valid_keys:
                    for vk in valid_keys:
                        _ensure_in_top_evidence(vk)
                    continue
                resolved = _unique_quote_owner(lq, cand["evidence_ids"], msg_bodies) or _unique_quote_owner(
                    lq, cls_fallback_pool, msg_bodies
                )
                if resolved is not None:
                    lbl["evidence_ids"], lbl["quote"] = [resolved[0]], resolved[1]
                    _ensure_in_top_evidence(resolved[0])
                    changed_any = True
                    _record_norm(f"type_labels[{idx}]", "unique_quote_message_aligned")

        other_rsn = cand.get("other_reason")
        if isinstance(other_rsn, dict) and isinstance(other_rsn.get("evidence_ids"), list):
            oq = other_rsn.get("quote")
            valid_keys = [
                k for k in other_rsn["evidence_ids"]
                if isinstance(k, str) and k in msg_bodies and _find_exact_or_ws_quote(oq, msg_bodies[k])
            ]
            if valid_keys:
                for vk in valid_keys:
                    _ensure_in_top_evidence(vk)
            else:
                resolved = _unique_quote_owner(oq, cand["evidence_ids"], msg_bodies) or _unique_quote_owner(
                    oq, cls_fallback_pool, msg_bodies
                )
                if resolved is not None:
                    other_rsn["evidence_ids"], other_rsn["quote"] = [resolved[0]], resolved[1]
                    _ensure_in_top_evidence(resolved[0])
                    changed_any = True
                    _record_norm("other_reason", "unique_quote_message_aligned")

        # 3. 处理 basis 中已知消息的邻近错号（未知历史 ID 保持原样交由下游核验）
        if isinstance(cand.get("basis"), list):
            for b_idx, bref in enumerate(cand["basis"]):
                if not isinstance(bref, dict):
                    continue
                bk, bq = bref.get("message_id"), bref.get("quote")
                if not isinstance(bk, str) or not isinstance(bq, str):
                    continue
                in_avail_ok = bk in msg_bodies and _find_exact_or_ws_quote(bq, msg_bodies[bk]) is not None
                in_hist_ok = bk in hist_bodies and any(bq in frag for frag in hist_bodies[bk])
                if in_avail_ok or in_hist_ok or (bk not in msg_bodies and bk not in hist_bodies):
                    continue
                resolved = _unique_quote_owner(bq, all_key_list, msg_bodies)
                if resolved is not None:
                    bref["message_id"], bref["quote"] = resolved
                    changed_any = True
                    _record_norm(f"basis[{b_idx}]", "unique_quote_message_aligned")

        # 4. 当候选已包含至少 1 条合法 new_messages 时，剥离误混入顶层 evidence_ids 的旧上下文 ID 与未变化旧字段
        has_real_new = any(k in new_keys for k in cand["evidence_ids"])
        if has_real_new:
            for list_name in ("typed_attributes", "attributes", "extensions", "assignees"):
                items = cand.get(list_name)
                if isinstance(items, list):
                    kept_items = [
                        entry for entry in items
                        if not isinstance(entry, dict)
                        or not isinstance(entry.get("evidence_ids"), list)
                        or any(k in new_keys for k in entry["evidence_ids"])
                    ]
                    if len(kept_items) != len(items):
                        cand[list_name] = kept_items
                        changed_any = True
                        _record_norm(list_name, "context_only_unchanged_fields_stripped")
            for single_name in ("status_proposal", "priority_proposal"):
                prop = cand.get(single_name)
                if isinstance(prop, dict) and isinstance(prop.get("evidence_ids"), list):
                    if prop["evidence_ids"] and not any(k in new_keys for k in prop["evidence_ids"]):
                        cand[single_name] = None
                        changed_any = True
                        _record_norm(single_name, "context_only_unchanged_proposal_stripped")
            mixed_ctx = [k for k in cand["evidence_ids"] if k in ctx_keys]
            if mixed_ctx and decision in {"new", "update", "duplicate"}:
                cand["evidence_ids"] = [k for k in cand["evidence_ids"] if k not in ctx_keys]
                changed_any = True
                _record_norm("evidence_ids", "mixed_context_ids_stripped", stripped=mixed_ctx)

    return json.dumps(payload, ensure_ascii=False) if changed_any else content


def parse_attribution_output(
    content,
    new_messages,
    context,
    existing_events,
    *,
    normalizations=None,
    history_items=(),
    require_type_labels: bool = False,
    output_model=AttributionOutput,
):
    """沿用字段及来源校验；关联依据在业务入口按当前窗口再次核对。"""
    content = align_quotes(content, [*context, *new_messages])
    content = _pre_normalize_attribution_json(
        content, new_messages, context, history_items, normalizations=normalizations
    )
    available = evidence_map([*context, *new_messages])
    try:
        candidates = parse_output(
            content,
            new_messages,
            context,
            existing_events,
            output_model=output_model,
            require_type_labels=require_type_labels,
        )
    except ContractError as error:
        try:
            output = output_model.model_validate_json(content)
        except ValueError:
            raise error
        errors = [str(error)]
        for ci, item in enumerate(output.candidates):
            for bi, ref in enumerate(item.basis):
                try:
                    _basis_message(ref.model_dump(), available)
                except ContractError:
                    errors.append(f"candidates[{ci}].basis[{bi}]：归属引文不是该消息的连续原文")
        raise ContractError("；".join(errors)) from None
    for candidate_index, candidate in enumerate(candidates):
        new_keys = {m.key for m in new_messages}
        completed = complete_field_changes(candidate, available, new_keys)
        if candidate.decision in {"duplicate", "review"} and candidate.changes:
            completed.append(dict(
                field="changes",
                reason="non_writing_action_proposals_only",
                decision=candidate.decision,
                count=len(candidate.changes),
            ))
        completed += normalize_uncertain_reference(candidate, existing_events)
        completed += normalize_review_candidates(candidate, existing_events)
        completed += normalize_review_basis(candidate, available, history_items)
        completed += complete_basis_from_changes(candidate, available, new_keys)
        completed += normalize_redundant_basis(
            candidate, available, history_items, existing_events, new_keys
        )
        if normalizations is not None:
            normalizations.extend(dict(candidate_index=candidate_index, **item) for item in completed)
        try:
            validate_changes(candidate, available, {m.key for m in new_messages})
        except ContractError as error:
            raise ContractError(f"candidates[{candidate_index}]：{error}") from None
        validate_continuity(candidate, available, {m.key for m in new_messages})
        for basis_index, ref in enumerate(candidate.basis):
            try:
                _basis_message(ref, available)
            except ContractError as error:
                raise ContractError(
                    f"{error}（位置 candidates[{candidate_index}].basis[{basis_index}]）。"
                    "请核对该项 message_id 与 quote：quote 必须是该编号消息的连续原文，"
                    "不能借用其他消息的正文。"
                ) from None
    return candidates


def complete_basis_from_changes(candidate, available, new_keys):
    """当模型已在 changes 中给出经逐字核验的本轮新消息引文、仅漏抄入 basis 时，补足本轮依据避免误降级为 review。"""
    if candidate.decision not in {"new", "update"} or candidate.continuity is not None or candidate.ambiguous:
        return []
    allowed_ids = set(candidate.evidence_ids)
    has_current = any(
        ref.get("message_id") in new_keys
        and ref.get("message_id") in allowed_ids
        and ref.get("message_id") in available
        and isinstance(ref.get("quote"), str)
        and ref["quote"].strip()
        and ref["quote"] in available[ref["message_id"]].content
        for ref in candidate.basis
    )
    if has_current:
        return []
    if candidate.decision == "update" and not any(ref.get("message_id") not in new_keys for ref in candidate.basis):
        return []

    added = []
    seen = {(ref.get("message_id"), ref.get("quote")) for ref in candidate.basis}
    for change in candidate.changes or []:
        for ref in change.get("evidence", []):
            key, quote = ref.get("message_id"), ref.get("quote")
            if (
                key in new_keys
                and key in allowed_ids
                and key in available
                and isinstance(quote, str)
                and quote.strip()
                and quote in available[key].content
                and (key, quote) not in seen
            ):
                added.append(dict(message_id=key, quote=quote))
                seen.add((key, quote))
    if not added:
        return []
    candidate.basis = [*candidate.basis, *added]
    return [dict(field="basis", reason="verified_change_quote_completed_basis", count=len(added))]


def normalize_review_basis(candidate, available, history_items):
    """待确认不执行事件写入，已知消息的无效引文记为未采用，不阻断其余事项。"""
    if candidate.decision != "review":
        return []
    kept, excluded = [], []
    for ref in candidate.basis:
        key, quote = ref["message_id"], ref["quote"]
        message = available.get(key)
        items = [item for item in history_items if item["id"] == key]
        bodies = [message.content] if message is not None else [
            fragment["text"] for item in items for fragment in item.get("fragments", [])
        ]
        if (message is not None or items) and not any(quote in body for body in bodies):
            excluded.append(dict(ref))
        else:
            kept.append(ref)
    if not excluded:
        return []
    candidate.basis = kept
    return [dict(field="basis", reason="review_invalid_quotes_not_adopted", excluded=excluded)]


def normalize_redundant_basis(candidate, available, history_items, existing_events, new_keys):
    """已有完整新旧依据时排除多余错引；不补造依据，不处理恢复边界。"""
    if candidate.decision not in {"new", "update", "duplicate"} or candidate.continuity is not None:
        return []
    target = next((e for e in existing_events if e.event_id == candidate.referenced_event_id), None)
    kept, excluded = [], []
    current_proof = old_proof = False
    for ref in candidate.basis:
        key, quote = ref["message_id"], ref["quote"]
        message = available.get(key)
        items = [item for item in history_items if item["id"] == key]
        bodies = [message.content] if message is not None else [
            fragment["text"] for item in items for fragment in item.get("fragments", [])
        ]
        valid = bool(quote.strip()) and any(quote in body for body in bodies)
        if (message is not None or items) and not valid:
            excluded.append(dict(ref))
            continue
        kept.append(ref)
        if not valid:
            continue
        current_proof |= key in new_keys and key in candidate.evidence_ids
        if target is not None and key not in new_keys:
            if message is not None:
                old_proof |= any(
                    (e["source"], e["message_id"], e.get("digest")) == (message.source, message.message_id, message.digest)
                    for e in target.evidence
                )
                old_proof |= any(
                    item.get("digest") == message.digest
                    and any(link["event_id"] == target.event_id and link["kind"] == "event_evidence" for link in item["links"])
                    for item in items
                )
            else:
                old_proof |= any(
                    any(link["event_id"] == target.event_id and link["kind"] == "event_evidence" for link in item["links"])
                    for item in items
                )
    if not excluded or not current_proof or (candidate.decision != "new" and not old_proof):
        return []
    candidate.basis = kept
    return [dict(field="basis", reason="redundant_invalid_quotes_not_adopted", excluded=excluded)]


def normalize_review_candidates(candidate, existing_events):
    """待确认备选只保留本次输入的编号，未知编号留审计，不猜替代事件。"""
    if (
        candidate.decision != "review"
        or candidate.referenced_event_id is not None
        or (
            candidate.continuity is not None
            and (
                candidate.continuity.get("kind") != "uncertain"
                or candidate.continuity.get("previous_event_id") is not None
            )
        )
    ):
        return []
    visible = {e.event_id for e in existing_events}
    unknown = list(dict.fromkeys(key for key in candidate.possible_event_ids if key not in visible))
    if not unknown:
        return []
    candidate.possible_event_ids = list(dict.fromkeys(key for key in candidate.possible_event_ids if key in visible))
    return [dict(field="possible_event_ids", reason="unprovided_review_candidates_excluded", excluded_event_ids=unknown)]


def _basis_message(ref, available):
    """共用原文引文检查；不改写、猜测或放宽引文，未知历史消息延后核对。"""
    key, quote = ref.get("message_id"), ref.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        raise ContractError("归属依据必须包含可核对的引文")
    message = available.get(key)
    if message is not None and (message.key != key or quote not in message.content):
        raise ContractError("归属依据的消息或引文无效")
    return message


def message_reference(message):
    """保存消息定位与版本，不把整个聊天正文复制到归属记录。"""
    return dict(
        message_id=message.key,
        source=message.source,
        id=message.message_id,
        digest=message.digest,
        conversation_id=message.conversation_id,
        send_time=message.sent_at.isoformat(),
    )


def decide(
    candidate,
    *,
    visible_events,
    available,
    new_keys,
    eligible_keys,
    background,
    uid,
    stream,
    before,
    closed_statuses,
    priority_preview=False,
):
    """核对明确目标和原文依据；不足则保留待确认，伪造引用则让整批回滚。"""
    action = candidate.decision
    if action not in {"new", "update", "duplicate", "review"} or not candidate.decision_reason.strip():
        raise ContractError("归属决定及依据不能为空")
    if any(e.uid != uid or e.stream != stream for e in visible_events):
        raise ContractError("归属候选必须属于同一客户和分析范围")
    visible = {e.event_id: e for e in visible_events}
    possible = list(dict.fromkeys(candidate.possible_event_ids))
    if any(key not in visible for key in possible):
        raise ContractError("待确认只能引用本次提供的历史候选")
    if action not in {"update", "duplicate"} and candidate.referenced_event_id is not None:
        raise ContractError("新建和待确认不能指定更新目标")
    recurrence_id = (candidate.continuity or {}).get("previous_event_id")
    old = visible.get(candidate.referenced_event_id or recurrence_id)
    if action in {"update", "duplicate"} or recurrence_id:
        if old is None:
            raise ContractError("归属目标必须是本次提供的已有事件")
        if recurrence_id:
            old_labels = (
                {old.type}
                if len(old.types) <= 1 and old.type != "待分类"
                else set(old.types)
            )
            new_labels = set(candidate.label_types())
            if old_labels and new_labels and not old_labels.intersection(new_labels):
                raise ContractError("复发前次目标须与当前事项具有相同类型维度")
    if any(
        e.end_time >= before
        or e.created_on >= before
        or not recovery_is_safe(e, before, require_evidence=False)
        or any(parse_time(r["window_end"]) > before for r in e.updated_by)
        for e in visible_events
    ):
        raise ContractError("归属不能读取未来事件")
    if background is not None and (background.uid != uid or background.stream != stream or background.before != before):
        raise ContractError("归属历史背景范围不一致")
    historical = background.items if background else []
    basis, current_proof, old_proof = [], False, False
    for ref in candidate.basis:
        key, quote = ref.get("message_id"), ref.get("quote")
        message = _basis_message(ref, available)
        versions = [
            item
            for item in historical
            if item["id"] == key and (old is None or any(link["event_id"] == old.event_id for link in item["links"]))
        ]
        if len(versions) > 1:
            raise ContractError("归属依据存在多个历史版本，不能仅按消息 ID 选择正文")
        item = versions[0] if versions else None
        if message is not None:
            if message.uid != uid or message.sent_at >= before:
                raise ContractError("归属依据的消息或引文无效")
            value = message_reference(message)
            current_proof |= key in eligible_keys and key in candidate.evidence_ids
            linked = old is not None and key not in new_keys and any(
                (e["source"], e["message_id"], e.get("digest")) == (message.source, message.message_id, message.digest)
                for e in old.evidence
            )
            if old is not None and key not in new_keys and item is not None:
                linked |= item.get("digest") == message.digest and any(
                    link["event_id"] == old.event_id and link["kind"] == "event_evidence"
                    for link in item["links"]
                )
        elif item is not None:
            if parse_time(item["send_time"]) >= before or not any(
                quote in fragment["text"] for fragment in item.get("fragments", [])
            ):
                raise ContractError("归属依据不在实际提供的历史片段中")
            value = dict(
                message_id=key,
                source=item["source"],
                id=item["message_id"],
                digest=item.get("digest"),
                conversation_id=item.get("conversation_id"),
                send_time=item["send_time"],
                availability=item["availability"],
            )
            linked = old is not None and any(
                link["event_id"] == old.event_id and link["kind"] == "event_evidence"
                for link in item["links"]
            )
        else:
            raise ContractError("归属依据引用了输入中不存在的消息")
        value.update(quote=quote, role="new" if key in new_keys else "history")
        basis.append(value)
        old_proof |= linked
    reason = candidate.decision_reason
    if action != "review" and (candidate.ambiguous or not current_proof or not candidate.topic_key):
        action, reason = "review", "本轮依据或主题不足，不能确定归属"
    if action == "new" and possible:
        action, reason = "review", "仍有可能属于的旧事件，不能直接新建"
    if (action in {"update", "duplicate"} or (action == "new" and recurrence_id)) and (not old_proof or possible):
        action, reason = "review", "旧事件依据不足或存在其他可能事件"
    if action == "review" and old is not None:
        possible = list(dict.fromkeys([old.event_id, *possible]))
    evidence = [
        message_reference(available[key])
        for key in dict.fromkeys(candidate.evidence_ids)
        if available[key].key in new_keys
    ]
    return dict(
        action=action,
        event_id=old.event_id if old and action in {"update", "duplicate"} else None,
        reason=reason,
        possible_event_ids=possible,
        basis=basis,
        new_evidence=evidence,
        policy_version=attribution_version(priority_preview=priority_preview),
    )
