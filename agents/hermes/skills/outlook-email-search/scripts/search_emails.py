#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Search Microsoft Graph mailbox and return structured JSON results.

Routes requests through the credential sidecar (MS_GRAPH_SIDECAR_URL) which swaps
Authorization: Bearer MS_GRAPH_TOKEN_PLACEHOLDER for a live delegated access token.
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

MS_GRAPH_TOKEN_PLACEHOLDER = "MS_GRAPH_TOKEN_PLACEHOLDER_OUTLOOK"

_WELL_KNOWN_FOLDERS = {
    "inbox": "inbox",
    "sent": "sentitems",
    "drafts": "drafts",
    "deleted": "deleteditems",
    "archive": "archive",
    "junk": "junkemail",
}


def _graph_base() -> str:
    sidecar = os.environ.get("MS_GRAPH_SIDECAR_URL", "").rstrip("/")
    return f"{sidecar}/v1.0" if sidecar else "https://graph.microsoft.com/v1.0"


def _mailbox() -> str:
    # OUTLOOK_REPLY_TO is the human owner's personal address (e.g. you@nvidia.com).
    # OUTLOOK_TARGET_MAILBOX is the agent's polling mailbox (e.g. agt-you@nvidia.com).
    # "my emails" means the human's inbox, so prefer REPLY_TO.
    for env_key in ("OUTLOOK_REPLY_TO", "OUTLOOK_TARGET_MAILBOX"):
        raw = os.environ.get(env_key, "").strip()
        if raw and not raw.startswith("openshell:resolve:"):
            return f"users/{raw}"
    return "me"


def _internal_domains() -> set[str]:
    """Return the set of domains considered 'internal' (derived from mailbox env vars)."""
    domains: set[str] = set()
    for env_key in ("OUTLOOK_REPLY_TO", "OUTLOOK_TARGET_MAILBOX"):
        raw = os.environ.get(env_key, "").strip()
        if "@" in raw and not raw.startswith("openshell:"):
            domains.add(raw.split("@")[-1].lower())
    return domains or {"nvidia.com"}


def _sender_domain(msg: dict) -> str:
    addr = msg.get("from", {}).get("emailAddress", {}).get("address", "")
    return addr.split("@")[-1].lower() if "@" in addr else ""


def _graph_get(path: str) -> dict:
    url = f"{_graph_base()}/{path.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {MS_GRAPH_TOKEN_PLACEHOLDER}",
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

    # When domain filtering is active, over-fetch so we have enough after client-side filtering.
    domain_filtering = args.external_only or args.domain or args.domain_not
    fetch_top = 50 if domain_filtering else min(args.top, 50)

    params: dict[str, str] = {
        "$select": "id,subject,from,receivedDateTime,isRead,hasAttachments,bodyPreview,conversationId",
        "$top": str(fetch_top),
    }

    if search_terms:
        params["$search"] = f'"{" ".join(search_terms)}"'
        # $orderby is incompatible with $search in Graph API
    else:
        params["$orderby"] = "receivedDateTime desc"

    if filters:
        params["$filter"] = " and ".join(filters)

    return params


def _fetch_pages(path: str, max_pages: int) -> list[dict]:
    """Fetch up to max_pages pages of results, following @odata.nextLink."""
    messages: list[dict] = []
    data = _graph_get(path)
    messages.extend(data.get("value", []))
    page = 1
    while page < max_pages:
        next_link = data.get("@odata.nextLink", "")
        if not next_link:
            break
        # nextLink is a full URL; strip the base so _graph_get re-prefixes correctly
        next_path = next_link.split("/v1.0/", 1)[-1]
        data = _graph_get(next_path)
        messages.extend(data.get("value", []))
        page += 1
    return messages


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


def _apply_domain_filters(messages: list[dict], args: argparse.Namespace) -> list[dict]:
    """Client-side domain filtering (Graph OData does not support domain-level filtering)."""
    if not (args.external_only or args.domain or args.domain_not):
        return messages

    internal = _internal_domains()
    result = []
    for msg in messages:
        domain = _sender_domain(msg)
        if args.external_only and domain in internal:
            continue
        if args.domain and domain != args.domain.lower():
            continue
        for excluded in (args.domain_not or []):
            if domain == excluded.lower():
                break
        else:
            result.append(msg)
    return result


def search_messages(args: argparse.Namespace) -> list[dict]:
    mailbox = _mailbox()
    folder = _WELL_KNOWN_FOLDERS.get(args.folder.lower(), args.folder)
    params = _build_params(args)
    path = f"{mailbox}/mailFolders/{folder}/messages?{urllib.parse.urlencode(params)}"

    raw = _fetch_pages(path, max_pages=min(args.pages, 5))
    raw = _apply_domain_filters(raw, args)
    # Trim to the requested top count after domain filtering
    raw = raw[: args.top]

    results = []
    for msg in raw:
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
            "conversation_id": msg.get("conversationId", ""),
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
    parser.add_argument("--external-only", action="store_true",
                        help="Return only emails from senders outside the internal domain "
                             "(auto-detected from OUTLOOK_REPLY_TO / OUTLOOK_TARGET_MAILBOX, "
                             "defaults to nvidia.com)")
    parser.add_argument("--domain", metavar="DOMAIN",
                        help="Return only emails from senders at this domain (e.g. partner.com)")
    parser.add_argument("--domain-not", metavar="DOMAIN", action="append",
                        help="Exclude emails from senders at this domain (repeatable)")
    parser.add_argument("--pages", type=int, default=1,
                        help="Number of Graph API pages to fetch (default 1; each page is up to 50 "
                             "messages). Max 5. Use with domain filters to get enough results after "
                             "client-side filtering.")
    args = parser.parse_args()

    if not any([args.query, args.subject, args.sender, args.since, args.until, args.unread,
                args.external_only, args.domain, args.domain_not]):
        print(json.dumps({
            "ok": False,
            "error": "no_criteria",
            "message": "Provide at least one of: --query, --subject, --from, --since, --until, "
                       "--unread, --external-only, --domain, --domain-not",
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
