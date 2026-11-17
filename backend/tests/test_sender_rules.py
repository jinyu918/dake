"""使用合成账号验证配置并发、范围与页面接口，不读取真实聊天。"""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from dake.config import Settings
from dake.services.sender_kinds import load_sender_kinds, sender_kind
from dake.services.sender_rules import RuleConflict, SenderRuleService
from offline_http import TestClient, offline_only


def account(**changes):
    return dict(uid="synthetic-rule-customer", source="official_chat", sender_id="synthetic-notice",
        conversation_id="synthetic-room", kind="bot", label="合成通知账号", basis="合成测试明确确认", enabled=True, **changes)


class Accounts:
    def observed(self, rule):
        return rule.sender_id == "synthetic-notice"

    def search(self, keyword, offset, limit):
        return {"total": 1, "items": [dict(uid="synthetic-rule-customer", source="official_chat", sender_id="synthetic-notice",
            conversation_id="synthetic-room", sender_name="合成MAAS账号", conversation_name="合成客户群", message_count=3,
            last_seen_at="2026-11-10T10:00:00+08:00")]}


@pytest.fixture
def rules(tmp_path):
    return SenderRuleService(replace(Settings(), data_dir=tmp_path), Accounts())


def test_save_and_disable_reuse_same_filter_and_private_file(rules):
    initial = rules.list()
    saved = rules.save(account(), initial["version"])
    item = saved["items"][0]
    path = rules.settings.data_dir / "config/sender_kinds.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert sender_kind(account(), load_sender_kinds(rules.settings.data_dir)) == "bot"
    assert sender_kind(dict(account(), sender_name="合成改名"), load_sender_kinds(rules.settings.data_dir)) == "bot"
    rules.save(dict(account(), enabled=False), saved["version"], item["rule_id"])
    assert sender_kind(account(), load_sender_kinds(rules.settings.data_dir)) == "unknown"


def test_stale_save_does_not_overwrite_other_editor(rules):
    initial = rules.list()
    saved = rules.save(account(), initial["version"])
    with pytest.raises(RuleConflict):
        rules.save(dict(account(), kind="human"), initial["version"], saved["items"][0]["rule_id"])
    assert rules.list() == saved


def test_group_scope_wins_and_disabled_rule_falls_back(rules):
    saved = rules.save(dict(account(), conversation_id=None), rules.list()["version"])
    saved = rules.save(dict(account(), kind="human"), saved["version"])
    assert sender_kind(account(), load_sender_kinds(rules.settings.data_dir)) == "human"
    assert sender_kind(dict(account(), conversation_id="different-room"), load_sender_kinds(rules.settings.data_dir)) == "bot"
    assert sender_kind(dict(account(), uid="other-customer"), load_sender_kinds(rules.settings.data_dir)) == "unknown"
    group = next(r for r in saved["items"] if r["conversation_id"])
    rules.save(dict(account(), kind="human", enabled=False), saved["version"], group["rule_id"])
    assert sender_kind(account(), load_sender_kinds(rules.settings.data_dir)) == "bot"


def test_edit_scope_removes_original_specific_rule(rules):
    saved = rules.save(account(), rules.list()["version"])
    saved = rules.save(dict(account(), conversation_id=None), saved["version"], saved["items"][0]["rule_id"])
    assert len(saved["items"]) == 1 and saved["items"][0]["conversation_id"] is None
    assert "conversation_id" not in json.loads((rules.settings.data_dir / "config/sender_kinds.json").read_text())["accounts"][0]


def test_search_name_is_not_automatic_bot_confirmation(rules):
    assert rules.candidates("maas")["items"][0]["sender_kind"] == "unknown"
    saved = rules.save(account(), rules.list()["version"])
    found = rules.candidates("maas")["items"][0]
    assert found["sender_kind"] == "bot" and found["configured"]
    assert found["rule_id"] == saved["items"][0]["rule_id"]
    assert "content" not in found


def test_unobserved_identity_and_duplicate_range_rejected(rules):
    with pytest.raises(ValueError):
        rules.save(dict(account(), sender_id="unobserved"), rules.list()["version"])
    saved = rules.save(account(), rules.list()["version"])
    with pytest.raises(RuleConflict):
        rules.save(account(), saved["version"])


def test_write_failure_keeps_previous_rules(rules, monkeypatch):
    saved = rules.save(account(), rules.list()["version"])
    def fail(*args):
        raise OSError("合成磁盘失败")
    monkeypatch.setattr("dake.services.sender_rules.private_json", fail)
    with pytest.raises(OSError):
        rules.save(dict(account(), enabled=False), saved["version"], saved["items"][0]["rule_id"])
    assert rules.list() == saved


def test_invalid_enable_flag_fails_closed_without_leaking_identity(rules):
    saved = rules.save(account(), rules.list()["version"])
    path = rules.settings.data_dir / "config/sender_kinds.json"
    value = json.loads(path.read_text())
    value["accounts"][0]["enabled"] = "false"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError) as error:
        load_sender_kinds(rules.settings.data_dir)
    assert saved["items"][0]["sender_id"] not in str(error.value)


def test_http_contract_conflict_and_origin_boundary(rules, offline_only):
    from dake.api.app_factory import create_app
    from test_api import TestRuns, TestQueries, WakeOnly
    app = create_app(rules.settings, run_repository=TestRuns(), query_service=TestQueries(rules.settings),
        runner=WakeOnly(), sender_rule_service=rules, start_worker=False)
    with TestClient(app) as client:
        initial = client.get("/api/v1/sender-rules").json()
        assert client.get("/api/v1/sender-rules/candidates", params={"keyword": "maas"}).json()["total"] == 1
        payload = {"expected_version": initial["version"], "rule": account()}
        assert client.post("/api/v1/sender-rules", json=payload, headers={"Origin": "https://foreign.invalid"}).status_code == 403
        saved = client.post("/api/v1/sender-rules", json=payload)
        assert saved.status_code == 200 and saved.json()["items"][0]["enabled"]
        assert client.post("/api/v1/sender-rules", json=payload).status_code == 409
        invalid = deepcopy(payload)
        invalid["rule"]["kind"] = "guessed-bot"
        assert client.post("/api/v1/sender-rules", json=invalid).status_code == 422
