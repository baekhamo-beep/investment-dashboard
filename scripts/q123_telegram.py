"""Completed-close signals and exchange-calendar pre-open Telegram notices."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

import exchange_calendars as xc
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from q123_engine import ASSETS, VERSION
from scripts.telegram_client import Telegram, TelegramError

SITE = 'https://investment-dashboard-drab-kappa.vercel.app/q123.html'
RECEIPTS = ROOT / 'state/q123_alert_receipts.json'


def calendar_at(now):
    return xc.get_calendar('XNYS', start=f'{now.year - 2}-01-01',
                           end=f'{now.year + 2}-12-31')


def due_events(calendar, now):
    """No pre-open notice after open; failed/delayed jobs can catch up before open."""
    schedule = calendar.schedule
    completed = schedule.loc[schedule['close'] <= now]
    upcoming = schedule.loc[schedule['open'] > now]
    last_day, last_row = completed.index[-1], completed.iloc[-1]
    next_day, next_row = upcoming.index[0], upcoming.iloc[0]
    preopen = next_row['open'] - pd.Timedelta(minutes=30) <= now < next_row['open']
    # The latest close must belong to today in New York. Covers early closes too.
    afterclose = last_row['close'].tz_convert('America/New_York').date() == now.tz_convert('America/New_York').date()
    return {'preopen': bool(preopen), 'afterclose': bool(afterclose),
            'as_of': str(last_day.date()), 'session': str(next_day.date()),
            'open': next_row['open']}


def validate_state(state, due):
    if state.get('version') != VERSION or state.get('as_of') != due['as_of']:
        raise ValueError('Latest completed-session Q123 state is unavailable')
    for mode, asset in [('mode', 'asset'), ('target_mode', 'target_asset')]:
        if state.get(mode) not in ASSETS or state.get(asset) != ASSETS[state[mode]]:
            raise ValueError('Q123 asset mapping is invalid')
    if state.get('next_session') != due['session'] or pd.Timestamp(state.get('next_open_utc')) != due['open']:
        raise ValueError('Q123 next-session calendar does not match')
    for name in ['close', 'sma50', 'sma200', 'ret63', 'ret126']:
        value = state.get('indicators', {}).get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Q123 indicator is incomplete')
    if state['mode'] != state['target_mode'] and state.get('signal_date') != due['as_of']:
        raise ValueError('Q123 transition is not a latest-close signal')


def load_alert_state(calendar, now, fetcher=None, cache_path=None):
    """Use a completed-close snapshot only after calendar and freshness checks."""
    due = due_events(calendar, now)
    try:
        if fetcher is None:
            from fetch_data import get_q123_state
            fetcher = get_q123_state
        state = fetcher()
        validate_state(state, due)
        print('Q123 data source: live download.')
        return state
    except Exception as exc:
        # Log the exception type only: upstream errors may contain request secrets.
        print(f'Live Q123 unavailable ({type(exc).__name__}); checking saved snapshot.')
    path = cache_path if cache_path is not None else ROOT / 'docs/data.json'
    state = json.loads(Path(path).read_text())['q123']
    validate_state(state, due)
    stamp = pd.Timestamp(state.get('updated_utc'))
    completed_close = calendar.schedule.loc[
        calendar.schedule['close'] <= now, 'close'].iloc[-1]
    if pd.isna(stamp) or stamp.tzinfo is None or not completed_close <= stamp <= now:
        raise ValueError('Saved Q123 snapshot timestamp is invalid')
    print(f"Q123 data source: validated saved snapshot; as_of={state['as_of']}.")
    return state


def korean_time(stamp):
    return stamp.tz_convert('Asia/Seoul').strftime('%m/%d %H:%M')


def utc_now():
    return pd.Timestamp.now(tz='UTC')


def wait_for_preopen(calendar, now, clock=utc_now, sleep=time.sleep):
    """An early runner waits locally; other checks never wait more than 30 minutes."""
    due = due_events(calendar, now)
    target = due['open'] - pd.Timedelta(minutes=30)
    remaining = (target - now).total_seconds()
    if not 0 < remaining <= 1800:
        return now
    print(f'Runner ready; waiting for pre-open notice at {target.isoformat()}.', flush=True)
    waited = 0
    while now < target and waited < 1800:
        seconds = min(30, (target - now).total_seconds(), 1800 - waited)
        sleep(seconds)
        waited += seconds
        now = clock()
    return now


def missed_preopen_messages(calendar, now, journal):
    """Report today's missing notice without sending an expired trading target."""
    opened = calendar.schedule.loc[calendar.schedule['open'] <= now]
    day, row = opened.index[-1], opened.iloc[-1]
    if row['open'].tz_convert('America/New_York').date() != now.tz_convert('America/New_York').date():
        return []
    session = str(day.date())
    if journal.sent(f'preopen:{session}'):
        return []
    return [(f'preopen-missed:{session}',
             '⚠️ Q123 개장 전 알림 누락 확인\n'
             f'미국 거래일: {session}\n'
             f"예정 알림: {korean_time(row['open'] - pd.Timedelta(minutes=30))} 한국시간\n"
             f"개장: {korean_time(row['open'])} 한국시간\n"
             '개장 전에 정상 알림을 전송한 기록이 없습니다.\n'
             '예약 실행 지연·누락 또는 전송 실패 여부를 점검해야 합니다.\n'
             '이미 개장했으므로 지난 시가 매매 목표를 뒤늦게 보내지 않습니다.\n'
             '이 메시지는 매매 신호가 아닌 알림 상태 안내입니다.\n' + SITE)]


def messages(state, due, now):
    notices = []
    switching = state['mode'] != state['target_mode']
    opening = korean_time(due['open'])
    if switching and (due['afterclose'] or due['preopen']):
        identity = f"transition:{state['signal_date']}:{state['mode']}:{state['target_mode']}"
        notices.append((identity,
            '🔔 Q123 전환 신호 확정\n'
            f"모델 상태: {state['mode']} → {state['target_mode']}\n"
            f"목표 자산: {state['target_asset']} 100%\n"
            f"신호일(미국): {state['signal_date']} 종가\n"
            f"근거: {state['reason']}\n"
            f'다음 거래일 시가: {opening} 한국시간\n'
            '실제 계좌의 주문·체결을 확인한 알림이 아닙니다.\n' + SITE))
    if due['preopen']:
        elapsed = int((now - (due['open'] - pd.Timedelta(minutes=30))).total_seconds() / 60)
        heading = '⏰ Q123 개장 전 알림'
        if elapsed >= 5:
            heading += f' (예약 실행 {elapsed}분 지연)'
        action = f"전환 신호 있음: {state['mode']} → {state['target_mode']}" if switching else 'HOLD · 모델 상태 유지'
        notices.append((f"preopen:{due['session']}",
            heading + '\n'
            f"미국 거래일: {due['session']}\n"
            f'개장: {opening} 한국시간\n'
            f"종가 기준: {state['as_of']}\n"
            f"모델 상태: {state['mode']} / {state['asset']}\n"
            f"오늘 시가 목표: {state['target_mode']} / {state['target_asset']} 100%\n"
            f'{action}\n'
            f"회복 확인 {min(state['above_days'], 5)}/5거래일 · "
            f"BOOST 보유 {state['boost_days']}거래일 · 쿨다운 {state['cooldown_remaining']}거래일\n"
            '계좌 보유·시가·실제 주문은 직접 확인해 주세요.\n' + SITE))
    return notices


class Journal:
    def __init__(self, path=RECEIPTS):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {'version': 1, 'sent': {}}
        if self.data.get('version') != 1 or not isinstance(self.data.get('sent'), dict):
            raise ValueError('Alert receipt journal is invalid')

    @staticmethod
    def key(identity):
        return hashlib.sha256(identity.encode()).hexdigest()

    def sent(self, identity):
        return self.key(identity) in self.data['sent']

    def record(self, identity, now):
        self.data['sent'][self.key(identity)] = now.isoformat()
        cutoff = now - pd.Timedelta(days=180)
        self.data['sent'] = {k: v for k, v in self.data['sent'].items() if pd.Timestamp(v) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2, sort_keys=True) + '\n')
        temporary.replace(self.path)


def deliver(client, chat_id, notices, journal, now):
    for identity, message in notices:
        if journal.sent(identity):
            continue
        client.send(chat_id, message)
        journal.record(identity, now)  # Persist only acknowledged sends, even if later sends fail.
        print('Telegram notice acknowledged; receipt persisted.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--wait-for-preopen', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        print('Telegram setup pending: configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.')
        return 1
    print(f'::add-mask::{chat_id}')
    client = Telegram(token)
    try:
        if args.test:
            client.send(chat_id, '✅ Q123 알림 수신 테스트\n'
                        'Telegram 전송 연결을 확인했습니다.\n'
                        '예약 실행과 정시 도착 여부는 별도로 확인해야 합니다.\n'
                        '알림 목표: 전환 신호 확정 시 및 매 거래일 개장 30분 전.\n'
                        '미국 휴장일 제외·서머타임 자동 반영.\n'
                        'GitHub 예약 실행 상황에 따라 발송이 지연될 수 있습니다.\n' + SITE)
            print('Test message acknowledged.')
            return 0
        now = utc_now()
        calendar = calendar_at(now)
        if args.wait_for_preopen:
            now = wait_for_preopen(calendar, now)
        due = due_events(calendar, now)
        print(f"Alert check UTC={now.isoformat()} preopen={due['preopen']} afterclose={due['afterclose']}")
        journal = Journal()
        deliver(client, chat_id, missed_preopen_messages(calendar, now, journal), journal, now)
        if not due['preopen'] and not due['afterclose']:
            print('Outside completed-close and pre-open notification windows.')
            return 0
        # Heavy price collection is done only when a notification window is active.
        try:
            state = load_alert_state(calendar, now)
            # Price collection can cross the open: never send a stale pre-open order target.
            now = utc_now()
            due = due_events(calendar, now)
            if due['preopen'] or due['afterclose']:
                validate_state(state, due)
        except Exception:
            deliver(client, chat_id, [(f"data-error:{due['session']}",
                '⚠️ Q123 데이터 확인 필요\n'
                '최신 완료 거래일의 데이터를 확인하지 못했습니다.\n'
                '이번 점검에서는 정상 상태나 매매 목표를 안내하지 않습니다.\n' + SITE)], journal, now)
            print('Fresh Q123 state validation failed; no normal signal was sent.')
            return 1
        deliver(client, chat_id, missed_preopen_messages(calendar, now, journal), journal, now)
        if not due['preopen'] and not due['afterclose']:
            print('Notification window closed during price collection; no expired target sent.')
            return 0
        deliver(client, chat_id, messages(state, due, now), journal, now)
        print('Q123 notification check complete.')
    except (TelegramError, ValueError):
        print('Notification failed. Check Telegram connection or receipt journal; no credential values are logged.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
