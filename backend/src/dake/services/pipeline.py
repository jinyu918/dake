"""一次分析窗口的执行权。模型调用发生在持有它的短事务之外。"""


class WindowLease:
    """提交时把归属、代次和前驱水位交给当前业务事务校验。"""

    def __init__(self, pipeline, claim):
        self.pipeline = pipeline
        self.claim = claim

    def isolate(self, store, records, window):
        self.pipeline.isolate(store, records, window)

    def fence(self, store, *, incomplete):
        self.pipeline.fence(store, self.claim, incomplete=incomplete)

    def block(self, kind, reason):
        self.pipeline.block(self.claim["uid"], self.claim["owner"], self.claim["epoch"], kind, reason)
