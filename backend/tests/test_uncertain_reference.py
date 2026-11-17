"""待确认引用字段归位，不把不明确恢复解释为已恢复或复发。"""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from dake.analysis.continuity import normalize_uncertain_reference
from test_attribution import candidate, message


@pytest.mark.parametrize("case", ["known", "duplicate", "unknown", "wrong_type", "update", "recurrence", "has_changes"])
def test_uncertain_reference_normalization_keeps_business_decision(case):
    m = message("synthetic", 2, "合成问题是否恢复尚不明确。")
    c = candidate(m, decision="review", changes=[])
    c.continuity = dict(kind="uncertain", previous_event_id="synthetic-event", evidence=[dict(message_id=m.key,quote=m.content)])
    events = [SimpleNamespace(event_id="synthetic-event", type=c.type)]
    if case == "duplicate": c.possible_event_ids = ["synthetic-event"]
    if case == "unknown": events = []
    if case == "wrong_type": events[0].type = "不同合成类型"
    if case == "update": c.decision = "update"
    if case == "recurrence": c.continuity["kind"] = "recurrence"
    if case == "has_changes": c.changes = [dict(kind="progress")]
    before = deepcopy(c)
    normalized = normalize_uncertain_reference(c, events)
    if case in {"known", "duplicate", "wrong_type", "has_changes"}:
        assert c.decision == "review" and c.changes == before.changes and c.referenced_event_id is None
        assert c.continuity["previous_event_id"] is None and c.possible_event_ids == ["synthetic-event"]
        assert c.continuity["evidence"] == before.continuity["evidence"]
        assert normalized[0]["reason"] == "uncertain_reference_to_possible_events"
        assert normalize_uncertain_reference(c, events) == []
    else:
        assert normalized == [] and c == before


@pytest.mark.parametrize("action", ["review", "new", "update", "duplicate"])
def test_unknown_review_candidates_are_audited_without_changing_action(action):
    from dake.analysis.attribution import normalize_review_candidates
    c = candidate(message("review", 2), decision=action, changes=[])
    c.possible_event_ids = ["known", "unprovided", "known"]
    before = deepcopy(c)
    records = normalize_review_candidates(c, [SimpleNamespace(event_id="known")])
    if action == "review":
        assert c.possible_event_ids == ["known"] and c.decision == "review" and c.changes == []
        assert records[0]["excluded_event_ids"] == ["unprovided"]
        assert normalize_review_candidates(c, []) == [dict(field="possible_event_ids",
            reason="unprovided_review_candidates_excluded", excluded_event_ids=["known"])]
        assert c.possible_event_ids == [] and c.decision == "review"
    else:
        assert records == [] and c == before


def test_repair_feedback_identifies_field_and_basis_errors_together():
    import json
    from dake.analysis.attribution import parse_attribution_output
    from dake.analysis.contract import ContractError
    from test_attribution import model_output
    m = message("synthetic", 2, "合成问题仍在等待排查。")
    raw = json.loads(model_output(m))
    c = raw["candidates"][0]
    c["status"] = dict(value="没有说过的状态", quote=m.content, evidence_ids=[m.key])
    c["basis"][0]["quote"] = "模型改写后的合成原文"
    with pytest.raises(ContractError) as error:
        parse_attribution_output(json.dumps(raw), [m], [], [])
    assert "candidates[0].status" in str(error.value)
    assert "candidates[0].basis[0]" in str(error.value)
    assert m.content not in str(error.value)
