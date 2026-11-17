"""验证月销等级边界，分类不依赖群、负责人或事件等级。"""

import pytest

from dake.domain.customer_policy import customer_segment, is_super_large


@pytest.mark.parametrize('number', range(11))
def test_published_levels_include_l10(number):
    assert customer_segment(f'L{number}', 'fresh') == ('large' if number >= 6 else 'non_large')
    assert is_super_large(f'L{number}', 'fresh') is (number >= 9)
    assert is_super_large(f'L{number}', 'stale') is False


@pytest.mark.parametrize('level', [None, '', 'L11', 'L06', 'l6', '6', 'L-1'])
def test_unknown_or_invalid_level_never_becomes_non_large(level):
    assert customer_segment(level, 'fresh') == 'pending'


@pytest.mark.parametrize('state', ['unknown', 'error', 'stale', 'unexpected'])
@pytest.mark.parametrize('level', ['L0', 'L5', 'L6', 'L10', None])
def test_failed_or_unknown_status_does_not_reuse_old_level_for_admission(level, state):
    assert customer_segment(level, state) == 'pending'
