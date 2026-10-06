"""Verify stored token and send the private recipient ID to its own Telegram chat."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.telegram_client import Telegram, TelegramError


def discover_chat(updates, phrase):
    chats = set()
    for update in updates:
        message = update.get('message') or {}
        chat = message.get('chat') or {}
        if (chat.get('type') == 'private' and message.get('text') == phrase
                and isinstance(chat.get('id'), int)
                and message.get('from', {}).get('id') == chat['id']):
            chats.add(chat['id'])
    if len(chats) != 1:
        raise TelegramError('Expected exactly one private chat with the connection phrase. '
                            'Send Q123 연결 확인 to your own bot, then run again.')
    return str(chats.pop())


def main():
    try:
        client = Telegram(os.environ.get('TELEGRAM_BOT_TOKEN', ''))
        me = client.call('getMe')
        if not isinstance(me, dict) or me.get('is_bot') is not True:
            raise TelegramError('Stored token does not identify a bot')
        print('Telegram token verified on GitHub runner.')
        # Public bot username helps distinguish a valid token for a different bot.
        username = me.get('username', '')
        if isinstance(username, str) and username.replace('_', '').isalnum():
            print(f'Configured bot username: @{username}')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
        if chat_id:
            chat = client.call('getChat', chat_id=chat_id)
            if chat.get('type') != 'private':
                raise TelegramError('Only a private recipient chat is supported')
        else:
            updates = client.call('getUpdates', allowed_updates=['message'], timeout=0)
            print(f'Pending update count: {len(updates or [])}')
            chat_id = discover_chat(updates or [], 'Q123 연결 확인')
        # Keep contact identifiers out of public Actions logs.
        print(f'::add-mask::{chat_id}')
        client.send(chat_id,
                    '✅ Q123 Telegram 연결 확인 완료\n'
                    'GitHub 서버에서 토큰과 수신 대화방을 확인했습니다.\n\n'
                    f'내 TELEGRAM_CHAT_ID: {chat_id}\n\n'
                    'GitHub 저장소 → Settings → Secrets and variables → Actions에서\n'
                    'TELEGRAM_CHAT_ID라는 이름으로 위 숫자를 등록해 주세요.\n'
                    '등록 후 전환 신호 및 거래일 개장 30분 전 알림이 활성화됩니다.\n'
                    '이 메시지는 연결 테스트이며 투자 전환 신호가 아닙니다.')
        print('Connection message delivered to the verified private chat. '
              'The chat ID is shown only inside that Telegram message.')
    except TelegramError as exc:
        print(f'Connection check failed: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
