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
from pathlib import Path

OUTLOOK_TOKEN_PLACEHOLDER = "OUTLOOK_TOKEN_PLACEHOLDER"

_WELL_KNOWN_FOLDERS = {
    "inbox": "inbox",
    "sent": "sentitems",
    "drafts": "drafts",
    "deleted": "deleteditems",
    "archive": "archive",
    "junk": "junkemail",
}


def _load_env_file(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        loaded[key.strip()] = value.strip()
    return loaded


def _env_optional(name: str, *, allow_placeholder: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if value and (allow_placeholder or not value.startswith("openshell:resolve:")):
        return value

    candidates: list[Path] = []
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        candidates.append(Path(hermes_home) / ".env")
    candidates.extend([
        Path("/sandbox/.hermes-data/.env"),
        Path("/sandbox/.hermes/.env"),
    ])

    for env_path in candidates:
        file_value = _load_env_file(env_path).get(name, "").strip()
        if file_value and (allow_placeholder or not file_value.startswith("openshell:resolve:")):
            return file_value
    return ""


def _graph_base() -> str:
    sidecar = (_env_optional("GRAPH_SIDECAR_URL") or "http://127.0.0.1:8766").rstrip("/")
    return f"{sidecar}/v1.0"


def _mailbox(which: str = "auto") -> str:
    # OUTLOOK_REPLY_TO is the human owner's personal address (e.g. you@nvidia.com).
    # OUTLOOK_TARGET_MAILBOX is the agent's polling mailbox (e.g. agt-you@nvidia.com).
    # "my emails" means the human's inbox, so prefer REPLY_TO.
    if which in {"reply", "human"}:
        env_keys = ("OUTLOOK_REPLY_TO",)
    elif which in {"target", "agent"}:
        env_keys = ("OUTLOOK_TARGET_MAILBOX",)
    else:
        env_keys = ("OUTLOOK_REPLY_TO", "OUTLOOK_TARGET_MAILBOX")
    for env_key in env_keys:
        raw = _env_optional(env_key, allow_placeholder=False)
        if raw:
            return f"users/{raw}"
    return "me"


def _odata_string(value: str) -> str:
    return value.replace("'", "''")


def _graph_url(path_or_url: str) -> str:
    if path_or_url.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(path_or_url)
        if parsed.netloc == "graph.microsoft.com" and parsed.path.startswith("/v1.0/"):
            url = f"{_graph_base()}/{parsed.path[len('/v1.0/'):].lstrip('/')}"
            if parsed.query:
                url = f"{url}?{parsed.query}"
            return url
        return path_or_url
    return f"{_graph_base()}/{path_or_url.lstrip('/')}"


def _graph_get(path: str) -> dict:
    url = _graph_url(path)
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

    $search handles free-text lookups only.
    $filter handles subject, sender, date range, and read-status.
    $orderby is omitted when $search is present (Graph API constraint).
    """
    search_terms: list[str] = []
    filters: list[str] = []

    if args.query:
        search_terms.append(args.query)

    # When ordering by receivedDateTime, Graph requires receivedDateTime
    # restrictions to appear before other fields in $filter.
    if args.since:
        filters.append(f"receivedDateTime ge {_parse_date(args.since)}")

    if args.until:
        filters.append(f"receivedDateTime le {_parse_date(args.until)}")

    if args.subject:
        filters.append(f"contains(subject,'{_odata_string(args.subject)}')")

    if args.sender:
        # Exact sender match is more reliable via $filter than KQL from:
        filters.append(f"from/emailAddress/address eq '{_odata_string(args.sender)}'")

    if args.unread:
        filters.append("isRead eq false")

    select_fields = ["id", "subject", "from", "receivedDateTime", "isRead", "hasAttachments", "bodyPreview"]
    if args.to or args.cc or args.recipient:
        select_fields.extend(["toRecipients", "ccRecipients"])

    local_filters = bool(args.to or args.cc or args.recipient or (search_terms and filters))
    page_size = min(max(args.top, args.scan if local_filters else args.top), 200)

    params: dict[str, str] = {
        "$select": ",".join(select_fields),
        "$top": str(page_size),
    }

    if search_terms and not filters:
        params["$search"] = f'"{" ".join(search_terms)}"'
        # $orderby is incompatible with $search in Graph API
    else:
        params["$orderby"] = "receivedDateTime desc"

    if filters:
        params["$filter"] = " and ".join(filters)

    return params


def _query_matches(msg: dict, query: str | None) -> bool:
    if not query:
        return True
    terms = [term.strip("\"'").lower() for term in query.split() if term.strip("\"'")]
    if not terms:
        return True
    from_addr = msg.get("from", {}).get("emailAddress", {})
    haystack = " ".join([
        msg.get("subject", ""),
        msg.get("bodyPreview", ""),
        from_addr.get("address", ""),
        from_addr.get("name", ""),
    ]).lower()
    return all(term in haystack for term in terms)


def _recipient_addresses(msg: dict, field: str) -> set[str]:
    addresses: set[str] = set()
    for recipient in msg.get(field, []) or []:
        email = recipient.get("emailAddress", {})
        address = email.get("address", "").strip().lower()
        if address:
            addresses.add(address)
    return addresses


def _recipient_names(msg: dict, field: str) -> set[str]:
    names: set[str] = set()
    for recipient in msg.get(field, []) or []:
        email = recipient.get("emailAddress", {})
        name = email.get("name", "").strip().lower()
        if name:
            names.add(name)
    return names


def _normalize_targets(values: list[str]) -> set[str]:
    return {value.strip().lower() for value in values if value.strip()}


def _match_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _match_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _match_text(value).split():
        tokens.add(token)
        if len(token) > 3 and token.endswith("s"):
            tokens.add(token[:-1])
    return tokens


def _target_matches(targets: set[str], addresses: set[str], names: set[str]) -> bool:
    if not targets:
        return True
    searchable = {_match_text(value) for value in addresses | names if value}
    searchable_tokens = [_match_tokens(value) for value in addresses | names if value]
    for target in targets:
        if target in addresses:
            return True
        normalized_target = _match_text(target)
        if normalized_target and any(normalized_target in value for value in searchable):
            return True
        target_tokens = _match_tokens(target)
        if target_tokens and any(target_tokens.issubset(tokens) for tokens in searchable_tokens):
            return True
        if any(target in name for name in names):
            return True
    return False


def _recipients_match(msg: dict, args: argparse.Namespace) -> bool:
    to_targets = _normalize_targets(args.to)
    cc_targets = _normalize_targets(args.cc)
    any_targets = _normalize_targets(args.recipient)

    to_addresses = _recipient_addresses(msg, "toRecipients")
    cc_addresses = _recipient_addresses(msg, "ccRecipients")
    to_names = _recipient_names(msg, "toRecipients")
    cc_names = _recipient_names(msg, "ccRecipients")

    if not _target_matches(to_targets, to_addresses, to_names):
        return False
    if not _target_matches(cc_targets, cc_addresses, cc_names):
        return False
    if any_targets and not _target_matches(any_targets, to_addresses | cc_addresses, to_names | cc_names):
        return False
    return True


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
    mailbox = _mailbox(args.mailbox)
    folder = _WELL_KNOWN_FOLDERS.get(args.folder.lower(), args.folder)
    params = _build_params(args)
    path = f"{mailbox}/mailFolders/{folder}/messages?{urllib.parse.urlencode(params)}"

    messages: list[dict] = []
    scanned = 0
    next_path: str | None = path
    while next_path and scanned < args.scan and len(messages) < args.top:
        data = _graph_get(next_path)
        page = data.get("value", [])
        scanned += len(page)
        for msg in page:
            if not _query_matches(msg, args.query if params.get("$search") is None else None):
                continue
            if not _recipients_match(msg, args):
                continue
            messages.append(msg)
            if len(messages) >= args.top:
                break
        next_path = data.get("@odata.nextLink")

    messages = messages[:args.top]

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
        if args.to or args.cc or args.recipient:
            item["to"] = sorted(_recipient_addresses(msg, "toRecipients"))
            item["cc"] = sorted(_recipient_addresses(msg, "ccRecipients"))
        if args.body:
            item["body"] = _fetch_body(mailbox, item["id"])
        results.append(item)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search Outlook mailbox via Microsoft Graph API"
    )
    parser.add_argument("--query", help="Free-text keyword search (KQL)")
    parser.add_argument("--subject", help="Subject contains this text")
    parser.add_argument("--from", dest="sender", metavar="EMAIL",
                        help="Filter by sender email address (exact match)")
    parser.add_argument("--to", action="append", default=[], metavar="EMAIL_OR_NAME",
                        help="Locally filter messages where To contains this address or display name")
    parser.add_argument("--cc", action="append", default=[], metavar="EMAIL_OR_NAME",
                        help="Locally filter messages where Cc contains this address or display name")
    parser.add_argument("--recipient", action="append", default=[], metavar="EMAIL_OR_NAME",
                        help="Locally filter messages where To or Cc contains this address or display name")
    parser.add_argument("--since", metavar="DATE",
                        help="Messages after this date (YYYY-MM-DD, or relative: 7d, 2w, 1m)")
    parser.add_argument("--until", metavar="DATE",
                        help="Messages before this date (YYYY-MM-DD)")
    parser.add_argument("--folder", default="inbox",
                        help="Folder to search: inbox (default), sent, drafts, archive, junk")
    parser.add_argument("--mailbox", choices=("auto", "reply", "human", "target", "agent"),
                        default="auto",
                        help=("Mailbox to search: auto/reply/human searches the human owner; "
                              "target/agent searches the agent polling mailbox"))
    parser.add_argument("--top", type=int, default=20,
                        help="Max results to return (default 20, max 50)")
    parser.add_argument("--scan", type=int, default=200,
                        help="Max recent messages to scan when local filters are needed (default 200)")
    parser.add_argument("--unread", action="store_true",
                        help="Return only unread messages")
    parser.add_argument("--body", action="store_true",
                        help="Fetch full body text for each message (slower; fetches individually)")
    args = parser.parse_args()

    if args.top < 1 or args.top > 50:
        print(json.dumps({
            "ok": False,
            "error": "invalid_argument",
            "message": "--top must be between 1 and 50",
        }))
        return 1

    if args.scan < args.top:
        args.scan = args.top

    if not any([args.query, args.subject, args.sender, args.to, args.cc, args.recipient,
                args.since, args.until, args.unread]):
        print(json.dumps({
            "ok": False,
            "error": "no_criteria",
            "message": "Provide at least one of: --query, --subject, --from, --to, --cc, --recipient, --since, --until, --unread",
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
