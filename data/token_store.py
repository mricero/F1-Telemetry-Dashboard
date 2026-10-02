"""The F1TV subscription token in the user's own ``.env`` (LIVE-24).

The token is a JWT valid for a few days. The Live page shows when it expires
and offers a paste box; the value is written to ``.env`` only on an explicit
Save, never logged, and never fetched by an automated login (``fastf1``'s
``f1auth`` starts a blocking local auth server, so it is not used here).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from data.live_adapter import TOKEN_ENV_VAR
from data.signalr_core import token_expiry, token_from_env_value

_LINE = re.compile(rf"^\s*(?:export\s+)?{TOKEN_ENV_VAR}\s*=")


def token_status(token: str | None, now: datetime | None = None) -> dict:
    """Expiry facts for display: ``{"expires_at", "expired", "days_left"}``.

    ``expires_at`` is None when the value is not a JWT with an ``exp`` claim;
    then nothing is known about expiry and ``expired`` is False.
    """
    expires_at = token_expiry(token_from_env_value(token))
    if expires_at is None:
        return {"expires_at": None, "expired": False, "days_left": None}
    current = now or datetime.now(UTC)
    remaining = (expires_at - current).total_seconds()
    return {
        "expires_at": expires_at,
        "expired": remaining <= 0,
        "days_left": max(int(remaining // 86400), 0),
    }


def save_subscription_token(token: str, env_path: str | Path) -> Path:
    """Write ``F1TV_SUBSCRIPTION_TOKEN=<token>`` into ``env_path``.

    Exactly one such line remains: an existing one is replaced in place,
    duplicates are dropped, and every other line is kept as it was. The
    value is also put into this process's environment, so the next
    connection uses it without a restart.
    """
    value = (token or "").strip().strip('"').strip("'")
    if not value or any(ch in value for ch in "\r\n"):
        raise ValueError("The token is empty or spans several lines")

    path = Path(env_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out, written = [], False
    for line in lines:
        if _LINE.match(line):
            if not written:
                out.append(f"{TOKEN_ENV_VAR}={value}")
                written = True
            continue
        out.append(line)
    if not written:
        out.append(f"{TOKEN_ENV_VAR}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)
    os.environ[TOKEN_ENV_VAR] = value
    return path
