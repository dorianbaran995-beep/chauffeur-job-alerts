from __future__ import annotations

import json
import os
import sys
from urllib import parse, request


def api_call(token: str, method: str, data: dict[str, str] | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    payload = parse.urlencode(data or {}).encode("utf-8") if data is not None else None
    req = request.Request(url, data=payload, method="POST" if payload is not None else "GET")
    with request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API returned an error: {result.get('description', result)}")
    return result


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    requested_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing.")
        print("Add it in GitHub: Settings > Secrets and variables > Actions > New repository secret")
        return 1

    me = api_call(token, "getMe").get("result", {})
    username = me.get("username", "unknown")
    print(f"Bot connection OK: @{username}")

    updates = api_call(token, "getUpdates", {"timeout": "0", "allowed_updates": json.dumps(["message", "channel_post", "my_chat_member"])})

    chats: dict[str, dict] = {}
    for update in updates.get("result", []):
        candidates = []
        for key in ("message", "channel_post"):
            obj = update.get(key)
            if isinstance(obj, dict) and isinstance(obj.get("chat"), dict):
                candidates.append(obj["chat"])
        member = update.get("my_chat_member")
        if isinstance(member, dict) and isinstance(member.get("chat"), dict):
            candidates.append(member["chat"])

        for chat in candidates:
            chat_id = str(chat.get("id", "")).strip()
            if chat_id:
                chats[chat_id] = chat

    if chats:
        print("\nTelegram destinations found:")
        for chat_id, chat in chats.items():
            title = chat.get("title") or chat.get("username") or chat.get("first_name") or "Unnamed chat"
            chat_type = chat.get("type", "unknown")
            username_text = f" (@{chat['username']})" if chat.get("username") else ""
            print(f"  CHAT_ID={chat_id} | {chat_type} | {title}{username_text}")
    else:
        print("\nNo channel/group found yet.")
        print("1. Add the bot to your Telegram channel/group.")
        print("2. For a channel, make the bot an administrator with Post Messages permission.")
        print("3. Post a NEW message after adding the bot.")
        print("4. Run this GitHub Action again.")

    chat_id = requested_chat_id
    if not chat_id and len(chats) == 1:
        chat_id = next(iter(chats))
        print(f"\nOne destination found, using it automatically: {chat_id}")

    if chat_id:
        print(f"\nTesting Telegram delivery to {chat_id}...")
        api_call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "✅ Chauffeur Job Alerts connected successfully. Telegram delivery is working.",
                "disable_web_page_preview": "true",
            },
        )
        print("SUCCESS: Test message sent.")
        print(f"Use this value for TELEGRAM_CHAT_ID: {chat_id}")
    elif len(chats) > 1:
        print("\nMore than one destination was found.")
        print("Run the action again and enter the required CHAT_ID in the optional chat_id box.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
