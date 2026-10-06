"""Telegram transport. Never log API URLs, response bodies, or credential values."""
import requests


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token, session=None):
        self.token = token.strip()
        if not self.token:
            raise TelegramError('TELEGRAM_BOT_TOKEN is not configured')
        self.session = session or requests.Session()

    def call(self, method, **payload):
        try:
            response = self.session.post(
                f'https://api.telegram.org/bot{self.token}/{method}',
                json=payload, timeout=(10, 30), allow_redirects=False)
        except requests.Timeout:
            raise TelegramError('Telegram TIMEOUT: server did not respond') from None
        except requests.RequestException:
            raise TelegramError('Telegram NETWORK_ERROR') from None
        if response.status_code != 200:
            raise TelegramError(f'Telegram HTTP {response.status_code}')
        try:
            data = response.json()
        except ValueError:
            raise TelegramError('Telegram INVALID_RESPONSE') from None
        if not isinstance(data, dict) or data.get('ok') is not True:
            raise TelegramError('Telegram API_ERROR')
        return data.get('result')

    def send(self, chat_id, text):
        result = self.call('sendMessage', chat_id=str(chat_id), text=text,
                           link_preview_options={'is_disabled': True})
        if not isinstance(result, dict) or not isinstance(result.get('message_id'), int):
            raise TelegramError('Telegram message acknowledgement missing')
        return result['message_id']
