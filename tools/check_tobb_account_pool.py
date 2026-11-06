#!/usr/bin/env python3
"""Check fixed existing accounts without registering or rotating identities."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.tobb_accounts import select_account
from collector.tobb_40_shard_pdf_indirici import TOBBSessionManager, TESSERACT_BIN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slots', default=','.join(map(str, range(1, 41))))
    parser.add_argument('--summary', type=Path, default=Path('account_pool_check.json'))
    args = parser.parse_args()
    try:
        slots = [int(value) for value in args.slots.split(',')]
        accounts = [select_account(slot, 40) for slot in slots]
        if not accounts or len(set(slots)) != len(slots):
            raise ValueError('Account check slots must be distinct')
    except ValueError as exc:
        parser.error(str(exc))
    results = []
    for account in accounts:
        manager = TOBBSessionManager(account.email, account.password, TESSERACT_BIN)
        manager.network_gate.interval = manager.minimum_request_interval = 3
        manager.auth_deadline = manager.download_deadline = time.monotonic() + 90
        started = time.monotonic()
        try:
            authenticated = manager.login()
            result = dict(slot=account.slot, account_id=account.identifier,
                          authenticated=authenticated,
                          transport_errors=manager.transport_errors,
                          source_status=manager.source_stopped,
                          http_requests=manager.network_gate.started,
                          elapsed_seconds=round(time.monotonic() - started, 1))
        finally:
            manager.session.close()
        results.append(result)
        args.summary.write_text(json.dumps(results, indent=2))
        print(json.dumps(result), flush=True)
        if manager.source_stopped or (not authenticated and manager.transport_errors):
            print('Source unavailable or refusing access; remaining account checks suspended', flush=True)
            break
    return 0 if len(results) == len(accounts) and all(r['authenticated'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
