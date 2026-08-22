"""Provision one owner-device pairing code and deliver it without logging it."""
from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.db import session_scope
from app.device_pairing import provision_pairing_session


def _send_telegram(code: str, minutes: int) -> None:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHATID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("owner Telegram delivery is not configured")
    text = (
        "SignalAI · привязка устройства\n\n"
        f"Код действует {minutes} мин. и только один раз:\n{code}\n\n"
        "Откройте SignalAI → Контроль → Подключения → Адрес движка и вставьте этот код."
    )
    body = urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        method="POST",
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed Telegram host
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("ok") is not True:
        raise RuntimeError("Telegram rejected pairing-code delivery")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=15)
    args = parser.parse_args()
    if not 1 <= args.minutes <= 30:
        raise SystemExit("--minutes must be between 1 and 30")

    with session_scope() as db:
        code, _ = provision_pairing_session(
            db,
            ttl=timedelta(minutes=args.minutes),
        )
    try:
        _send_telegram(code, args.minutes)
    except Exception:
        # The raw code is deliberately not printed even on delivery failure.
        # Re-running provisioning invalidates this undelivered code.
        raise
    print(f"Pairing code sent to owner Telegram; valid {args.minutes} minutes, one use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
