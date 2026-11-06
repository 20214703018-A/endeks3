import json

import pytest

from collector.tobb_accounts import select_account


def pool():
    return {str(i): {'email': f'account{i}@example.test', 'password': f'secret-{i}'}
            for i in range(1, 41)}


def test_forty_shards_receive_forty_fixed_distinct_accounts():
    config = json.dumps(pool())
    accounts = [select_account(i, 40, accounts_json=config) for i in range(1, 41)]
    assert len({a.identifier for a in accounts}) == 40
    assert [(a.email, a.password, a.slot) for a in accounts] == [
        (f'account{i}@example.test', f'secret-{i}', i) for i in range(1, 41)]
    assert all(a.pooled for a in accounts)
    assert select_account(5, 40, accounts_json=config) == accounts[4]


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'case_duplicate', 'password', 'wrong_shape'])
def test_invalid_pool_cannot_fall_back_to_shared_identity(change):
    config = pool()
    if change == 'missing':
        config.pop('40')
    elif change == 'duplicate':
        config['40'] = config['1']
    elif change == 'case_duplicate':
        config['40']['email'] = config['1']['email'].upper()
    elif change == 'password':
        config['40']['password'] = ''
    else:
        config['40'] = 'private-credential'
    with pytest.raises(ValueError) as exc:
        select_account(1, 40, accounts_json=json.dumps(config), legacy=('legacy@example.test', 'legacy-secret'))
    assert 'secret' not in str(exc.value)
    assert '@' not in str(exc.value)
    assert 'private-credential' not in str(exc.value)


def test_parse_error_and_account_repr_do_not_disclose_credentials():
    with pytest.raises(ValueError) as exc:
        select_account(1, 40, accounts_json='private-credential')
    assert 'private-credential' not in str(exc.value)
    account = select_account(1, 40, accounts_json=json.dumps(pool()))
    assert account.email not in repr(account)
    assert account.password not in repr(account)


def test_configured_pool_is_used_from_environment(monkeypatch):
    monkeypatch.setenv('TOBB_ACCOUNTS_JSON', json.dumps(pool()))
    assert select_account(40, 40).email == 'account40@example.test'


def test_legacy_mode_remains_explicit_for_unconfigured_runs(monkeypatch):
    monkeypatch.delenv('TOBB_ACCOUNTS_JSON', raising=False)
    account = select_account(1, 40, legacy=('legacy@example.test', 'legacy-secret'))
    assert not account.pooled


def test_production_cannot_silently_revert_to_shared_account(monkeypatch):
    monkeypatch.setenv('TOBB_REQUIRE_ACCOUNT_POOL', '1')
    with pytest.raises(ValueError, match='distinct TOBB account pool is required'):
        select_account(1, 40, accounts_json='', legacy=('legacy@example.test', 'legacy-secret'))
