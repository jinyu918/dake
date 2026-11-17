"""工作台客户列表只来自实际记录，配置默认客户不注入展示。"""
from dake.config import Settings
from dake.services.queries import QueryService


class EmptyStore:
    def __init__(self, **kwargs): pass
    def _execute(self, sql, args=()): return [], 0
    def close(self): pass


def test_empty_database_does_not_inject_default_customer():
    result = QueryService(Settings(uid='synthetic-default'), store_factory=EmptyStore).config()
    assert result['customers'] == []


def test_real_default_customer_has_no_special_sample_label():
    class RealStore(EmptyStore):
        def _execute(self, sql, args=()):
            if sql.startswith('SELECT DISTINCT uid'):
                return [{'uid': 'synthetic-default'}], 1
            if 'COUNT(*)' in sql:
                return [{'total': 0}], 1
            return [], 0
    result = QueryService(Settings(uid='synthetic-default'), store_factory=RealStore).config()
    assert len(result['customers']) == 1
    assert result['customers'][0]['label'] == '客户 synthetic-default'
