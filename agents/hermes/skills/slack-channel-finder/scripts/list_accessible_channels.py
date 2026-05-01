#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
list_accessible_channels.py

List all Slack channels the bot has been added to (or that are public and
visible to the token), with the cheap metadata fields needed for topic-
based ranking: id, name, topic, purpose, is_archived, num_members.

This script intentionally does NOT call conversations.history or pins.list.
It is the cheap "what channels exist" endpoint. For per-channel deep
inspection, use describe_slack_channel.py.

Usage:
    python3 list_accessible_channels.py
    python3 list_accessible_channels.py --include-archived
    python3 list_accessible_channels.py --types public_channel,private_channel

Environment:
    SLACK_BOT_TOKEN must be set.

Exit codes:
    0  ok
    1  bad arguments or environment
    2  Slack API error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


SLACK_API_BASE = "https://slack.com/api"
DEFAULT_TIMEOUT_SECONDS = 15
PAGE_LIMIT = 200  # Slack hard cap per page is 1000; 200 is friendly to rate limits.


def slack_get(method: str, params: dict[str, Any], token: str) -> dict[str, Any]:
    """Call a Slack Web API GET method and return parsed JSON."""
    url = f"{SLACK_API_BASE}/{method}?{urlencode(params)}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        return {"ok": False, "error": f"http_{e.code}", "detail": str(e)}
    except URLError as e:
        return {"ok": False, "error": "url_error", "detail": str(e)}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": "bad_json", "detail": str(e)}


def list_bot_channels(
    token: str,
    types: str,
    include_archived: bool,
) -> dict[str, Any]:
    """Page through users.conversations and collect bot-accessible channels."""
    channels: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {
            "types": types,
            "limit": str(PAGE_LIMIT),
            "exclude_archived": "false" if include_archived else "true",
        }
        if cursor:
            params["cursor"] = cursor

        resp = slack_get("users.conversations", params, token)
        if not resp.get("ok"):
            return {
                "ok": False,
                "error": resp.get("error", "unknown_error"),
                "detail": resp.get("detail"),
                "partial_channels": channels,
            }

        for ch in resp.get("channels", []):
            channels.append(
                {
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "is_archived": ch.get("is_archived", False),
                    "is_private": ch.get("is_private", False),
                    "is_member": ch.get("is_member", False),
                    "num_members": ch.get("num_members"),
                    "topic": (ch.get("topic") or {}).get("value", ""),
                    "topic_last_set": (ch.get("topic") or {}).get("last_set"),
                    "purpose": (ch.get("purpose") or {}).get("value", ""),
                    "purpose_last_set": (ch.get("purpose") or {}).get("last_set"),
                    "created": ch.get("created"),
                }
            )

        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break

    return {"ok": True, "channels": channels, "count": len(channels)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--types",
        default="public_channel",
        help="Comma-separated Slack conversation types (default: public_channel). "
             "Add 'private_channel' only if the bot token has the groups:read scope.",
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived channels in the result",
    )
    args = parser.parse_args()

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print(json.dumps({"ok": False, "error": "missing_token"}))
        return 1

    result = list_bot_channels(token, args.types, args.include_archived)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
