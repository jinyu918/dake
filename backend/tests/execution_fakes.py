"""执行记录故障注入替身；阶段独立保存，收据与业务一起提交或回滚。"""

from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

from dake.repositories.attempts import AttemptError
from dake.repositories.mysql import TransactionOutcomeUnknown
from fakes import MemoryStore


class MemoryAttempts:
    def __init__(self):
        self.items = {}
        self.fail_stage = None
        self.fail_receipt = False
        self.fail_failure = False
        self.fail_delivery = False
        self.fail_confirm = False

    def get(self, uid, stream, attempt_id):
        record = self.items.get(attempt_id)
        return deepcopy(record) if record and (record["uid"], record["stream"]) == (uid, stream) else None

    def unresolved(self, uid, stream):
        return next((rid for rid, item in self.items.items() if (item["uid"], item["stream"]) == (uid, stream)
            and item["outcome"] in {"running", "commit_unknown"}), None)

    def for_run_window(self, uid, stream, run_id, window_index, window):
        return next((deepcopy(item) for item in reversed(list(self.items.values())) if
            (item["uid"], item["stream"], item["run_id"], item["window_index"], item["window_start"], item["window_end"]) ==
            (uid, stream, run_id, window_index, window.start.isoformat(), window.end.isoformat())), None)

    def create(self, uid, stream, window, **identity):
        attempt_id = str(uuid4())
        self.items[attempt_id] = dict(attempt_id=attempt_id, uid=uid, stream=stream, run_id=None, window_index=None,
            retry_of=None, **{k: v for k, v in identity.items() if k not in {"run_id", "window_index", "retry_of"}},
            window_start=window.start.isoformat(), window_end=window.end.isoformat(), outcome="running", stages=[], receipt=None, delivery={})
        self.items[attempt_id].update({k: v for k, v in identity.items() if k in {"run_id", "window_index", "retry_of"}})
        return attempt_id

    def stage(self, uid, stream, attempt_id, stage, status, payload):
        assert self.get(uid, stream, attempt_id) is not None
        if self.fail_stage == stage:
            raise AttemptError("虚构阶段记录失败")
        self.items[attempt_id]["stages"].append(dict(stage=stage, status=status, payload=deepcopy(payload)))

    def write_receipt(self, store, uid, stream, attempt_id, receipt):
        store._require(uid)
        assert self.get(uid, stream, attempt_id) is not None
        store.staged_receipt = (attempt_id, deepcopy(receipt))
        if self.fail_receipt:
            raise AttemptError("虚构收据写入失败")

    def failure(self, uid, stream, attempt_id, *, unknown, error_type, model_unknown=False):
        assert self.get(uid, stream, attempt_id) is not None
        if unknown and model_unknown:
            raise AttemptError("同一次失败不能同时是提交不明和模型不明")
        if self.fail_failure:
            raise AttemptError("虚构结束记录失败")
        item = self.items[attempt_id]
        if model_unknown and item["receipt"] is None:
            item.update(outcome="model_unknown", error_type=error_type)
        elif unknown or item["receipt"] is None:
            item.update(outcome="commit_unknown" if unknown else "not_committed", error_type=error_type)

    def verified(self, uid, stream, attempt_id, *, committed):
        assert self.get(uid, stream, attempt_id) is not None
        outcome = "committed" if committed else "not_committed"
        self.items[attempt_id]["outcome"] = outcome
        self.items[attempt_id]["stages"].append(dict(stage="verification", status="completed", payload=dict(outcome=outcome)))

    def confirm_commit(self, uid, stream, attempt_id):
        assert self.get(uid, stream, attempt_id)["receipt"] is not None
        if self.fail_confirm:
            raise AttemptError("虚构提交确认记录失败")
        self.items[attempt_id]["outcome"] = "committed"

    def delivery(self, uid, stream, attempt_id, destination, status):
        assert self.get(uid, stream, attempt_id)["receipt"] is not None
        if self.fail_delivery:
            raise AttemptError("虚构展示状态记录失败")
        self.items[attempt_id]["delivery"][destination] = dict(status=status)


class RecordingStore(MemoryStore):
    def __init__(self, attempts):
        super().__init__()
        self.attempts = attempts
        self.staged_receipt = None
        self.commit_fault = None
        self._customer_uid = None

    @contextmanager
    def customer_lock(self, uid):
        assert self._customer_uid in {None, uid}
        previous = self._customer_uid
        self._customer_uid = uid
        try:
            yield self
        finally:
            self._customer_uid = previous

    @contextmanager
    def transaction(self, uid):
        self.staged_receipt = None
        try:
            with super().transaction(uid):
                yield self
                if self.commit_fault == "before":
                    raise TransactionOutcomeUnknown("虚构提交结果不明")
                if self.staged_receipt:
                    attempt_id, receipt = self.staged_receipt
                    self.attempts.items[attempt_id].update(receipt=deepcopy(receipt))
            if self.commit_fault == "after":
                raise TransactionOutcomeUnknown("虚构提交已成功但响应丢失")
        finally:
            self.staged_receipt = None
