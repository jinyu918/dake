from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from dake.analysis.field_changes import active_facts, field_key
from dake.analysis.updates import snapshot_as_of
from dake.api.app_factory import create_app
from dake.api.schemas import EventResponse
from dake.services.queries import QueryService
from fakes import MemoryStore
from offline_http import TestClient
from test_classification_replay import UID, STREAM, day_window, msg, output_candidate, ref, replay
from test_field_decisions import FieldsProvider, change


def attribute(label, name):
    return dict(kind="attribute", type_label=label, name=name)


def common(name):
    return dict(kind="common", type_label=None, name=name)


def fact(message, label, name, value, stage, **kwargs):
    return change(message, attribute(label, name), value, stage, scope="本次事项",
                  reference=message.message_id + ":" + name + ":" + stage, **kwargs)


def record(message, labels, updates=(), *, old=None, anchor=None, quote=None, **kwargs):
    labels = list(labels)
    item = output_candidate(message, label=labels[0] if labels else "待分类", labels=labels,
        action="update" if old else "new", event_id=old.event_id if old else None,
        old_message=anchor, **kwargs)
    item["field_updates"] = list(updates)
    if quote:
        item["summary"] = quote
        item["basis"][0]["quote"] = quote
        for proof in item["type_labels"]:
            proof["quote"] = quote
        item["changes"] = [dict(kind="progress", text=quote, evidence=[ref(message, quote)], replaces=None)]
    return item


class ReplayStore(MemoryStore):
    def _execute(self, sql, args):
        assert sql == "SELECT source, message_id, payload FROM messages WHERE uid=%s"
        return ([dict(source=m.source, message_id=m.message_id, payload=m.to_dict())
                 for m in self.messages.values() if m.uid == args[0]], 0)


class ReplayQueries(QueryService):
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def scope(self, uid=None, stream=None):
        assert uid == UID and stream == STREAM
        return uid, stream

    @contextmanager
    def connection(self):
        yield self.store


class Replay:
    def __init__(self, path):
        self.service, _, _, self.source = replay(path, [])
        self.service.settings = replace(self.service.settings, ingest_lookback=timedelta(days=20))
        self.store = ReplayStore()
        self.service.store = self.store
        self.service.ingestion.store = self.store
        self.service.history_retriever.store = self.store
        self.service.history_context.store = self.store
        self.provider = FieldsProvider()
        self.service.provider = self.provider
        self.versions = {}
        self.rounds = []
        queries = ReplayQueries(self.store, self.service.settings)
        self.client = TestClient(create_app(self.service.settings, query_service=queries,
            start_worker=False, runner=object(), run_repository=SimpleNamespace(init_schema=lambda: None)))

    def send(self, message, candidates=(), *, day=None):
        self.source.messages = [m for m in self.source.messages if m.key != message.key] + [message]
        self.versions[(message.source, message.message_id, message.digest)] = message
        self.provider.mapping[message.message_id] = list(candidates) if isinstance(candidates, tuple) else candidates
        before = {key: deepcopy(event.updated_by) for key, event in self.store.events.items()}
        result = self.service.run_window(UID, day_window(day or message.sent_at.day))
        for key, history in before.items():
            assert self.store.events[key].updated_by[:len(history)] == history
        for event in self.store.events.values():
            self.check(event)
        self.rounds.append(result)
        return result

    def event(self, event_id=None):
        return self.store.events[event_id] if event_id else next(iter(self.store.events.values()))

    def check(self, event):
        params = dict(uid=UID, stream=STREAM)
        response = self.client.get(f"/api/v1/events/{event.event_id}", params=params)
        assert response.status_code == 200, response.text
        item = response.json()
        serialized = EventResponse.model_validate(event.to_dict()).model_dump()
        for name in ("types", "summary", "attributes", "typed_attributes", "field_states", "field_issues", "recovery", "recurrence_of", "relations"):
            assert item[name] == serialized[name], name
        for label in ("商机", "流失", "排障", "资源需求", "功能需求", "商务需求", "其他", "待分类"):
            response = self.client.get("/api/v1/events", params=dict(params, type=label))
            assert response.status_code == 200, response.text
            ids = {row["event_id"] for row in response.json()["items"]}
            assert (event.event_id in ids) == (label in (event.types or [event.type]))
        proofs = [proof for state in event.field_states.values() for value in state["facts"] for proof in value["evidence"]]
        proofs += [proof for values in event.type_evidence.values() for value in values for proof in value.get("evidence_refs", [])]
        for proof in proofs:
            source = self.versions[(proof["source"], proof["message_id"], proof["digest"])]
            assert proof["quote"] in source.content
            assert proof["send_time"] == source.sent_at.isoformat()
        for label, attributes in event.typed_attributes.items():
            assert label in event.types
            for name, value in attributes.items():
                assert event.attributes.get(name) == value["value"]
        assert event.severity == "未知" and event.assignees == []

    def assert_current(self, labels, *, count=1, event_id=None, expected=(), absent=()):
        assert len(self.store.events) == count
        event = self.event(event_id)
        assert set(event.types) == set(labels)
        for label, name, value in expected:
            assert event.typed_attributes[label][name]["value"] == value
            assert event.attributes[name] == value
        for label, name in absent:
            assert name not in event.typed_attributes.get(label, {})
            assert name not in event.attributes
        return event

    def adopted(self, event, target):
        return active_facts(event.field_states[field_key(target)])

    def old(self, event, day):
        value = snapshot_as_of(event, day_window(day).end)
        assert value == self.rounds[day - 1].events[0]
        return value
