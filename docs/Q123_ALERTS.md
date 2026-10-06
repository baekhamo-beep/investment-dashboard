# Q123 Telegram alerts

This feature uses the existing Q123_CANONICAL_V2 engine and only completed NYSE sessions.
It reports model signals, not a brokerage account's orders or holdings.

## Private connection

1. Create a bot with official Telegram BotFather and send `Q123 연결 확인` to **your own bot**.
2. Add `TELEGRAM_BOT_TOKEN` to repository Actions secrets. Do not publish tokens in code or chat.
3. Run **Connect Q123 Telegram**. Its first installation on main also runs the connection check.
4. The runner verifies the token and accepts only one private chat containing that exact phrase.
   If none or multiple chats match, it stops without picking a recipient.
5. The bot sends the chat ID **inside that private Telegram message**, never in Actions logs.
6. Add that number to the `TELEGRAM_CHAT_ID` Actions secret.
7. Run **Q123 Telegram Alerts** manually with `test_message` selected to verify reception.
   Changes to its workflow or transport/alert code on main also send one connection test.
   Connection tests then verify fresh market data against the current exchange calendar.

Telegram keeps unconsumed updates for at most 24 hours. If discovery fails, send the phrase again.
The discovery script does not remove existing webhooks. A bot with an existing webhook must
be handled separately; it does not silently disable another integration.

## Notifications

- **Transition:** the latest completed close produces a new target mode. Sent once per signal
  date/from/to, before its next open. Includes NORMAL returns and BEAR-to-BOOST direct entries.
- **Pre-open:** sent every trading session even when unchanged. Target time is NYSE open minus
  30 minutes: 22:00 Korea in US daylight time, 23:00 Korea in standard time.
- Exchange calendar excludes US holidays and weekends and includes early-close dates.
- Quotes are collected afresh with the same `get_q123_state()` used by the dashboard collector.
  No stale seed fallback or intraday signal is used. Invalid/missing data sends a separate
  data-check notice, never a normal trading instruction.

GitHub Actions schedules can be delayed or dropped. Runs can catch up before the open and
label delays of at least 5 minutes; no pre-open instruction is sent at or after open.
Exact delivery at minus 30 minutes is not guaranteed. Use an external scheduler if required.
Phone/app notification settings and connectivity also affect when a user sees the message.

## Delivery journal and credentials

`state/q123_alert_receipts.json` stores only SHA-256 event keys and acknowledgment times.
It contains no chat ID, token, message body, or account positions. Alerts use a shared workflow
concurrency group; successfully acknowledged sends are journaled immediately and committed
even if a later send fails. Normal repeated runs do not resend the same event.

Telegram sendMessage has no client idempotency key. If the API accepts a message but the
response is lost, or the receipt commit fails, a subsequent run can duplicate it. This is
an acknowledged-delivery journal, not an exactly-once guarantee. Ambiguous sends are not
blindly retried inside the transport. API exceptions never expose credential-bearing URLs.

No Telegram command can submit trades. Notifications contain dashboard links and model targets.
