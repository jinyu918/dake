"""基于本轮事实局部更新摘要，并准备可追溯的变化记录。"""

from copy import deepcopy
from dataclasses import replace
from typing import Literal

from pydantic import Field

from ..domain.models import parse_time
from .contract import ContractError, StrictObject


SUMMARY_POLICY = """
七、摘要与变化历史（替代前述输出版本和摘要整段覆盖约定）
schema_version 固定为 "4.0"。每个候选增加 changes，说明本轮新增事实及其直接证据。
changes 每项为 {kind,text,evidence:[{message_id,quote}],replaces}。
kind=progress：新的排查结果、动作及效果、影响、负责人或承诺变化；
kind=confirmation：新的测试或反馈再次确认现状，例如今天再试仍失败；记录进展但摘要可以不变；
kind=correction：有明确本轮依据纠正旧结论，保留历史，不把猜测当成确认。
text 是一条简短事实，保留未确认、怀疑、请求等限定词；evidence 引用本批直接证据的连续原文，
至少有本轮可分析的新讨论支持，不可把历史背景引文写作新增事实依据。
new 必须有初始事实，summary 概括初始问题。duplicate/review 的 changes=[]。
update 的 summary 不能用于整体覆盖旧摘要；程序只采用有依据的 changes 局部更新。
新增事实通常 replaces=null，程序追加简短事实，保留原问题、重要进展和未解决事项。
确有依据更新摘要中已经过时或错误的片段时，replaces 必须逐字复制那个旧片段，
由 text 替换它；不得为润色删除未解决事项或把局部新反馈替换成整段摘要。
confirmation 的 replaces=null，不仅因为新消息时间较晚就重写摘要。
没有新增事实、仅重述或换措辞时 changes=[]，不能编造 progress。已有原归属时应选 duplicate。
同一事件同批的全部有效进展都列入 changes，不能只留下最后一条。字段变化也必须由其中的依据支持。
迟到旧陈述只补证据，不能覆盖较新结论；有明确新纠正时引用本轮纠正原文，保存被纠正的片段。
"""


class ChangeReference(StrictObject):
    """变化所依据的本批原文，定位信息和版本由程序填写。"""

    message_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class SummaryChange(StrictObject):
    """少量事实与可选的旧摘要片段，不要求模型重写全部历史。"""

    kind: Literal["progress", "confirmation", "correction"]
    text: str = Field(min_length=1)
    evidence: list[ChangeReference] = Field(min_length=1)
    replaces: str | None


def complete_field_changes(candidate, available, eligible_keys):
    """字段已有本轮逐字依据时补原文事实，避免要求模型重复填写同一份依据。"""
    if candidate.decision not in {"new", "update"} or not candidate.changes:
        return []
    extracted = candidate.extensions.get("extraction_v1", {})
    facts = [(name, extracted.get(name)) for name in ("status", "severity")]
    facts += [(f"{name}[{index}]", fact) for name in ("assignees", "attributes", "extensions")
              for index, fact in enumerate(extracted.get(name, []))]
    facts += [(f"typed_attributes[{fact['type_label']}][{fact['name']}]", fact)
              for fact in extracted.get("typed_attributes", [])]
    normalized = []
    for path, fact in facts:
        if not fact:
            continue
        covered = {ref["message_id"] for change in candidate.changes for ref in change["evidence"]}
        keys, quote = set(fact["evidence_ids"]), fact["quote"]
        if keys <= covered:
            continue
        # 只补全部属于本轮可分析消息且逐条包含原文的证明，不扩充旧背景或机器人证据。
        if (not keys or not keys <= eligible_keys or not keys <= set(candidate.evidence_ids)
                or not quote.strip() or fact["value"] not in quote
                or any(key not in available or quote not in available[key].content for key in keys)):
            continue
        candidate.changes.append(dict(kind="progress", text=quote, replaces=None,
            evidence=[dict(message_id=key, quote=quote) for key in dict.fromkeys(fact["evidence_ids"])]))
        normalized.append(dict(field=path, change_index=len(candidate.changes) - 1,
                               reason="verified_current_field_quote", evidence_ids=fact["evidence_ids"]))
    return normalized


def validate_changes(candidate, available, eligible_keys):
    """变更事实只引用本候选的新增直接证据；历史依据仍留在归属 basis。"""
    if candidate.changes is None:
        return
    if candidate.decision in {"duplicate", "review"}:
        # 非写入动作中的变化只保留为模型建议，不让多填的建议阻断整个周期。
        return
    if candidate.decision == "new" and not candidate.changes:
        raise ContractError("新建事件必须给出初始事实及依据")
    allowed = set(candidate.evidence_ids)
    for raw in candidate.changes:
        try:
            change = SummaryChange.model_validate(raw)
        except ValueError:
            raise ContractError("变化事实不符合合约") from None
        if not change.text.strip() or (change.replaces is not None and not change.replaces.strip()):
            raise ContractError("变化事实和替换片段不能是空白")
        if change.kind == "confirmation" and change.replaces is not None:
            raise ContractError("进展确认不能替换旧摘要")
        if change.kind == "correction" and change.replaces is None:
            raise ContractError("明确纠正须指出旧摘要中的被纠正片段")
        if candidate.decision == "new" and (change.replaces is not None or change.kind != "progress"):
            raise ContractError("新建只记录初始事实，不能替换不存在的旧摘要")
        for ref in change.evidence:
            message = available.get(ref.message_id)
            if (ref.message_id not in allowed or message is None or message.key != ref.message_id
                    or not ref.quote.strip() or ref.quote not in message.content):
                raise ContractError("变化事实的引文不能在本批直接证据中核对")
        if not any(ref.message_id in eligible_keys for ref in change.evidence):
            raise ContractError("每条变化必须由本轮可分析的新讨论支持")
    # 字段核对自身的本轮原文即可，不再要求在 changes 中重复声明。
    refs = allowed & eligible_keys
    extracted = candidate.extensions.get("extraction_v1", {})
    facts = [(name, extracted.get(name)) for name in ("status", "severity")]
    facts += [(f"{name}[{index}]", fact) for name in ("assignees", "attributes", "extensions")
              for index, fact in enumerate(extracted.get(name, []))]
    facts += [(f"typed_attributes[{fact['type_label']}][{fact['name']}]", fact)
              for fact in extracted.get("typed_attributes", [])]
    unsupported = [path for path, fact in facts
                   if fact and (not fact["evidence_ids"] or not set(fact["evidence_ids"]).issubset(refs)
                       or not any(key in available and fact["quote"] in available[key].content
                                  and (fact["value"] in fact["quote"] or (path.startswith("assignees[")
                                       and fact["value"] == available[key].sender_id))
                                  for key in fact["evidence_ids"]))]
    if unsupported:
        # 仅反馈协议位置，不回显字段值、消息身份或原文；一次指出全部缺口。
        raise ContractError("字段更新必须有本轮直接消息依据；不匹配位置："
            + ", ".join(unsupported) + "。这些字段引用了本轮可分析消息之外的依据。"
            "只提交本轮确有变化且有依据的字段；未变化或仅来自旧背景的字段不重复填写，"
            "status/severity 用 null，列表中移除相应项，旧值由程序保留。"
            "确有本轮新事实时补充对应 changes 及其逐字引文；不得编造事实来凑齐引用。")


def field_only_corrections(candidate, previous_attributes, summary) -> dict[int, set[str]]:
    corrections: dict[int, set[str]] = {}
    for label, attrs in candidate.typed_attributes.items():
        for name, fact in attrs.items():
            previous = previous_attributes.get(label, {}).get(name, {}).get("value")
            value, quote = fact["value"], fact["quote"]
            if not previous or previous == value or previous in summary:
                continue
            for index, change in enumerate(candidate.changes or []):
                if (change["kind"] == "correction" and previous in (change["replaces"] or "")
                        and value in change["text"] and any(
                            ref["message_id"] in fact["evidence_ids"]
                            and previous in ref["quote"] and value in ref["quote"]
                            and (quote in ref["quote"] or ref["quote"] in quote)
                            for ref in change["evidence"])):
                    corrections.setdefault(index, set()).add(previous)
    return corrections


def prepare_summary(candidate, event, available, *, historical=False, keep_facts=False,
                    field_corrections: dict[int, set[str]] | None = None):
    """应用有依据的局部修改；不同进展的替换冲突时失败，不按最后一条静默覆盖。"""
    base = event.summary if event is not None else candidate.summary
    field_change = event is not None and (any(
        getattr(candidate, name) is not None and getattr(candidate, name) != getattr(event, name)
        for name in ("status", "severity")) or
        bool(candidate.assignees and candidate.assignees != event.assignees) or
        any(event.attributes.get(key) != value for key, value in candidate.attributes.items()))
    field_change = field_change or (event is not None and any(
        event.typed_attributes.get(label, {}).get(name, {}).get("value") != fact["value"]
        for label, attrs in candidate.typed_attributes.items() for name, fact in attrs.items()))
    records, seen, replacements, additions = [], set(), [], []
    for index, raw in enumerate(candidate.changes):
        change = deepcopy(raw)
        evidence = []
        for ref in change["evidence"]:
            message = available[ref["message_id"]]
            value = dict(source=message.source, message_id=message.message_id, digest=message.digest,
                         conversation_id=message.conversation_id, send_time=message.sent_at.isoformat(), quote=ref["quote"])
            if value not in evidence:
                evidence.append(value)
        change["evidence"] = evidence
        latest = max(parse_time(ref["send_time"]) for ref in evidence)
        late = event is not None and (historical or latest < event.end_time)
        change["effect"] = "evidence_only" if late else change["kind"]
        text, replaced = change["text"].strip(), change["replaces"]
        identity = (change["effect"], text, replaced)
        if identity in seen:
            saved = next(r for r in records if (r["effect"], r["text"], r["replaces"]) == identity)
            saved["evidence"].extend(ref for ref in evidence if ref not in saved["evidence"])
            continue
        # 同一事实换消息 ID 仍不构成新增进展；新一次状态确认须明确标为 confirmation。
        if (event is not None and not late and not field_change and not keep_facts and change["kind"] == "progress"
                and replaced is None and text in base):
            continue
        if not late and event is not None and change["kind"] != "confirmation":
            if replaced is not None:
                if replaced.strip() == base.strip():
                    raise ContractError("摘要只允许局部修改，不能整段覆盖旧摘要")
                if base.count(replaced) != 1:
                    occurrences = sum(value.count(replaced) for value in additions)
                    corrected_values = (field_corrections or {}).get(index, set())
                    field_only = bool(corrected_values) and not any(
                        value in planned_text for value in corrected_values
                        for planned_text in [*additions, *(replacement[2] for replacement in replacements)])
                    if (change["kind"] != "correction" or replaced in base or
                            (occurrences != 1 and not (occurrences == 0 and field_only))):
                        raise ContractError("被替换的摘要片段不存在或不能唯一定位")
                    additions = [value.replace(replaced, text) for value in additions]
                else:
                    start, stop = base.index(replaced), base.index(replaced) + len(replaced)
                    if any(start < end and left < stop for left, end, _ in replacements):
                        raise ContractError("同一批摘要修改片段冲突，需要明确后再处理")
                    if replaced == text:
                        continue
                    replacements.append((start, stop, text))
            elif text not in base:
                additions.append(text)
        change["text"] = text
        seen.add(identity)
        records.append(change)
    summary = base
    for start, stop, text in sorted(replacements, reverse=True):
        summary = summary[:start] + text + summary[stop:]
    for text in additions:
        if text not in summary:
            summary = summary.rstrip() + "\n" + text
    return summary, records


def explicitly_corrects_typed_attribute(candidate, fact):
    """只接受与字段直接证据同源、且明确写出新值的纠正变化。"""
    evidence_ids = set(fact.get("evidence_ids", []))
    return any(change.get("kind") == "correction"
               and fact.get("value") in change.get("text", "")
               and evidence_ids.intersection(ref["message_id"] for ref in change.get("evidence", []))
               for change in candidate.changes or [])


def restrict_fields(candidate, event, changes, available):
    """字段按自身证据时间过滤，防止一条新消息把同候选中的迟到字段带进当前结论。"""
    if event is None:
        return candidate
    active = {f"{ref['source']}:{ref['message_id']}" for change in changes
              if change["effect"] in {"progress", "correction"} for ref in change["evidence"]}
    extracted = candidate.extensions.get("extraction_v1", {})

    def supported(fact):
        if fact is None:
            return bool(active)
        refs = [key for key in fact["evidence_ids"] if fact["quote"] in available[key].content]
        return bool(refs) and set(refs).issubset(active) and max(available[key].sent_at for key in refs) >= event.end_time

    attributes = {fact["name"]: fact for fact in extracted.get("attributes", [])}
    typed_names = {name for facts in candidate.typed_attributes.values() for name in facts}
    supported_typed_names = {name for label, facts in candidate.typed_attributes.items()
                             for name, fact in facts.items() if supported(fact)}
    typed_attributes = {
        label: {name: fact for name, fact in facts.items() if supported(fact)}
        for label, facts in candidate.typed_attributes.items()
    }
    return replace(candidate,
        status=candidate.status if supported(extracted.get("status")) else None,
        severity=candidate.severity if supported(extracted.get("severity")) else None,
        assignees=candidate.assignees if active and all(supported(fact) for fact in extracted.get("assignees", [])) else [],
        attributes={key: value for key, value in candidate.attributes.items()
                    if (supported(attributes[key]) if key in attributes else
                        key in supported_typed_names if key in typed_names else bool(active))},
        typed_attributes={label: facts for label, facts in typed_attributes.items()
                          if (label not in candidate.removed_types or label in candidate.label_types()) and facts},
        extensions=candidate.extensions if active else {})
