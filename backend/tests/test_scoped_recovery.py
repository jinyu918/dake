from dataclasses import replace

import pytest

from final_replay import record
from test_classification_final import boundary, case
from test_classification_replay import msg, ref


@pytest.mark.parametrize("scope", ["A上传测试环境", "A全部环境", None])
def test_support_recovery_only_establishes_its_verified_scope(case, monkeypatch, scope):
    monkeypatch.setattr("dake.services.processing.load_sender_roles", lambda path: {
        ("synthetic-classification-replay", "synthetic", "support"): "internal"})
    first = msg("scope-fault", 1, "A上传测试环境与生产环境失败，请排查。")
    case.send(first, record(first, ["排障"]))
    parent = case.event()
    fixed = replace(msg("scope-fixed", 2, "支持方已在A上传测试环境核验恢复，生产环境未测，客户尚未验收。"), sender_id="support")
    item = record(fixed, ["排障"], old=parent, anchor=first)
    item["continuity"] = dict(boundary("verify_recovery", fixed), scope="A上传测试环境")
    case.send(fixed, item)
    saved = case.event()
    assert saved.recovery["identity_basis"] == "confirmed_support_account"
    assert saved.recovery["scope"] == "A上传测试环境" and saved.closed_at is None
    assert saved.recovery["evidence"][0]["sender_role"] == "internal"
    again = msg("scope-again", 3, f"{scope}在核验恢复后出现新的失败窗口。" if scope else "又不行了。")
    item = record(again, ["排障"])
    item["basis"].append(ref(first))
    item["continuity"] = dict(boundary("recurrence", again, parent.event_id), scope=scope)
    result = case.send(again, item)
    if scope == "A上传测试环境":
        assert len(case.store.events) == 2 and len(result.created) == 1
        child = case.event(result.created[0])
        assert child.recurrence_of["recovery"]["scope"] == scope
        assert child.recurrence_of["review_status"] == "confirmed"
    else:
        assert len(case.store.events) == 1 and not result.created and result.review


def test_unconfirmed_support_identity_cannot_establish_recovery(case):
    first = msg("unverified-first", 1, "A测试环境失败。")
    case.send(first, record(first, ["排障"]))
    fixed = replace(msg("unverified-fixed", 2, "A测试环境已核验恢复。"), sender_id="unconfigured-support")
    item = record(fixed, ["排障"], old=case.event(), anchor=first)
    item["continuity"] = dict(boundary("verify_recovery", fixed), scope="A测试环境")
    result = case.send(fixed, item)
    assert case.event().recovery is None and result.review and not result.updated
