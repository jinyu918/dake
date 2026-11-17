"""只读投影执行记录；不读取当前聊天、不核实事务、不触发重试或模型调用。"""

from copy import deepcopy

from ..domain.execution_links import link_reasons
from ..domain.models import parse_time
from .executions import fingerprint


def _ref(value, role, quote=None):
    """正文仅来自当时保存的输入或片段，元数据按同一版本一起投影。"""
    return dict(source=value.get("source"), message_id=value.get("message_id"), digest=value.get("digest"),
        conversation_id=value.get("conversation_id"), send_time=value.get("send_time", value.get("sent_at")),
        quote=quote if quote is not None else value.get("quote", value.get("excerpt")), role=role)


def _scoped(value, event):
    return all(value.get(k, event[k]) == event[k] for k in ("uid", "stream"))


def _inputs(stages, event):
    results = []
    for stage in stages:
        name, payload = stage["stage"], stage["payload"]
        if name == "input":
            manifest = payload.get("manifest", {})
            results.append(dict(origin="manifest", call_no=None,
                new_messages=[_ref(m, "new") for m in manifest.get("pending", [])],
                auxiliary_messages=[_ref(m, "auxiliary") for m in manifest.get("auxiliary", [])]))
        elif name in {"model_started", "provider_input"}:
            body = payload.get("input", {}) if name == "model_started" else payload
            if not _scoped(body, event):
                continue
            versions = {m["key"]: m for m in payload.get("message_versions", []) if m.get("key")}
            groups = {}
            for field, role in (("new_messages", "new"), ("context_messages", "auxiliary")):
                messages = []
                for message in body.get(field, []):
                    if not _scoped(message, event):
                        continue
                    version = versions.get(message.get("id"), {}) if name == "model_started" else message
                    # 正式请求正文必须能连回同一次请求保存的版本清单，否则不替它猜版本。
                    value = dict(version, conversation_id=message.get("conversation_id"),
                                 send_time=message.get("send_time", message.get("sent_at")))
                    messages.append(_ref(value, role, message.get("content")))
                groups["new_messages" if role == "new" else "auxiliary_messages"] = messages
            results.append(dict(origin="request" if name == "model_started" else "provider",
                                call_no=payload.get("call_no"), **groups))
    return results


def _basis(refs, saved):
    result = []
    for ref in refs:
        key, quote = ref.get("message_id"), ref.get("quote")
        matches = [m for m in saved if m.get("source") and f"{m['source']}:{m['message_id']}" == key
                   and quote and m.get("quote") and quote in m["quote"]]
        unique = {(m.get("source"), m.get("message_id"), m.get("digest")): m for m in matches}
        result.append(dict(next(iter(unique.values())), quote=quote) if len(unique) == 1 else
                      dict(source=None, message_id=key, digest=None, conversation_id=None, send_time=None, quote=quote, role=None))
    return result


def _commit(record, event, revisions):
    receipt = record.get("receipt")
    empty = dict(commit_status="not_committed" if record["outcome"] == "not_committed" else "unconfirmed",
        receipt_kind=None, receipt_prepared_at=None, processed=None, created=False, updated=False,
        revision_indexes=[], revision_link_unavailable=False)
    if not receipt:
        if record["outcome"] == "committed":
            empty["commit_status"] = "missing"
        return empty
    # 未知格式或缺失清单不能被默认成零写入；恢复收据本身不能再次认领业务成果。
    if (receipt.get("version") != 1 or receipt.get("kind") not in {"analysis", "delivery_recovery"} or
            not isinstance(receipt.get("writes"), list) or not isinstance(receipt.get("processed_versions"), list) or
            (receipt["kind"] == "delivery_recovery" and (receipt["writes"] or receipt["processed_versions"]))):
        return dict(empty, commit_status="invalid")
    cycle = receipt.get("cycle", {})
    if ((cycle.get("uid"), cycle.get("stream"), cycle.get("attempt_id")) !=
            (event["uid"], event["stream"], record["attempt_id"]) or
            cycle.get("window") != dict(start=record["window_start"], end=record["window_end"])):
        return dict(empty, commit_status="invalid")
    writes = [w for w in receipt.get("writes", []) if w.get("event_id") == event["event_id"]]
    indexes = []
    # 收据引用原快照及修订位置；不能按同窗口寻找后来更新的版本。
    matching_ref = next((r for r in receipt.get("event_versions", []) if r.get("event_id") == event["event_id"]), None)
    reference_valid = False
    if matching_ref:
        index = matching_ref.get("revision_index")
        if isinstance(index, int) and 0 <= index < len(revisions):
            snapshot = deepcopy(revisions[index].get("snapshot"))
            if snapshot and (snapshot.get("uid"), snapshot.get("stream"), snapshot.get("event_id")) == (event["uid"], event["stream"], event["event_id"]):
                snapshot["updated_by"] = [{k: v for k, v in r.items() if k != "snapshot"}
                    for r in event.get("updated_by", [])[:index + 1] if r.get("source") != "manual"
                    and parse_time(r["window_end"]) <= parse_time(record["window_end"])]
                reference_valid = fingerprint(snapshot) == matching_ref.get("digest")
    if reference_valid and receipt.get("kind") == "analysis":
        for write in writes:
            index = write.get("revision_index")
            if not isinstance(index, int) or not 0 <= index < len(revisions):
                continue
            revision = revisions[index]
            if (revision.get("attempt_id") == record["attempt_id"] and revision.get("snapshot") and
                    revision.get("revision_no") == write.get("revision_no") and revision.get("action") == write.get("action")):
                indexes.append(index)
    return dict(commit_status="committed", receipt_kind=receipt.get("kind"),
        receipt_prepared_at=receipt.get("receipt_prepared_at"), processed=len(receipt.get("processed_versions", [])),
        created=any(w.get("action") == "created" for w in writes), updated=any(w.get("action") == "updated" for w in writes),
        revision_indexes=indexes, revision_link_unavailable=len(indexes) != len(writes))


def execution_changes(event, revisions, page, offset, *, include_details=True):
    """列表与投影双重限制范围；只读收据，不把读取操作变成显式核实。"""
    result = []
    known, related = set(page.get("known_ids", [])), set(page.get("related_ids", []))
    revision_ids = {r.get("attempt_id") for r in event.get("updated_by", [])}
    for record in page["items"]:
        if (record.get("uid"), record.get("stream")) != (event["uid"], event["stream"]):
            continue
        reasons = link_reasons(record, event["event_id"])
        if record["attempt_id"] in revision_ids:
            reasons.append("revision")
        if record.get("retry_of") in related:
            reasons.append("retry")
        if not reasons:
            continue
        stages = [s for s in record.get("stages", []) if isinstance(s.get("payload"), dict)
                  and _scoped(s["payload"].get("input", {}), event)]
        inputs, candidates, contexts, context_reasons, suggestions, decisions = [], [], [], [], [], []
        omitted = None
        if include_details:
            inputs = _inputs(stages, event)
            model_inputs = [s["payload"]["input"] for s in stages if s["stage"] == "model_started" and "input" in s["payload"]]
            sent_ids = {c.get("event_id") for body in model_inputs for c in body.get("existing_events", [])}
            candidates, contexts, context_reasons, omitted = [], [], [], None
            for stage in stages:
                payload = stage["payload"]
                if stage["stage"] == "history":
                    candidates.extend(dict(event_id=c["event_id"], summary=c.get("summary"), status=c.get("status"),
                        hit_reasons=c.get("hit_reasons", []), selected_for_request=c["event_id"] in sent_ids if model_inputs else None)
                        for c in payload.get("candidates", []) if _scoped(c, event))
                if stage["stage"] == "history_context" and payload.get("result"):
                    packet = payload["result"]
                    if not _scoped(packet, event):
                        continue
                    context_reasons, omitted = packet.get("reasons", []), packet.get("omitted")
                    for message in packet.get("items", []):
                        if not _scoped(message, event):
                            continue
                        selected = any(any((m.get("source"), m.get("message_id"), m.get("digest")) ==
                            (message.get("source"), message.get("message_id"), message.get("digest"))
                            for m in body.get("history_context", {}).get("items", [])) for body in model_inputs)
                        fragments = message.get("fragments", [])
                        contexts.append(dict(_ref(message, "history"), fragments=fragments, links=message.get("links", []),
                            availability=message.get("availability"), truncated=message.get("truncated"),
                            selected_for_request=selected if model_inputs else None))
            saved = [m for group in inputs for field in ("new_messages", "auxiliary_messages") for m in group[field]]
            saved += [dict(m, quote=f["text"]) for m in contexts for f in m["fragments"]]
            suggestions, decisions = [], []
            final = [s for s in stages if s["stage"] == "suggestions"]
            for stage in final or [s for s in stages if s["stage"] == "model_completed"]:
                for raw in stage["payload"].get("suggestions", []):
                    value = raw.get("value", raw)
                    suggestions.append(dict(proposal_id=raw.get("proposal_id"), call_no=stage["payload"].get("call_no"),
                        action=value.get("decision"), event_id=value.get("referenced_event_id"),
                        possible_event_ids=value.get("possible_event_ids", []), summary=value.get("summary"), status=value.get("status"),
                        reason=value.get("decision_reason"), facts=[c["text"] for c in value.get("changes") or []],
                        basis=_basis(value.get("basis", []), saved)))
            for stage in stages:
                if stage["stage"] == "decision":
                    value = stage["payload"]
                    decisions.append({k: deepcopy(value.get(k)) for k in ("proposal_id", "action", "event_id", "reason", "before", "after",
                        "contributed_to_revision", "change_scope")} | dict(possible_event_ids=value.get("possible_event_ids", []),
                        added_evidence=[_ref(r, "new") for r in value.get("evidence_delta", {}).get("added", [])]))
        parent = record.get("retry_of")
        result.append(dict(attempt_id=record["attempt_id"], run_id=record.get("run_id"), window_index=record.get("window_index"),
            window=dict(start=record["window_start"], end=record["window_end"]), outcome=record["outcome"],
            created_at=record.get("created_at"), finished_at=record.get("finished_at"), error_type=record.get("error_type"),
            retry_of=parent if parent in known else None, retry_link_unavailable=bool(parent and parent not in known), related_by=reasons,
            stages=[dict(stage=s["stage"], status=s["status"], recorded_at=s.get("recorded_at"),
                         call_no=s["payload"].get("call_no"), error_type=s["payload"].get("error_type")) for s in stages],
            inputs=inputs, candidates=candidates, contexts=contexts, context_reasons=context_reasons, context_omitted=omitted,
            suggestions=suggestions, decisions=decisions, **_commit(record, event, revisions),
            delivery=[dict(destination=k, status=v["status"], observed_at=v.get("observed_at"))
                      for k, v in (record.get("delivery") or {}).items() if k in {"files", "run_progress"}]))
    return dict(executions=result, execution_records_available=page["available"], total_executions=page["total"],
        execution_offset=offset, next_execution_offset=offset + len(page["items"]) if offset + len(page["items"]) < page["total"] else None)
