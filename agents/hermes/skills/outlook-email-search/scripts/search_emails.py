#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Search Microsoft Graph mailbox and return structured JSON results.

Routes requests through the credential sidecar (GRAPH_SIDECAR_URL) which swaps
Authorization: Bearer OUTLOOK_TOKEN_PLACEHOLDER for a live delegated access token.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

OUTLOOK_TOKEN_PLACEHOLDER = "OUTLOOK_TOKEN_PLACEHOLDER"

_WELL_KNOWN_FOLDERS = {
    "inbox": "inbox",
    "sent": "sentitems",
    "drafts": "drafts",
    "deleted": "deleteditems",
    "archive": "archive",
    "junk": "junkemail",
}


def _graph_base() -> str:
    sidecar = os.environ.get("GRAPH_SIDECAR_URL", "http://127.0.0.1:8766").rstrip("/")
    return f"{sidecar}/v1.0"


def _mailbox() -> str:
    # OUTLOOK_REPLY_TO is the human owner's personal address (e.g. you@nvidia.com).
    # OUTLOOK_TARGET_MAILBOX is the agent's polling mailbox (e.g. agt-you@nvidia.com).
    # "my emails" means the human's inbox, so prefer REPLY_TO.
    for env_key in ("OUTLOOK_REPLY_TO", "OUTLOOK_TARGET_MAILBOX"):
        raw = os.environ.get(env_key, "").strip()
        if raw and not raw.startswith("openshell:resolve:"):
            return f"users/{raw}"
    return "me"


def _graph_get(path: str) -> dict:
    url = f"{_graph_base()}/{path.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {OUTLOOK_TOKEN_PLACEHOLDER}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("error", {}).get("message", body[:300])
        except Exception:
            detail = body[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _strip_html(text: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: str) -> str:
    """Parse a date to ISO 8601 UTC. Accepts 2026-04-01, 2026-04-01T12:00:00Z,
    or relative shorthand: 7d, 2w, 1m (days/weeks/months ago from now)."""
    value = value.strip()
    now = datetime.now(tz=timezone.utc)
    m = re.fullmatch(r"(\d+)([dwm])", value)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"d": timedelta(days=n), "w": timedelta(weeks=n), "m": timedelta(days=n * 30)}[unit]
        return (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {value!r}. Use YYYY-MM-DD or relative like 7d, 2w, 1m.")


def _build_params(args: argparse.Namespace) -> dict[str, str]:
    """Build OData query parameters.

    $search (KQL) handles free-text and subject lookups.
    $filter handles sender, date range, and read-status — combinable with $search.
    $orderby is omitted when $search is present (Graph API constraint).
    """
    search_terms: list[str] = []
    filters: list[str] = []

    if args.query:
        search_terms.append(args.query)

    if args.subject:
        # KQL subject: prefix scopes the search to subject field
        search_terms.append(f'subject:"{args.subject}"')

    if args.sender:
        # Exact sender match is more reliable via $filter than KQL from:
        filters.append(f"from/emailAddress/address eq '{args.sender}'")

    if args.since:
        filters.append(f"receivedDateTime ge {_parse_date(args.since)}")

    if args.until:
        filters.append(f"receivedDateTime le {_parse_date(args.until)}")

    if args.unread:
        filters.append("isRead eq false")

    params: dict[str, str] = {
        "$select": "id,subject,from,receivedDateTime,isRead,hasAttachments,bodyPreview",
        "$top": str(min(args.top, 50)),
    }

    if search_terms:
        params["$search"] = f'"{" ".join(search_terms)}"'
        # $orderby is incompatible with $search in Graph API
    else:
        params["$orderby"] = "receivedDateTime desc"

    if filters:
        params["$filter"] = " and ".join(filters)

    return params


def _fetch_body(mailbox: str, msg_id: str) -> str:
    try:
        data = _graph_get(f"{mailbox}/messages/{msg_id}?$select=body")
        content = data.get("body", {}).get("content", "")
        content_type = data.get("body", {}).get("contentType", "text")
        if content_type.lower() == "html":
            content = _strip_html(content)
        return content[:4000]
    except Exception as exc:
        return f"(error fetching body: {exc})"


def search_messages(args: argparse.Namespace) -> list[dict]:
    mailbox = _mailbox()
    folder = _WELL_KNOWN_FOLDERS.get(args.folder.lower(), args.folder)
    params = _build_params(args)
    path = f"{mailbox}/mailFolders/{folder}/messages?{urllib.parse.urlencode(params)}"

    data = _graph_get(path)
    messages = data.get("value", [])

    results = []
    for msg in messages:
        from_addr = msg.get("from", {}).get("emailAddress", {})
        item = {
            "id": msg.get("id"),
            "subject": msg.get("subject", "(no subject)"),
            "from": from_addr.get("address", ""),
            "from_name": from_addr.get("name", ""),
            "received": msg.get("receivedDateTime", ""),
            "is_read": msg.get("isRead", False),
            "has_attachments": msg.get("hasAttachments", False),
            "preview": msg.get("bodyPreview", ""),
        }
        if args.body:
            item["body"] = _fetch_body(mailbox, item["id"])
        results.append(item)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search Outlook mailbox via Microsoft Graph API"
    )
    parser.add_argument("--query", help="Free-text keyword search (KQL)")
    parser.add_argument("--subject", help="Subject contains this text (KQL subject: field)")
    parser.add_argument("--from", dest="sender", metavar="EMAIL",
                        help="Filter by sender email address (exact match)")
    parser.add_argument("--since", metavar="DATE",
                        help="Messages after this date (YYYY-MM-DD, or relative: 7d, 2w, 1m)")
    parser.add_argument("--until", metavar="DATE",
                        help="Messages before this date (YYYY-MM-DD)")
    parser.add_argument("--folder", default="inbox",
                        help="Folder to search: inbox (default), sent, drafts, archive, junk")
    parser.add_argument("--top", type=int, default=20,
                        help="Max results to return (default 20, max 50)")
    parser.add_argument("--unread", action="store_true",
                        help="Return only unread messages")
    parser.add_argument("--body", action="store_true",
                        help="Fetch full body text for each message (slower; fetches individually)")
    args = parser.parse_args()

    if not any([args.query, args.subject, args.sender, args.since, args.until, args.unread]):
        print(json.dumps({
            "ok": False,
            "error": "no_criteria",
            "message": "Provide at least one of: --query, --subject, --from, --since, --until, --unread",
        }))
        return 1

    try:
        results = search_messages(args)
        print(json.dumps({"ok": True, "count": len(results), "messages": results}, indent=2))
        return 0
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": "graph_error", "message": str(exc)}))
        return 2
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "invalid_argument", "message": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "unexpected", "message": str(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
