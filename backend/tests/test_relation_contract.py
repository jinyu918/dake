from copy import deepcopy

import pytest

from dake.analysis.contract import ContractError
from final_replay import record
from test_classification_final import case
from test_classification_replay import msg, ref


@pytest.mark.parametrize("invalid", ["target", "basis", "quote", "duplicate", "self"])
def test_invalid_relation_cannot_write_any_event(case, invalid):
    first = msg("relation-parent", 1, "事项T的A下载异常。")
    case.send(first, record(first, ["排障"]))
    parent = case.event()
    second = msg("relation-child", 2, "因事项T异常另立迁移M。")
    item = record(second, ["流失"], old=parent if invalid == "self" else None, anchor=first if invalid == "self" else None)
    relation = dict(kind="caused_by", event_id="not-provided" if invalid == "target" else parent.event_id,
        intent="attach", reason="因事项T异常另立迁移M", evidence=[ref(second, "没有出现的引文" if invalid == "quote" else None)])
    item["relations"] = [relation, deepcopy(relation)] if invalid == "duplicate" else [relation]
    if invalid not in {"basis", "self"}:
        item["basis"].append(ref(first))
    before = deepcopy(case.store.events)
    with pytest.raises(ContractError):
        case.send(second, item)
    assert case.store.events == before


def test_three_event_relation_cycle_is_rejected(case):
    messages, events = [], []
    for day in range(1, 4):
        message = msg(f"chain-{day}", day, f"事项T{day}因前项另立目标。")
        item = record(message, ["排障"])
        if events:
            item["relations"] = [dict(kind="based_on", event_id=events[-1].event_id, intent="attach",
                reason="因前项另立目标", evidence=[ref(message)])]
            item["basis"].append(ref(messages[-1]))
        result = case.send(message, item)
        messages.append(message)
        events.append(case.event(result.created[0]))
    closing = msg("cycle-close", 4, "事项T1基于事项T3。")
    item = record(closing, ["排障"], old=events[0], anchor=messages[0])
    item["relations"] = [dict(kind="based_on", event_id=events[-1].event_id, intent="attach",
        reason="事项T1基于事项T3", evidence=[ref(closing)])]
    item["basis"].append(ref(messages[-1]))
    before = deepcopy(case.store.events)
    with pytest.raises(ContractError, match="循环"):
        case.send(closing, item)
    assert case.store.events == before


def test_same_batch_cycle_rolls_back_the_first_relation(case):
    first = msg("batch-parent", 1, "事项A的下载异常。")
    case.send(first, record(first, ["排障"]))
    parent = case.event()
    second = msg("batch-child", 2, "事项B的上传异常。")
    result = case.send(second, record(second, ["排障"]))
    child = case.event(result.created[0])
    follow = msg("batch-cycle", 3, "事项A基于事项B，事项B又基于事项A。")
    items = []
    for old, anchor, target, basis in [(parent, first, child, second), (child, second, parent, first)]:
        item = record(follow, ["排障"], old=old, anchor=anchor)
        item["relations"] = [dict(kind="based_on", event_id=target.event_id, intent="attach",
            reason=follow.content, evidence=[ref(follow)])]
        item["basis"].append(ref(basis))
        items.append(item)
    before = deepcopy(case.store.events)
    with pytest.raises(ContractError, match="循环"):
        case.send(follow, tuple(items))
    assert case.store.events == before
