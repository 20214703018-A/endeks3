"""Fixed assignment of existing TOBB accounts to download shards."""
import hashlib
import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TOBBAccount:
    email: str = field(repr=False)
    password: str = field(repr=False)
    pooled: bool
    slot: int

    @property
    def identifier(self):
        return hashlib.sha256(self.email.casefold().encode()).hexdigest()[:12]


def select_account(shard, total, *, accounts_json=None, legacy=None):
    """Validate the entire pool and bind one identity for the whole run.

    Invalid pools fail closed. Authentication failures never select another
    account, and this module never registers an account.
    """
    if not isinstance(shard, int) or not isinstance(total, int) or not 1 <= shard <= total:
        raise ValueError('Invalid account shard allocation')
    if accounts_json is None:
        accounts_json = os.environ.get('TOBB_ACCOUNTS_JSON', '')
    if not accounts_json:
        if os.environ.get('TOBB_REQUIRE_ACCOUNT_POOL') == '1':
            raise ValueError('A distinct TOBB account pool is required for this workflow')
        if not legacy or len(legacy) != 2 or not all(isinstance(v, str) and v for v in legacy):
            raise ValueError('TOBB account configuration is missing')
        return TOBBAccount(*legacy, pooled=False, slot=shard)
    try:
        accounts = json.loads(accounts_json)
    except (ValueError, TypeError):
        raise ValueError('TOBB_ACCOUNTS_JSON must be a valid account pool') from None
    if not isinstance(accounts, dict) or set(accounts) != {str(i) for i in range(1, total + 1)}:
        raise ValueError('TOBB account pool must contain exactly one slot per shard')
    seen = set()
    validated = {}
    for slot, account in accounts.items():
        if not isinstance(account, dict):
            raise ValueError('Invalid TOBB account entry')
        email, password = account.get('email'), account.get('password')
        if (not isinstance(email, str) or not email.strip() or '@' not in email
                or not isinstance(password, str) or not password):
            raise ValueError('Each TOBB account requires an email and password')
        email = email.strip()
        if email.casefold() in seen:
            raise ValueError('TOBB account pool contains duplicate identities')
        seen.add(email.casefold())
        validated[slot] = TOBBAccount(email, password, pooled=True, slot=int(slot))
    return validated[str(shard)]
