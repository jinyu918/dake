"""真实 Provider 使用合成夹具验证授权开关、处理流、任务冻结和私有记录。"""

from dataclasses import replace
from datetime import timedelta
import json
import stat

import httpx
import pytest

from dake.analysis.providers.xhub import XHubConfig, XHubError, XHubProvider, configured_stream
from dake.config import Settings
from dake.jobs.runner import prepare_request
from dake.services.queries import QueryService
from dake.services.enrich import enrich_evidence
from dake.services.history_retrieval import HistoryRetriever
from fakes import MemoryStore
from provider_fakes import candidate, message, output, response
from test_runs import execute_next, run_fixture


@pytest.mark.parametrize("formal", [False, True])
def test_gateway_strict_schema_requires_every_property_recursively(tmp_path, formal):
    settings = replace(Settings(), provider="xhub", uid="synthetic-authorized", data_dir=tmp_path)
    msg = replace(message(), uid=settings.uid)

    def check_schema(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            for nested in value.values():
                check_schema(nested)
        elif isinstance(value, list):
            for nested in value:
                check_schema(nested)

    def reply(request):
        body = json.loads(request.content)
        check_schema(body["response_format"]["json_schema"]["schema"])
        return response(json.dumps(dict(schema_version="5.0" if formal else "1.0", candidates=[])))

    client = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-key",
                                   max_tokens=settings.llm_max_tokens), settings,
                          transport=httpx.MockTransport(reply))
    try:
        if formal:
            history = HistoryRetriever(MemoryStore(), settings.history_retrieval).retrieve(
                settings.uid, client.name, [msg], msg.sent_at + timedelta(minutes=5))
            client.configure_history_retrieval(history)
        assert client.extract([msg], [], []) == []
        assert client.name == configured_stream(settings)
    finally:
        client.close()


def test_official_provider_uses_same_contract_and_private_trace(tmp_path):
    settings = replace(Settings(), provider="xhub", uid="synthetic-authorized", data_dir=tmp_path)
    msg = replace(message(), uid=settings.uid, source="official_chat")
    client = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-key", max_tokens=8192),
                          settings, transport=httpx.MockTransport(
                              lambda _: response(output(candidate(evidence_ids=[msg.key])))))
    try:
        assert client.name == configured_stream(settings)
        assert len(client.extract([msg], [], [])) == 1
        path = next((tmp_path / "raw" / "xhub" / client.name).glob("*.json"))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        saved = json.loads(path.read_text())
        assert saved["ok"] is True
        assert "synthetic-key" not in path.read_text()
    finally:
        client.close()


@pytest.mark.parametrize("kind", ["disabled", "other_customer", "synthetic_source"])
def test_official_provider_rejects_wrong_scope_before_sending(tmp_path, kind):
    settings = replace(Settings(), provider="xhub", uid="synthetic-authorized", data_dir=tmp_path)
    msg = replace(message(), uid=settings.uid, source="official_chat")
    if kind == "disabled":
        settings = replace(settings, provider="stub")
    if kind == "other_customer":
        msg = replace(msg, uid="different-customer")
    if kind == "synthetic_source":
        msg = replace(msg, source="synthetic_experiment")
    calls = []
    client = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-key"), settings,
                          transport=httpx.MockTransport(lambda request: calls.append(request)))
    try:
        with pytest.raises(XHubError):
            client.extract([msg], [], [])
        assert not calls
    finally:
        client.close()


def test_queries_and_jobs_use_same_model_version_and_queries_stay_scoped():
    settings = replace(Settings(), provider="xhub")
    query = QueryService(settings)
    request, _ = prepare_request(settings, {"interval": "1d"})
    assert request["analysis_stream"] == query.scope()[1] == configured_stream(settings)
    assert request["llm_model"] == settings.llm_model
    assert request["llm_max_tokens"] == 8192
    assert "API_KEY" not in json.dumps(request)
    changed = replace(settings, llm_model="another-model")
    assert configured_stream(changed) != configured_stream(settings)
    assert configured_stream(replace(settings, llm_timeout_seconds=300)) == configured_stream(settings)
    with pytest.raises(ValueError):
        query.scope(settings.uid, "stub-v1")


def test_task_freezes_model_and_rejects_changed_prompt_identity(tmp_path):
    settings, repository, runner, _, _, _, _, total, seen = run_fixture(tmp_path, days=1)
    selected = replace(settings, provider="xhub", llm_model="synthetic-model")
    request, total = prepare_request(selected, {"interval": "1d"})
    runner.settings = replace(selected, llm_model="later-model")
    run = execute_next(repository, runner, request, total)
    assert run["status"] == "succeeded"
    assert seen[-1].llm_model == "synthetic-model" and seen[-1].provider == "xhub"
    request["analysis_stream"] = "changed-prompt-version"
    run = execute_next(repository, runner, request, total)
    assert run["status"] == "failed" and run["completed_windows"] == 0
    assert "提示词" in run["error"]


def test_old_queued_task_does_not_silently_switch_from_stub_to_model(tmp_path):
    settings, repository, runner, _, _, _, request, total, seen = run_fixture(tmp_path, days=1)
    for key in ("provider", "llm_model", "llm_max_tokens", "llm_timeout_seconds", "analysis_stream"):
        request.pop(key)
    runner.settings = replace(settings, provider="xhub")
    run = execute_next(repository, runner, request, total)
    assert run["status"] == "succeeded" and seen[-1].provider == "stub"


@pytest.mark.parametrize("corrected", [True, False])
def test_contract_repair_is_bounded_and_revalidated(tmp_path, corrected):
    settings = replace(Settings(), provider="xhub", uid="synthetic-authorized", data_dir=tmp_path)
    msg = replace(message(), uid=settings.uid, source="official_chat")
    calls = []

    def reply(request):
        body = json.loads(request.content)
        calls.append(body)
        ids = [msg.key] if corrected and len(calls) == 2 else ["nonexistent-evidence"]
        return response(output(candidate(evidence_ids=ids)))

    client = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-key"), settings,
                          transport=httpx.MockTransport(reply))
    try:
        if corrected:
            assert len(client.extract([msg], [], [])) == 1
        else:
            with pytest.raises(XHubError):
                client.extract([msg], [], [])
        assert len(calls) == 2
        assert len(calls[1]["messages"]) == 4
        assert client.traces[1]["repair_attempt"] is True
    finally:
        client.close()


def test_full_evidence_content_only_uses_matching_message_revision():
    msg = message(text="合成的完整原文，用于验证详情。")
    evidence = dict(source=msg.source, message_id=msg.message_id, digest=msg.digest, excerpt="合成摘要")
    full = enrich_evidence(evidence, {(msg.source, msg.message_id): msg.to_dict()})
    assert full["content"] == msg.content
    revised = replace(msg, content="后来修改的另一段合成原文")
    older = enrich_evidence(evidence, {(msg.source, msg.message_id): revised.to_dict()})
    assert older["content"] is None and older["excerpt"] == evidence["excerpt"]
