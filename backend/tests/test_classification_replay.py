"""使用 schema 4.0 固定输出回放合成分类场景。"""

from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from dake.analysis.attribution import parse_attribution_output
from dake.analysis.contract import ContractError
from dake.config import Settings
from dake.domain.models import Message, Window, parse_time
from dake.services.processing import IncrementalService
from fakes import FakeSource, MemoryStore


UID = "synthetic-classification-replay"
STREAM = "synthetic-formal-attribution-v4"
BASE = parse_time("2038-06-01T00:00:00+08:00")


def day_window(day):
    start = BASE + timedelta(days=day - 1)
    return Window(start, start + timedelta(days=1))


def msg(message_id, day, text, *, hour=9, minute=0, room="synthetic-room"):
    return Message(UID, message_id, day_window(day).start + timedelta(hours=hour, minutes=minute),
                   text, source="synthetic", conversation_id=room, sender_id="synthetic-human")


def ref(message, quote=None):
    return {"message_id": message.key, "quote": quote or message.content}


def output_candidate(message, *, label="排障", labels=None, attributes=(), action="new", event_id=None,
                     old_message=None, summary=None, changes=None, removed=(), classification_status=None,
                     other_reason=None, topic="synthetic-topic"):
    selected = [label] if labels is None and label != "待分类" else (labels or [])
    if classification_status is None:
        classification_status = "待分类" if label == "待分类" else "已分类"
    if changes is None:
        changes = ([{"kind": "progress", "text": message.content,
                     "evidence": [ref(message)], "replaces": None}]
                   if action == "new" else [] if action in {"duplicate", "review"} else
                   [{"kind": "progress", "text": message.content,
                     "evidence": [ref(message)], "replaces": None}])
    return {
        "type": label,
        "topic_key": topic,
        "summary": summary or message.content,
        "evidence_ids": [message.key],
        "status": None,
        "severity": None,
        "assignees": [],
        "attributes": [],
        "extensions": [],
        "decision": action,
        "referenced_event_id": event_id,
        "decision_reason": "合成回放固定输出，非模型判断",
        "type_labels": [
            {"type": value, "evidence_ids": [message.key], "quote": message.content}
            for value in selected
        ],
        "removed_types": [
            {"type": value, "evidence_ids": [message.key], "quote": message.content}
            for value in removed
        ],
        "typed_attributes": [
            {"type_label": attribute[0], "name": attribute[1], "value": attribute[2],
             "evidence_ids": [message.key], "quote": message.content}
            for attribute in attributes
        ],
        "classification_status": classification_status,
        "other_reason": other_reason,
        "basis": [ref(message), *([ref(old_message)] if old_message else [])],
        "possible_event_ids": [],
        "changes": changes,
        "continuity": None,
    }


class ContractReplayProvider:
    def __init__(self, mapping=None):
        self.name = STREAM
        self.mapping = mapping or {}
        self.calls = []
        self.background = None

    def configure_history_retrieval(self, history):
        self.history = history

    def configure_history_context(self, background):
        self.background = background

    def extract(self, new_messages, context, existing_events):
        self.calls.append(deepcopy((new_messages, context, existing_events)))
        candidates = []
        for message in new_messages:
            value = self.mapping.get(message.message_id, [])
            candidates.extend(deepcopy(value if isinstance(value, list) else [value]))
        return parse_attribution_output(json.dumps({"schema_version": "4.0", "candidates": candidates},
                                                   ensure_ascii=False),
                                        new_messages, context, existing_events,
                                        history_items=self.background.items if self.background else ())


def replay(tmp_path, messages, mapping=None):
    settings = Settings(uid=UID, data_dir=tmp_path, start=BASE,
                        end=BASE + timedelta(days=20), provider="stub",
                        context_lookback=timedelta(days=20), closed_statuses=("虚构已恢复",))
    store = MemoryStore()
    provider = ContractReplayProvider(mapping)
    source = FakeSource(messages)
    return IncrementalService(store, source, provider, settings), store, provider, source


@pytest.mark.parametrize(("label", "text", "attribute", "value"), [
    ("商机", "决定为虚构项目新增采购2套虚构服务并进行购买评估", "计划规模", "新增采购2套"),
    ("流失", "计划将虚构A业务迁出30%，暂未执行", "减少或转移规模", "迁出30%"),
    ("排障", "虚构A接口从200毫秒退化到5秒，请排查", "症状与对照表现", "从200毫秒退化到5秒"),
    ("资源需求", "申请把虚构A环境配额提高到2000次/分钟", "目标总量与增减量", "提高到2000次/分钟"),
    ("功能需求", "请让虚构A导出支持批量取消长任务", "期望行为与改善目标", "支持批量取消长任务"),
    ("商务需求", "请办理虚构合同C续费并提供续费方案", "合同与商务条件", "办理虚构合同C续费"),
])
def test_each_official_type_saves_a_first_round_typed_attribute(tmp_path, label, text, attribute, value):
    message = msg("opening", 1, text)
    record = output_candidate(message, label=label,
                              attributes=[(label, attribute, value)], topic=f"synthetic-{label}")
    service, store, _, _ = replay(tmp_path, [message], {message.message_id: record})

    result = service.run_window(UID, day_window(1))

    assert len(result.created) == 1 and not result.review
    event = store.events[result.created[0]]
    fact = event.typed_attributes[label][attribute]
    assert fact["value"] == value and fact["evidence_ids"] == [message.key]
    assert fact["evidence_refs"] == [{
        "source": message.source,
        "message_id": message.message_id,
        "id": message.message_id,
        "digest": message.digest,
        "conversation_id": message.conversation_id,
        "send_time": message.sent_at.isoformat(),
        "quote": message.content,
    }]
    assert event.type_evidence[label][0]["evidence_refs"][0]["digest"] == message.digest
    snapshot = event.updated_by[0]["snapshot"]
    assert snapshot["typed_attributes"][label][attribute] == fact


def test_adding_evidence_to_an_existing_label_saves_without_summary_change(tmp_path):
    first = msg("first", 1, "虚构A下载失败，请排查")
    second = msg("second", 2, "虚构A下载仍失败，原排障标签继续适用")
    service, store, _, _ = replay(tmp_path, [first, second])
    service.provider.mapping[first.message_id] = output_candidate(first, label="排障")
    created = service.run_window(UID, day_window(1)).created[0]
    before_summary = store.events[created].summary
    service.provider.mapping[second.message_id] = output_candidate(
        second, label="排障", action="update", event_id=created, old_message=first,
        summary=before_summary, changes=[])

    result = service.run_window(UID, day_window(2))

    assert result.updated == [created] and not result.created, (result.review, result.filter_audit)
    event = store.events[created]
    assert event.summary == before_summary
    references = event.type_evidence["排障"]
    assert [entry["evidence_refs"][0]["message_id"] for entry in references] == ["first", "second"]
    assert event.updated_by[-1]["change_kind"] == "classification"


def test_re_adopting_a_label_later_in_the_same_batch_preserves_its_removal_history(tmp_path):
    first = msg("opening", 1, "虚构A接口失败，同时申请配额目标总量1000次/分钟")
    revoke = msg("revoke", 2, "更正：本次没有配额申请，虚构A排障仍在处理", hour=9)
    readopt = msg("readopt", 2, "确认另有虚构A配额申请，本轮目标总量为2000次/分钟，资源需求标签恢复", hour=10,
                  room="synthetic-second-room")
    service, store, provider, _ = replay(tmp_path, [first, revoke, readopt])
    provider.mapping[first.message_id] = output_candidate(
        first, labels=["排障", "资源需求"], attributes=[("资源需求", "目标总量与增减量", "1000次/分钟")])
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[revoke.message_id] = output_candidate(
        revoke, label="排障", labels=["排障"], action="update", event_id=event_id,
        old_message=first, removed=["资源需求"])
    provider.mapping[readopt.message_id] = output_candidate(
        readopt, label="排障", labels=["排障", "资源需求"], action="update", event_id=event_id,
        old_message=first, attributes=[("资源需求", "目标总量与增减量", "2000次/分钟")])

    result = service.run_window(UID, day_window(2))

    assert result.updated == [event_id] and not result.created, (result.review, result.filter_audit)
    event = store.events[event_id]
    assert event.types == ["排障", "资源需求"]
    assert event.removed_types["资源需求"][0]["evidence_ids"] == [revoke.key]
    assert event.type_evidence["资源需求"][-1]["evidence_refs"][0]["message_id"] == readopt.message_id
    fact = event.typed_attributes["资源需求"]["目标总量与增减量"]
    assert fact["value"] == event.attributes["目标总量与增减量"] == "2000次/分钟"
    assert fact["evidence_refs"][0]["message_id"] == readopt.message_id
    assert event.updated_by[-1]["snapshot"]["types"] == event.types


@pytest.mark.parametrize("previous_value", [None, "1200次/分钟"])
def test_same_batch_explicit_field_correction_resolves_a_typed_attribute_conflict(tmp_path, previous_value):
    first = msg("opening", 1, "虚构A配额申请目标总量暂记1200次/分钟" if previous_value else "虚构A配额申请办理中")
    repeat = msg("repeat", 2, "虚构A配额申请目标总量暂记1200次/分钟", hour=9)
    correction = msg("correction", 2, "更正：目标总量先前记为1200次/分钟有误，应为900次/分钟", hour=10,
                     room="synthetic-second-room")
    service, store, provider, _ = replay(tmp_path, [first, repeat, correction])
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求",
        attributes=[("资源需求", "目标总量与增减量", previous_value)] if previous_value else [])
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[repeat.message_id] = output_candidate(
        repeat, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "1200次/分钟")])
    provider.mapping[correction.message_id] = output_candidate(
        correction, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "900次/分钟")],
        changes=[{"kind": "correction", "text": "目标总量更正为900次/分钟",
                  "evidence": [ref(correction)], "replaces": "目标总量暂记1200次/分钟"}])

    result = service.run_window(UID, day_window(2))

    event = store.events[event_id]
    assert result.updated == [event_id] and not result.review, (result.review, result.filter_audit)
    assert event.typed_attributes["资源需求"]["目标总量与增减量"]["value"] == "900次/分钟"
    assert event.attributes["目标总量与增减量"] == "900次/分钟"
    assert "字段冲突待核实" not in (event.review_flag or "")
    assert "目标总量更正为900次/分钟" in event.summary
    assert any(change["replaces"] == "目标总量暂记1200次/分钟"
               for change in event.updated_by[-1]["changes"])


@pytest.mark.parametrize("same_batch", [False, True])
def test_field_only_correction_preserves_summary_without_the_old_quantity(tmp_path, same_batch):
    first = msg("opening", 1, "虚构A配额申请办理中" if same_batch else "虚构A配额申请办理中，目标总量暂记1200次/分钟")
    repeat = msg("repeat", 2, "虚构A配额申请目标总量暂记1200次/分钟", hour=9)
    correction = msg("correction", 2, "更正：目标总量先前记为1200次/分钟有误，应为900次/分钟", hour=10)
    service, store, provider, _ = replay(tmp_path, [first, *([repeat] if same_batch else []), correction])
    summary = "虚构A配额申请办理中"
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求", summary=summary,
        attributes=[] if same_batch else [("资源需求", "目标总量与增减量", "1200次/分钟")])
    event_id = service.run_window(UID, day_window(1)).created[0]
    initial_revision = deepcopy(store.events[event_id].updated_by[0])
    if same_batch:
        progress = "虚构A配额申请已进入复核"
        provider.mapping[repeat.message_id] = output_candidate(
            repeat, label="资源需求", action="update", event_id=event_id, old_message=first,
            attributes=[("资源需求", "目标总量与增减量", "1200次/分钟")],
            changes=[{"kind": "progress", "text": progress, "evidence": [ref(repeat)], "replaces": None}])
        summary += "\n" + progress
    provider.mapping[correction.message_id] = output_candidate(
        correction, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "900次/分钟")],
        changes=[{"kind": "correction", "text": "目标总量更正为900次/分钟",
                  "evidence": [ref(correction)], "replaces": "目标总量暂记1200次/分钟"}])

    result = service.run_window(UID, day_window(2))

    event = store.events[event_id]
    assert result.updated == [event_id] and not result.review
    assert event.summary == summary
    assert event.typed_attributes["资源需求"]["目标总量与增减量"]["value"] == "900次/分钟"
    assert event.attributes["目标总量与增减量"] == "900次/分钟"
    assert "字段冲突待核实" not in (event.review_flag or "")
    assert event.updated_by[0] == initial_revision
    saved = event.updated_by[-1]["changes"][-1]
    assert saved["kind"] == saved["effect"] == "correction"
    assert saved["replaces"] == "目标总量暂记1200次/分钟"
    assert saved["evidence"][0]["message_id"] == correction.message_id


@pytest.mark.parametrize("fault", [
    "unrelated_replacement", "unrelated_correction_quote", "missing_previous_field",
    "old_value_in_summary", "old_value_in_addition", "old_value_in_replacement",
    "ambiguous_replacement", "not_correction",
])
def test_field_only_correction_does_not_accept_an_unverifiable_replacement(tmp_path, fault):
    first = msg("opening", 1, "虚构A配额申请目标总量暂记1200次/分钟")
    correction = msg("correction", 2,
                     "虚构A配额申请更正：目标总量先前记为1200次/分钟有误，应为900次/分钟；另行更正负责人为乙")
    progress = msg("progress", 2, "虚构A配额申请预计1200次/分钟", hour=8)
    progress_messages = [progress] if fault in {"old_value_in_addition", "old_value_in_replacement"} else []
    service, store, provider, _ = replay(tmp_path, [first, *progress_messages, correction])
    summary = ("虚构A配额申请目标1200次/分钟" if fault == "old_value_in_summary" else
               "目标总量暂记1200次/分钟；目标总量暂记1200次/分钟" if fault == "ambiguous_replacement" else
               "虚构A配额申请办理中")
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求", summary=summary,
        attributes=[] if fault == "missing_previous_field" else [("资源需求", "目标总量与增减量", "1200次/分钟")])
    event_id = service.run_window(UID, day_window(1)).created[0]
    if progress_messages:
        provider.mapping[progress.message_id] = output_candidate(
            progress, label="资源需求", action="update", event_id=event_id, old_message=first,
            changes=[{"kind": "progress", "text": "配额申请预计1200次/分钟", "evidence": [ref(progress)],
                      "replaces": "办理中" if fault == "old_value_in_replacement" else None}])
    provider.mapping[correction.message_id] = output_candidate(
        correction, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "900次/分钟")],
        changes=[{"kind": "progress" if fault == "not_correction" else "correction",
                  "text": "目标总量更正为900次/分钟",
                  "evidence": [ref(correction, "另行更正负责人为乙" if fault == "unrelated_correction_quote" else None)],
                  "replaces": "不存在的旧片段" if fault == "unrelated_replacement" else "目标总量暂记1200次/分钟"}])
    before = deepcopy((store.messages, store.events, store.processed, store.attributions))

    with pytest.raises(ContractError, match="被替换的摘要片段不存在或不能唯一定位"):
        service.run_window(UID, day_window(2))

    assert (store.messages, store.events, store.processed, store.attributions) == before


def test_unresolved_cross_round_typed_value_conflict_keeps_the_saved_value(tmp_path):
    first = msg("opening", 1, "申请虚构A环境目标总量1000次/分钟")
    second = msg("proposal", 2, "同一虚构A配额申请的另一份清单写900次/分钟，尚未确认")
    service, store, provider, _ = replay(tmp_path, [first, second])
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求", attributes=[("资源需求", "目标总量与增减量", "1000次/分钟")])
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[second.message_id] = output_candidate(
        second, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "900次/分钟")])

    result = service.run_window(UID, day_window(2))

    event = store.events[event_id]
    assert result.updated == [event_id] and not result.review, (result.review, result.filter_audit)
    assert event.typed_attributes["资源需求"]["目标总量与增减量"]["value"] == "1000次/分钟"
    assert event.attributes["目标总量与增减量"] == "1000次/分钟"
    assert "字段冲突待核实" in event.review_flag


@pytest.mark.parametrize(("label", "name", "initial", "progress"), [
    ("资源需求", "审批、交付与可用验证", "申请已受理", "已获批待交付"),
    ("功能需求", "上线与需求验证结果", "部分通过", "按条件验证通过"),
])
@pytest.mark.parametrize("same_batch", [False, True])
def test_typed_progress_facts_can_advance_without_correction(tmp_path, label, name, initial, progress, same_batch):
    first = msg("opening", 1, f"虚构A办理记录：{initial}")
    repeated = msg("repeat", 2, f"虚构A早间办理记录：{initial}", hour=8)
    follow = msg("follow", 2, f"虚构A本次办理进展：{progress}")
    service, store, provider, _ = replay(tmp_path, [first, *([repeated] if same_batch else []), follow])
    provider.mapping[first.message_id] = output_candidate(
        first, label=label, attributes=[(label, name, initial)])
    event_id = service.run_window(UID, day_window(1)).created[0]
    if same_batch:
        provider.mapping[repeated.message_id] = output_candidate(
            repeated, label=label, action="update", event_id=event_id, old_message=first,
            attributes=[(label, name, initial)])
    provider.mapping[follow.message_id] = output_candidate(
        follow, label=label, action="update", event_id=event_id, old_message=first,
        attributes=[(label, name, progress)])

    result = service.run_window(UID, day_window(2))

    event = store.events[event_id]
    assert result.updated == [event_id] and not result.review
    assert event.typed_attributes[label][name]["value"] == event.attributes[name] == progress
    assert event.updated_by[0]["snapshot"]["typed_attributes"][label][name]["value"] == initial
    assert event.updated_by[-1]["changes"][0]["kind"] == "progress"
    assert "字段冲突待核实" not in (event.review_flag or "")


def test_cross_round_explicit_quantity_correction_resolves_only_that_review_flag(tmp_path):
    first = msg("opening", 1, "虚构A配额申请目标总量暂记1200次/分钟")
    conflicting = msg("proposal", 2, "虚构A配额清单显示900次/分钟，尚未核实")
    correction = msg("correction", 3, "明确更正：先前目标总量1200次/分钟有误，应为900次/分钟")
    service, store, provider, _ = replay(tmp_path, [first, conflicting, correction])
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求", attributes=[("资源需求", "目标总量与增减量", "1200次/分钟")])
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[conflicting.message_id] = output_candidate(
        conflicting, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "900次/分钟")])
    service.run_window(UID, day_window(2))
    assert "字段冲突待核实" in store.events[event_id].review_flag
    provider.mapping[correction.message_id] = output_candidate(
        correction, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "900次/分钟")],
        changes=[{"kind": "correction", "text": "目标总量更正为900次/分钟",
                  "evidence": [ref(correction)], "replaces": "目标总量暂记1200次/分钟"}])

    result = service.run_window(UID, day_window(3))

    event = store.events[event_id]
    assert result.updated == [event_id] and not result.review
    assert event.typed_attributes["资源需求"]["目标总量与增减量"]["value"] == "900次/分钟"
    assert event.review_flag is None
    assert event.updated_by[-2]["snapshot"]["review_flag"].startswith("字段冲突待核实：")


def test_same_batch_conflicting_typed_values_are_flagged_and_not_silently_applied(tmp_path):
    first = msg("opening", 1, "申请虚构A环境目标总量1000次/分钟")
    second = msg("proposal-a", 2, "同一虚构A配额申请的目标总量暂记1200次/分钟", hour=9)
    third = msg("proposal-b", 2, "同一虚构A配额申请的另一份清单写900次/分钟，尚未确认", hour=10,
                room="synthetic-second-room")
    service, store, provider, _ = replay(tmp_path, [first, second, third])
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求", attributes=[("资源需求", "目标总量与增减量", "1000次/分钟")])
    event_id = service.run_window(UID, day_window(1)).created[0]
    for current, value in ((second, "1200次/分钟"), (third, "900次/分钟")):
        provider.mapping[current.message_id] = output_candidate(
            current, label="资源需求", action="update", event_id=event_id, old_message=first,
            attributes=[("资源需求", "目标总量与增减量", value)])

    result = service.run_window(UID, day_window(2))

    event = store.events[event_id]
    assert result.updated == [event_id], (result.review, result.filter_audit)
    assert event.typed_attributes["资源需求"]["目标总量与增减量"]["value"] == "1000次/分钟"
    assert "字段冲突待核实" in event.review_flag
    assert {change["text"] for change in event.updated_by[-1]["changes"]} == {second.content, third.content}
    assert event.updated_by[-1]["snapshot"]["review_flag"] == event.review_flag


def test_late_typed_value_cannot_borrow_a_later_same_batch_progress(tmp_path):
    first = msg("opening", 1, "申请虚构A环境目标总量1000次/分钟")
    checkpoint = msg("checkpoint", 2, "虚构A配额申请仍在处理中", hour=10)
    late = msg("late", 2, "补充旧核算记录：目标总量1200次/分钟", hour=9)
    recent = msg("recent", 2, "虚构A配额申请今天继续办理", hour=11,
                 room="synthetic-second-room")
    service, store, provider, source = replay(tmp_path, [first, checkpoint])
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求", attributes=[("资源需求", "目标总量与增减量", "1000次/分钟")])
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[checkpoint.message_id] = output_candidate(
        checkpoint, label="资源需求", action="update", event_id=event_id, old_message=first)
    service.run_window(UID, day_window(2))
    source.messages.extend([late, recent])
    provider.mapping[late.message_id] = output_candidate(
        late, label="资源需求", action="update", event_id=event_id, old_message=first,
        attributes=[("资源需求", "目标总量与增减量", "1200次/分钟")])
    provider.mapping[recent.message_id] = output_candidate(
        recent, label="资源需求", action="update", event_id=event_id, old_message=first)

    result = service.run_window(UID, day_window(3))

    event = store.events[event_id]
    assert result.updated == [event_id]
    assert event.typed_attributes["资源需求"]["目标总量与增减量"]["value"] == "1000次/分钟"
    assert event.attributes["目标总量与增减量"] == "1000次/分钟"


def test_other_pending_and_ordinary_consultation_remain_distinct(tmp_path):
    ordinary = msg("ordinary", 1, "什么是虚构A产品的并发？只是了解概念，没有实际业务要处理")
    training = msg("training", 1, "请为虚构A项目安排专项培训，完成实际操作讲解，不涉及采购、排障或新增功能",
                   hour=10)
    pending = msg("pending", 1, "请继续办理虚构申请R的交付，具体交付内容目前待核实", hour=11)
    clarification = msg("clarification", 2, "虚构申请R是按现有权益提高A环境配额，不涉及采购", room="synthetic-second-room")
    service, store, provider, source = replay(tmp_path, [ordinary, training, pending])
    provider.mapping[training.message_id] = output_candidate(
        training, label="其他", other_reason={"value": "专项培训", "evidence_ids": [training.key],
                                               "quote": training.content}, topic="synthetic-training")
    provider.mapping[pending.message_id] = output_candidate(
        pending, label="待分类", classification_status="待分类", labels=[], topic="synthetic-request-R")

    first = service.run_window(UID, day_window(1))

    assert len(first.created) == 2
    assert {event.summary for event in store.events.values()} == {training.content, pending.content}
    other_event = next(event for event in store.events.values() if event.type == "其他")
    pending_event = next(event for event in store.events.values() if event.type == "待分类")
    assert other_event.other_reason["value"] == "专项培训"
    assert pending_event.classification_status == "待分类" and pending_event.types == []
    source.messages.append(clarification)
    provider.mapping[clarification.message_id] = output_candidate(
        clarification, label="资源需求", action="update", event_id=pending_event.event_id,
        old_message=pending, attributes=[("资源需求", "请求动作与用途", "提高A环境配额")],
        topic="synthetic-request-R")

    second = service.run_window(UID, day_window(2))

    assert second.updated == [pending_event.event_id] and not second.created
    assert store.events[pending_event.event_id].types == ["资源需求"]
    assert store.events[pending_event.event_id].classification_status == "已分类"
    assert store.events[other_event.event_id].types == ["其他"]


def test_function_improvement_and_restoring_existing_behavior_create_separate_cases(tmp_path):
    improvement = msg("improvement", 1, "虚构A导出目前正常，希望增加批量撤销长任务的能力")
    restoration = msg("restoration", 1, "虚构A导出原有单项撤销按钮今天失效，请恢复")
    service, store, provider, _ = replay(tmp_path, [improvement, restoration])
    provider.mapping[improvement.message_id] = output_candidate(
        improvement, label="功能需求",
        attributes=[("功能需求", "期望行为与改善目标", "增加批量撤销长任务")],
        topic="synthetic-export-batch-cancel")
    provider.mapping[restoration.message_id] = output_candidate(
        restoration, label="排障",
        attributes=[("排障", "症状与对照表现", "单项撤销按钮今天失效")],
        topic="synthetic-export-restore-cancel")

    result = service.run_window(UID, day_window(1))

    assert len(result.created) == 2
    assert {event.type for event in store.events.values()} == {"功能需求", "排障"}
    assert len({event.event_id for event in store.events.values()}) == 2


def test_independent_targets_in_one_message_are_saved_as_separate_events(tmp_path):
    message = msg("mixed", 1, "A配额申请提高到2000；B导出新增批量撤销；合同C发票请办理")
    slices = [
        ("资源需求", "A配额申请提高到2000", "目标总量与增减量", "提高到2000", "synthetic-quota-A"),
        ("功能需求", "B导出新增批量撤销", "期望行为与改善目标", "新增批量撤销", "synthetic-export-B"),
        ("商务需求", "合同C发票请办理", "账单与发票要求", "发票请办理", "synthetic-contract-C"),
    ]
    items = []
    for label, quote, attribute, value, topic in slices:
        item = output_candidate(message, label=label, attributes=[(label, attribute, value)], topic=topic)
        item["summary"] = quote
        item["type_labels"][0]["quote"] = quote
        item["typed_attributes"][0]["quote"] = quote
        item["changes"][0]["text"] = quote
        item["changes"][0]["evidence"][0]["quote"] = quote
        items.append(item)
    service, store, provider, _ = replay(tmp_path, [message], {message.message_id: items})

    result = service.run_window(UID, day_window(1))

    assert len(result.created) == 3
    assert {event.type for event in store.events.values()} == {"资源需求", "功能需求", "商务需求"}
    assert all(event.evidence[0]["message_id"] == message.message_id for event in store.events.values())


def test_cross_day_cross_room_follow_up_keeps_the_same_event_id(tmp_path):
    first = msg("opening", 1, "工单T：虚构A下载失败，请排查", room="synthetic-room-A")
    follow = msg("follow", 2, "继续处理工单T，虚构A下载仍失败，清理缓存无效",
                 room="synthetic-room-B")
    service, store, provider, _ = replay(tmp_path, [first, follow])
    provider.mapping[first.message_id] = output_candidate(first, label="排障", topic="synthetic-download-A")
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[follow.message_id] = output_candidate(
        follow, label="排障", action="update", event_id=event_id, old_message=first,
        topic="synthetic-renamed-download-A")

    result = service.run_window(UID, day_window(2))

    assert result.updated == [event_id] and not result.created
    assert store.events[event_id].event_id == event_id
    refs = store.events[event_id].type_evidence["排障"]
    assert {item["evidence_refs"][0]["conversation_id"] for item in refs} == {
        "synthetic-room-A", "synthetic-room-B"}


def test_pure_acknowledgement_does_not_close_or_extend_a_closed_case(tmp_path):
    first = msg("opening", 1, "虚构A下载故障按原条件验证后状态为虚构已恢复")
    acknowledgement = msg("ack", 2, "收到，谢谢")
    service, store, provider, _ = replay(tmp_path, [first, acknowledgement])
    opening = output_candidate(first, label="排障", topic="synthetic-download-A")
    opening["status"] = {"value": "虚构已恢复", "evidence_ids": [first.key], "quote": first.content}
    provider.mapping[first.message_id] = opening
    event_id = service.run_window(UID, day_window(1)).created[0]
    before = deepcopy(store.events[event_id])

    result = service.run_window(UID, day_window(2))

    event = store.events[event_id]
    assert not result.created and not result.updated and not result.review
    assert event.status == before.status and event.closed_at == before.closed_at
    assert event.end_time == before.end_time and len(event.updated_by) == len(before.updated_by)


def test_real_request_cancellation_keeps_request_and_cancellation_history(tmp_path):
    first = msg("request", 1, "为虚构A环境申请额外100个配额用于新项目")
    cancel = msg("cancel", 2, "虚构申请R现在取消，请保留此前已提交的申请记录")
    service, store, provider, _ = replay(tmp_path, [first, cancel])
    provider.mapping[first.message_id] = output_candidate(
        first, label="资源需求", attributes=[("资源需求", "目标总量与增减量", "额外100个配额")],
        topic="synthetic-request-R")
    event_id = service.run_window(UID, day_window(1)).created[0]
    original = deepcopy(store.events[event_id].updated_by[0]["snapshot"])
    provider.mapping[cancel.message_id] = output_candidate(
        cancel, label="资源需求", action="update", event_id=event_id, old_message=first,
        topic="synthetic-request-R")

    result = service.run_window(UID, day_window(2))

    event = store.events[event_id]
    assert result.updated == [event_id] and event.types == ["资源需求"]
    assert original["typed_attributes"]["资源需求"]["目标总量与增减量"]["value"] == "额外100个配额"
    assert cancel.message_id in {item["message_id"] for item in event.evidence}
    assert event.updated_by[-1]["changes"][0]["text"] == cancel.content


def test_typed_attribute_quote_stays_addressable_when_event_excerpt_is_clipped(tmp_path):
    prefix = "虚构配额办理背景" * 35
    text = prefix + "请把目标总量提高到2000次/分钟"
    message = msg("long-opening", 1, text)
    record = output_candidate(message, label="资源需求",
                              attributes=[("资源需求", "目标总量与增减量", "提高到2000次/分钟")])
    service, store, _, _ = replay(tmp_path, [message], {message.message_id: record})

    result = service.run_window(UID, day_window(1))

    event = store.events[result.created[0]]
    fact = event.typed_attributes["资源需求"]["目标总量与增减量"]
    assert len(event.evidence[0]["excerpt"]) == 160
    assert fact["evidence_refs"][0]["quote"] == message.content
    assert fact["evidence_refs"][0]["digest"] == message.digest
    assert event.summary == message.content
