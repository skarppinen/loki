import pytest
from pathlib import Path

from conftest import generate_report_handler, generate_linter
from loki.lint.rules import DrHookRule
from loki.frontend import FP


@pytest.fixture(scope='module')
def refpath():
    return Path(__file__).parent / 'dr_hook.f90'


@pytest.mark.parametrize('frontend', [FP])
def test_dr_hook(refpath, frontend):
    handler = generate_report_handler()
    _ = generate_linter(refpath, [DrHookRule], frontend=frontend, handlers=[handler])

    assert len(handler.target.messages) == 9
    assert all(all(keyword in msg for keyword in ('DrHookRule', 'DR_HOOK', '[1.9]'))
               for msg in handler.target.messages)

    assert all('First executable statement must be call to DR_HOOK.' in handler.target.messages[i]
               for i in [0, 4, 6])
    assert all('Last executable statement must be call to DR_HOOK.' in handler.target.messages[i]
               for i in [5, 7])
    assert all('String argument to DR_HOOK call should be "' in handler.target.messages[i]
               for i in [1, 8])
    assert 'Second argument to DR_HOOK call should be "0" or "1".' in handler.target.messages[2]
    assert 'Third argument to DR_HOOK call should be "ZHOOK_HANDLE".' in handler.target.messages[3]

    assert '(l. 39)' in handler.target.messages[1]
    assert '(l. 48)' in handler.target.messages[2]
    assert '(l. 53)' in handler.target.messages[3]
    assert '(l. 116)' in handler.target.messages[8]

    assert all('routine_not_okay_{}'.format(letter) in handler.target.messages[i]
               for letter, i in (('a', 0), ('c', 4), ('c', 5), ('d', 6), ('e', 7)))
