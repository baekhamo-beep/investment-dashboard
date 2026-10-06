from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import pandas as pd
import requests

from scripts.telegram_client import Telegram, TelegramError
from scripts.setup_q123_telegram import discover_chat
from scripts.q123_telegram import calendar_at, due_events, validate_state, messages, Journal, deliver


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
