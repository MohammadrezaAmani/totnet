#!/usr/bin/env python3
"""Carefully reconcile the current checkout with the new subscription selector.

This is intentionally an explicit, backup-first operation.  The accompanying
Git patch only *adds* this tool and reference files; running git apply alone
never overwrites project runtime code.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / '.purchase_repair_payload'
PICKER = ROOT / 'apps/bot/handlers/plan_picker.py'
PURCHASE = ROOT / 'apps/bot/handlers/purchase.py'
PICKER_TESTS = ROOT / 'apps/bot/test_plan_picker.py'
BOT_TESTS = ROOT / 'apps/bot/tests.py'
POLICY_IMPORT = 'from apps.subscriptions.policy import TEMPORARILY_UNAVAILABLE_TEXT, temporary_duration_blocked\n'


def patch_purchase(data: str) -> str:
    """Only fix missing policy symbol; preserve other purchase-handler edits."""
    from_policy = re.compile(r'^from apps\.subscriptions\.policy import ([^\n]+)$', re.M)
    match = from_policy.search(data)
    if match:
        fields = {item.strip() for item in match.group(1).split(',')}
        missing = ['TEMPORARILY_UNAVAILABLE_TEXT', 'temporary_duration_blocked']
        fields.update(missing)
        return data[:match.start()] + 'from apps.subscriptions.policy import ' + ', '.join(sorted(fields)) + data[match.end():]
    marker = re.compile('^from apps\\.subscriptions\\.presentation import [^\\n]+\\n', re.M)
    match = marker.search(data)
    if not match:
        raise RuntimeError('purchase.py import layout unknown; refuse automatic edit')
    return data[:match.end()] + POLICY_IMPORT + data[match.end():]


def patch_integration_test(data: str) -> str:
    """Update only the obsolete assertion about the first button in new picker.

    This change is valid because selecting category now starts filters, rather
    than showing an immediate select_plan button.  The rest of the end-to-end
    test continues to confirm real order creation/payment/activation.
    """
    pattern = re.compile(
        r'(?P<indent>^[ \t]*)self\.assertEqual\(\s*keyboard\.inline_keyboard\[0\]\[0\]\.callback_data,\s*'
        r'f[\'\"]select_plan_\{plan\.pk\}[\'\"]\s*\)', re.M,
    )
    replacement = (
        'self.assertTrue(any(\n'
        '{i}    button.callback_data and button.callback_data.endswith(":all:")\n'
        '{i}    for row in keyboard.inline_keyboard for button in row\n'
        '{i}))'
    )
    return pattern.sub(lambda m: m.group('indent') + replacement.format(i=m.group('indent')), data)


def validate_content(name: str, text: str) -> None:
    try:
        ast.parse(text, filename=name)
    except SyntaxError as exc:
        raise RuntimeError(f'{name} has invalid syntax after reconciliation: {exc}') from exc


def atomic_replace(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='review expected changes; write nothing')
    mode.add_argument('--apply', action='store_true', help='back up and apply the repairs')
    parser.add_argument('--sync-regression-tests', action='store_true',
                        help='replace the obsolete plan-picker test module with current-spec regression tests (backup kept)')
    args = parser.parse_args()
    if not (ROOT / 'manage.py').is_file():
        parser.error('must run inside a Django project containing manage.py')
    required = (PICKER, PURCHASE, BOT_TESTS, PICKER_TESTS)
    if not all(p.is_file() for p in required):
        parser.error('some expected Django/aiogram source files are missing')
    current = {p: p.read_text(encoding='utf-8') for p in required}
    target = dict(current)
    target[PICKER] = (PAYLOAD / 'plan_picker.py').read_text(encoding='utf-8')
    target[PURCHASE] = patch_purchase(current[PURCHASE])
    target[BOT_TESTS] = patch_integration_test(current[BOT_TESTS])
    if args.sync_regression_tests:
        target[PICKER_TESTS] = (PAYLOAD / 'test_plan_picker.py').read_text(encoding='utf-8')
    changed = [p for p in required if target[p] != current[p]]
    for path in changed:
        validate_content(str(path.relative_to(ROOT)), target[path])
    print(f'Changed files: {len(changed)}')
    for path in changed:
        old, new = current[path].splitlines(keepends=True), target[path].splitlines(keepends=True)
        print(f'  {path.relative_to(ROOT)}  ({len(old)} -> {len(new)} lines)')
        if args.check:
            excerpt = list(difflib.unified_diff(old, new, fromfile='current', tofile='proposed', n=2))
            print(''.join(excerpt[:75]), end='')
            if len(excerpt) > 75:
                print(f'  ... {len(excerpt)-75} more diff lines')
    if args.check:
        if not args.sync_regression_tests:
            print('NOTE: Legacy plan-picker tests may continue to assert obsolete stage names.')
            print('Use --sync-regression-tests only after reviewing the new purchase contract.')
        return 0
    if not changed:
        print('Nothing to change: already reconciled.')
        return 0
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = ROOT / '.purchase-repair-backups' / timestamp
    for path in changed:
        destination = backup / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    print(f'Backed up original files to {backup}')
    for path in changed:
        atomic_replace(path, target[path])
    print('Files reconciled. Next: python -m compileall -q apps/bot && run Django tests.')
    if not args.sync_regression_tests:
        print('NOTE: run again with --apply --sync-regression-tests if legacy picker tests are obsolete.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as err:
        print(f'ERROR: {err}', file=sys.stderr)
        sys.exit(2)
