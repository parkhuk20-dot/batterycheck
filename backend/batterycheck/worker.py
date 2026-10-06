"""Optional local scheduler. Fetches public notices only; delivers no external messages."""
import argparse
import fcntl
import json
import os
import signal
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .models import ProductInput
from .official import OfficialService
from .store import Store


@contextmanager
def refresh_lock(store):
    """Coordinate one web process and optional workers on macOS/Linux/shared local volume."""
    path = store.path.parent / 'official-refresh.lock'
    with path.open('a') as handle:
        acquired = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
            yield acquired
        finally:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def once(store, official_service):
    """One public sync, then recheck real products for consenting accounts only."""
    with refresh_lock(store) as acquired:
        if not acquired:
            return {'skipped': 'refresh_in_progress', 'rechecked': 0, 'notifications_created': 0}
        source_status = official_service.sync()
        rechecked = 0
        skipped = 0
        notifications_created = 0
        for user_id in store.consenting_users():
            before = len(store.notifications(user_id))
            for saved in store.list(user_id):
                product = ProductInput.model_validate(saved['input'])
                if product.demo:
                    continue
                result = official_service.evaluate(product)
                # The account may have been removed, consent revoked, or input edited during evaluation.
                updated = store.save(product, result, user_id, saved['id'], notify=True,
                                     expected_input=saved['input'], consent_required=True)
                if updated is None:
                    skipped += 1
                else:
                    rechecked += 1
            notifications_created += max(0, len(store.notifications(user_id)) - before)
        return {'source_status': source_status, 'rechecked': rechecked,
                'notifications_created': notifications_created, 'skipped_changed_products': skipped}


def main(argv=None):
    parser = argparse.ArgumentParser(description='배터리체크 선택적 공식 공지 갱신 및 동의 사용자 앱내 알림')
    parser.add_argument('--once', action='store_true', help='한 차례 갱신 후 종료')
    parser.add_argument('--interval', type=int, default=21600, help='정기 갱신 간격(초), 기본 6시간, 최소 60초')
    parser.add_argument('--db', default=os.environ.get('BATTERYCHECK_DB', str(Path(__file__).parents[1] / '.data/batterycheck.sqlite3')))
    args = parser.parse_args(argv)
    if args.interval < 60:
        parser.error('--interval은 60초 이상이어야 합니다.')
    store = Store(args.db)
    service = OfficialService(store.path.parent / 'official')
    stopped = threading.Event()
    for event in (signal.SIGINT, signal.SIGTERM):
        signal.signal(event, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            report = once(store, service)
        except (OSError, ValueError, sqlite3.Error) as exc:
            report = {'ok': False, 'error': type(exc).__name__, 'message': '자료 갱신을 완료하지 못했습니다. 다음 주기에 재시도합니다.'}
        # Never include accounts, passwords, sessions, or product labels in worker logs.
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if args.once:
            return 1 if report.get('ok') is False or report.get('source_status', {}).get('ok') is False else 0
        stopped.wait(args.interval)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
