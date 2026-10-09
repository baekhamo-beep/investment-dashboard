import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from scripts.telegram_client import Telegram, TelegramError
from scripts.setup_q123_telegram import discover_chat
from scripts.q123_telegram import (calendar_at, due_events, validate_state, messages,
                                  Journal, deliver, missed_preopen_messages,
                                  wait_for_preopen, load_alert_state, main,
                                  data_failure_messages, recovery_messages, needs_recovery)


class TelegramTests(unittest.TestCase):
    def state(self, due, mode='NORMAL', target='NORMAL'):
        from q123_engine import ASSETS, VERSION
        return {'version': VERSION, 'as_of': due['as_of'], 'next_session': due['session'],
                'next_open_utc': due['open'].isoformat(), 'mode': mode, 'asset': ASSETS[mode],
                'target_mode': target, 'target_asset': ASSETS[target],
                'signal_date': due['as_of'] if mode != target else None, 'reason': 'fixture',
                'above_days': 10, 'boost_days': 0, 'cooldown_remaining': 0,
                'indicators': {'close': 300, 'sma50': 250, 'sma200': 200, 'ret63': .15, 'ret126': .2}}

    def due(self, value):
        now = pd.Timestamp(value)
        return now, due_events(calendar_at(now), now)


    def test_data_warning_grace_persistence_and_recovery(self):
        now, due = self.due('2026-10-08T20:05:00Z')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.json'
            journal = Journal(path)
            self.assertEqual(data_failure_messages(journal, due, now), [])
            journal = Journal(path)
            self.assertTrue(needs_recovery(journal, due))
            self.assertEqual(data_failure_messages(journal, due, now + pd.Timedelta(minutes=29)), [])
            notices = data_failure_messages(journal, due, now + pd.Timedelta(minutes=30))
            self.assertEqual(len(notices), 1)
            client = Mock()
            deliver(client, 'test', notices, journal, now)
            deliver(client, 'test', notices, journal, now)
            self.assertEqual(client.send.call_count, 1)
            recovered = recovery_messages(journal, self.state(due), due)
            self.assertIn('[시스템 복구]', recovered[0][1])
            deliver(client, 'test', recovered, journal, now)
            deliver(client, 'test', recovered, journal, now)
            self.assertEqual(client.send.call_count, 2)

    def test_preopen_failure_is_immediate_and_silent_failures_do_not_recover_notice(self):
        now, due = self.due('2026-10-09T13:00:00Z')
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'journal.json')
            notices = data_failure_messages(journal, due, now)
            self.assertEqual(len(notices), 1)
            self.assertIn('개장 전 판단 불가', notices[0][1])
            self.assertEqual(recovery_messages(journal, self.state(due), due), [])
            journal.record(notices[0][0], now)
            self.assertEqual(len(recovery_messages(journal, self.state(due), due)), 1)


    def test_legacy_warning_recovers_outside_trading_window(self):
        now, due = self.due('2026-10-09T06:15:00Z')
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'journal.json')
            journal.record(f"data-error:{due['session']}", now)
            client = Mock()
            with patch('scripts.q123_telegram.utc_now', return_value=now), \
                 patch('scripts.q123_telegram.Journal', return_value=journal), \
                 patch('scripts.q123_telegram.Telegram', return_value=client), \
                 patch('scripts.q123_telegram.load_alert_state', return_value=self.state(due)), \
                 patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'fixture', 'TELEGRAM_CHAT_ID': 'fixture-id'}), \
                 patch.object(sys, 'argv', ['q123_telegram.py']):
                self.assertEqual(main(), 0)
                self.assertEqual(main(), 0)
            self.assertEqual(client.send.call_count, 1)
            self.assertIn('[시스템 복구]', client.send.call_args.args[1])

    def test_saved_snapshot_fallback_and_rejection(self):
        now, due = self.due('2026-10-08T00:48:00Z')
        cal = calendar_at(now)
        good = self.state(due)
        good['updated_utc'] = '2026-10-07T23:55:33Z'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            path.write_text(json.dumps({'q123': good}))
            failed = Mock(side_effect=ValueError('provider missing latest bar'))
            self.assertEqual(load_alert_state(cal, now, failed, path), good)
            self.assertEqual(load_alert_state(cal, now, lambda: good, Path('absent')), good)
            for changes in [
                {'as_of': '2026-10-06'},
                {'next_session': '2026-10-09'},
                {'version': 'obsolete'},
                {'updated_utc': '2026-10-07T19:59:00Z'},
                {'updated_utc': '2026-10-08T01:00:00Z'},
                {'updated_utc': None},
                {'updated_utc': '2026-10-07T23:55:33'},
                {'target_asset': 'INVALID'},
            ]:
                with self.subTest(changes=changes):
                    path.write_text(json.dumps({'q123': {**good, **changes}}))
                    with self.assertRaises(ValueError):
                        load_alert_state(cal, now, failed, path)

    def test_summer_winter_and_open_boundary(self):
        for value, opening in [('2026-10-06T13:00:00Z', '2026-10-06T13:30:00Z'),
                               ('2027-01-06T14:00:00Z', '2027-01-06T14:30:00Z')]:
            now, due = self.due(value)
            self.assertTrue(due['preopen'])
            self.assertEqual(due['open'], pd.Timestamp(opening))
            _, atopen = self.due(opening)
            self.assertFalse(atopen['preopen'])
            _, early = self.due((now - pd.Timedelta(seconds=1)).isoformat())
            self.assertFalse(early['preopen'])

    def test_weekends_holidays_and_early_close(self):
        for value in ['2026-10-10T13:00:00Z', '2026-11-26T14:00:00Z']:
            _, due = self.due(value)
            self.assertFalse(due['preopen'])
            self.assertFalse(due['afterclose'])
        _, due = self.due('2026-11-27T18:07:00Z')
        self.assertTrue(due['afterclose'])
        self.assertEqual(due['as_of'], '2026-11-27')
        self.assertEqual(due['session'], '2026-11-30')

    def test_latest_completed_close_required(self):
        _, due = self.due('2026-10-06T13:00:00Z')
        s = self.state(due)
        validate_state(s, due)
        s['as_of'] = '2026-10-02'
        with self.assertRaises(ValueError):
            validate_state(s, due)
        s = self.state(due)
        s['next_open_utc'] = '2026-10-06T14:30:00Z'
        with self.assertRaises(ValueError):
            validate_state(s, due)
        s = self.state(due)
        s['indicators']['ret63'] = float('nan')
        with self.assertRaises(ValueError):
            validate_state(s, due)

    def test_hold_daily_and_all_transition_directions(self):
        now, due = self.due('2026-10-06T13:00:00Z')
        hold = messages(self.state(due), due, now)
        self.assertEqual(len(hold), 1)
        self.assertIn('HOLD', hold[0][1])
        for old, new in [('NORMAL', 'BEAR'), ('NORMAL', 'BOOST'), ('BOOST', 'NORMAL'),
                         ('BOOST', 'BEAR'), ('BEAR', 'NORMAL'), ('BEAR', 'BOOST')]:
            s = self.state(due, old, new)
            validate_state(s, due)
            notices = messages(s, due, now)
            self.assertEqual(len(notices), 2)
            self.assertIn(f'{old} → {new}', notices[0][1])
            self.assertIn(due['as_of'], notices[0][0])

    def test_after_close_transition_only_and_delay_label(self):
        now, due = self.due('2026-10-06T20:07:00Z')
        self.assertEqual(messages(self.state(due), due, now), [])
        self.assertEqual(len(messages(self.state(due, target='BOOST'), due, now)), 1)
        now, due = self.due('2026-10-06T13:12:00Z')
        self.assertIn('12분 지연', messages(self.state(due), due, now)[0][1])

    def test_early_runner_waits_for_summer_and_winter_target(self):
        for value, target in [('2026-10-06T12:59:15Z', '2026-10-06T13:00:00Z'),
                              ('2027-01-06T13:59:15Z', '2027-01-06T14:00:00Z')]:
            now = pd.Timestamp(value)
            expected = pd.Timestamp(target)
            clock = Mock(side_effect=[expected - pd.Timedelta(seconds=15), expected])
            sleep = Mock()
            self.assertEqual(wait_for_preopen(calendar_at(now), now, clock, sleep), expected)
            self.assertEqual([x.args[0] for x in sleep.call_args_list], [30, 15])

    def test_wait_never_blocks_far_early_holidays_or_after_open(self):
        for value in ['2026-10-06T11:00:00Z', '2026-10-10T12:50:00Z',
                      '2026-11-26T13:50:00Z', '2026-10-06T13:30:00Z']:
            now = pd.Timestamp(value)
            sleep = Mock()
            self.assertEqual(wait_for_preopen(calendar_at(now), now, Mock(), sleep), now)
            sleep.assert_not_called()

    def test_missing_preopen_warns_once_and_never_sends_expired_target(self):
        now = pd.Timestamp('2026-10-06T14:05:00Z')
        cal = calendar_at(now)
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'journal.json')
            notices = missed_preopen_messages(cal, now, journal)
            self.assertEqual(len(notices), 1)
            identity, message = notices[0]
            self.assertEqual(identity, 'preopen-missed:2026-10-06')
            self.assertIn('10/06 22:00', message)
            self.assertIn('매매 신호가 아닌', message)
            self.assertNotIn('100%', message)
            client = Mock()
            deliver(client, 'private-id', notices, journal, now)
            deliver(client, 'private-id', missed_preopen_messages(cal, now, journal), journal, now)
            self.assertEqual(client.send.call_count, 1)
            journal.record('preopen:2026-10-06', now)
            self.assertEqual(missed_preopen_messages(cal, now, journal), [])

    def test_no_missing_warning_before_open_weekends_or_holidays(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'journal.json')
            for value in ['2026-10-06T13:29:59Z', '2026-10-10T14:05:00Z',
                          '2026-11-26T14:35:00Z']:
                now = pd.Timestamp(value)
                self.assertEqual(missed_preopen_messages(calendar_at(now), now, journal), [])

    def test_warning_at_open_and_after_early_close(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'journal.json')
            for value in ['2026-10-06T13:30:00Z', '2026-11-27T18:10:00Z']:
                now = pd.Timestamp(value)
                self.assertEqual(len(missed_preopen_messages(calendar_at(now), now, journal)), 1)

    def test_slow_price_download_does_not_send_after_open_target(self):
        before = pd.Timestamp('2026-10-06T13:29:59Z')
        after = pd.Timestamp('2026-10-06T13:30:01Z')
        state = self.state(due_events(calendar_at(before), before), target='BOOST')
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'journal.json')
            client = Mock()
            with patch('scripts.q123_telegram.utc_now', side_effect=[before, after]), \
                 patch('scripts.q123_telegram.Journal', return_value=journal), \
                 patch('scripts.q123_telegram.Telegram', return_value=client), \
                 patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'fixture', 'TELEGRAM_CHAT_ID': 'fixture-id'}), \
                 patch.object(sys, 'argv', ['q123_telegram.py']), \
                 patch.dict(sys.modules, {'fetch_data': SimpleNamespace(get_q123_state=lambda: state)}):
                self.assertEqual(main(), 0)
            self.assertEqual(client.send.call_count, 1)
            self.assertIn('누락 확인', client.send.call_args.args[1])
            self.assertNotIn('100%', client.send.call_args.args[1])
            self.assertFalse(journal.sent('preopen:2026-10-06'))

    def test_missing_configuration_fails_visibly(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(sys, 'argv', ['q123_telegram.py']):
            self.assertEqual(main(), 1)

    def test_receipts_survive_failure_and_prevent_repeats(self):
        now, due = self.due('2026-10-06T13:00:00Z')
        notices = messages(self.state(due, target='BOOST'), due, now)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.json'
            client = Mock()
            client.send.side_effect = [1, TelegramError('failed')]
            with self.assertRaises(TelegramError):
                deliver(client, 'private-id', notices, Journal(path), now)
            journal = Journal(path)
            self.assertTrue(journal.sent(notices[0][0]))
            self.assertFalse(journal.sent(notices[1][0]))
            client = Mock()
            deliver(client, 'private-id', notices, journal, now)
            self.assertEqual(client.send.call_count, 1)
            deliver(client, 'private-id', notices, Journal(path), now)
            self.assertEqual(client.send.call_count, 1)
            contents = path.read_text()
            self.assertNotIn('private-id', contents)
            self.assertNotIn('BOOST', contents)

    def test_discovery_requires_one_exact_private_sender(self):
        def update(chat_id, kind='private', text='Q123 연결 확인', sender=None):
            return {'message': {'chat': {'id': chat_id, 'type': kind}, 'text': text,
                                'from': {'id': chat_id if sender is None else sender}}}
        self.assertEqual(discover_chat([update(10), update(10), update(20, 'group')], 'Q123 연결 확인'), '10')
        for updates in [[], [update(10, text='/start')], [update(10), update(20)], [update(10, sender=20)]]:
            with self.assertRaises(TelegramError):
                discover_chat(updates, 'Q123 연결 확인')

    def test_transport_errors_never_expose_credentials(self):
        secret = '123456:secret-value'
        for error in [requests.Timeout(f'https://api.telegram.org/bot{secret}'),
                      requests.ConnectionError(f'https://api.telegram.org/bot{secret}')]:
            session = Mock()
            session.post.side_effect = error
            with self.assertRaises(TelegramError) as raised:
                Telegram(secret, session).call('getMe')
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn('https://', str(raised.exception))
        session = Mock()
        session.post.return_value.status_code = 401
        with self.assertRaisesRegex(TelegramError, 'HTTP 401'):
            Telegram(secret, session).call('getMe')
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {'ok': True, 'result': {'message_id': 5}}
        self.assertEqual(Telegram(secret, session).send('private-id', 'test'), 5)
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])


if __name__ == '__main__':
    unittest.main()
