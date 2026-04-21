#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Outlook sidecar bridge for NemoClaw / Hermes Agent.
#
# Polls Microsoft Graph API for new emails using delta queries, relays
# each message body to the Hermes HTTP API, and sends the reply back to the
# sender via Graph API. Also runs scheduled jobs from cron/outlook-jobs.json.
#
# Credential injection: all Microsoft credential strings are hardcoded as
# openshell:resolve:env:* placeholders. The OpenShell L7 proxy rewrites them
# with real values at egress — no secrets are stored in the image or env file.

import asyncio
import datetime
import json
import logging
import os
import signal
import sys
import time

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="[outlook-bridge] %(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ── OpenShell credential placeholders ───────────────────────────────────────
# The L7 proxy rewrites these strings in outgoing HTTP request URLs and headers.
TENANT_ID    = "openshell:resolve:env:OUTLOOK_TENANT_ID"
BOT_MAILBOX  = "openshell:resolve:env:OUTLOOK_BOT_MAILBOX"   # polls, sends, marks read
USER_MAILBOX = "openshell:resolve:env:OUTLOOK_USER_MAILBOX"  # default recipient for scheduled jobs

# Pre-computed base64(client_id:client_secret) stored as a single OpenShell
# credential. The L7 proxy rewrites it in the Authorization header before the
# request reaches Microsoft, keeping individual secrets off the wire and out of
# the image. Computed by `nemoclaw onboard` and stored as OUTLOOK_BASIC_AUTH.
BASIC_AUTH = "openshell:resolve:env:OUTLOOK_BASIC_AUTH"

TOKEN_URL  = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# ── Runtime config ───────────────────────────────────────────────────────────
HERMES_URL = "http://127.0.0.1:18642/v1/chat/completions"
HEALTH_URL = "http://127.0.0.1:18642/health"

MIN_POLL_INTERVAL = 5    # seconds when inbox is active
MAX_POLL_INTERVAL = 30   # seconds when inbox is quiet
BACKOFF_AFTER     = 3    # consecutive empty polls before backing off

HEALTH_RETRY_SECONDS = 5
HEALTH_MAX_RETRIES = 60  # 5 minutes total

HERMES_HOME = os.environ.get("HERMES_HOME", "/sandbox/.hermes-data")
JOBS_FILE = os.path.join(HERMES_HOME, "cron", "outlook-jobs.json")
SENDER_POLL_INTERVAL = 30  # seconds between inbox checks while waiting for first email

# ── Module-level state ───────────────────────────────────────────────────────
_client: httpx.AsyncClient | None = None   # created in _async_main()
_token: str = ""
_token_expiry: float = 0.0
_delta_link: str | None = None             # None = initialize on first poll
_consecutive_empty: int = 0
ALLOWED_SENDERS: set[str] = set()          # populated at startup


# ── Startup health check ─────────────────────────────────────────────────────

async def wait_for_hermes() -> None:
    for attempt in range(HEALTH_MAX_RETRIES):
        try:
            r = await _client.get(HEALTH_URL, timeout=5)
            if r.status_code == 200:
                log.info("Hermes gateway is healthy")
                return
        except httpx.RequestError:
            pass
        log.info("Waiting for Hermes gateway (attempt %d/%d)…", attempt + 1, HEALTH_MAX_RETRIES)
        await asyncio.sleep(HEALTH_RETRY_SECONDS)
    log.error("Hermes gateway did not become healthy — exiting")
    sys.exit(1)


# ── Token caching ────────────────────────────────────────────────────────────

async def get_access_token(*, force: bool = False) -> str:
    global _token, _token_expiry
    if not force and _token and time.monotonic() < _token_expiry - 60:
        return _token
    # RFC 6749 §2.3.1: client credentials via HTTP Basic auth so the L7 proxy
    # can rewrite the BASIC_AUTH placeholder in the Authorization header.
    resp = await _client.post(
        TOKEN_URL,
        content="grant_type=client_credentials&scope=https://graph.microsoft.com/.default",
        headers={
            "Authorization": f"Basic {BASIC_AUTH}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _token_expiry = time.monotonic() + data.get("expires_in", 3600)
    return _token


# ── Microsoft Graph helpers ──────────────────────────────────────────────────
# Accepts full URLs (delta links) or relative paths. Handles 401 token refresh
# and 429 rate limiting with Retry-After backoff.

async def _graph_request(method: str, path_or_url: str, token: str, **kwargs) -> dict | None:
    url = path_or_url if path_or_url.startswith("http") else f"{GRAPH_BASE}/{path_or_url.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    resp = await getattr(_client, method)(url, headers=headers, **kwargs)

    if resp.status_code == 401:
        token = await get_access_token(force=True)
        headers["Authorization"] = f"Bearer {token}"
        resp = await getattr(_client, method)(url, headers=headers, **kwargs)

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 60))
        log.warning("Graph API rate limited — retrying after %ds", retry_after)
        await asyncio.sleep(retry_after)
        resp = await getattr(_client, method)(url, headers=headers, **kwargs)

    resp.raise_for_status()
    return resp.json() if resp.content else None


async def graph_get(path_or_url: str, token: str) -> dict:
    return await _graph_request("get", path_or_url, token, timeout=15)


async def graph_post(path_or_url: str, payload: dict, token: str) -> None:
    await _graph_request(
        "post", path_or_url, token,
        json=payload, headers={"Content-Type": "application/json"}, timeout=15,
    )


async def graph_patch(path_or_url: str, payload: dict, token: str) -> None:
    await _graph_request(
        "patch", path_or_url, token,
        json=payload, headers={"Content-Type": "application/json"}, timeout=10,
    )


# ── Allowed-senders resolution ───────────────────────────────────────────────

async def resolve_allowed_senders() -> set[str]:
    # Discover USER_MAILBOX's SMTP address from BOT_MAILBOX's inbox.
    # Retries indefinitely — sandbox stays up, bridge activates on first email.
    # Mail.Read (Application, already required) covers both mailboxes tenant-wide.
    # User.Read.All is NOT required.
    logged_waiting = False
    while True:
        token = await get_access_token()
        data = await graph_get(
            f"users/{BOT_MAILBOX}/mailFolders/inbox/messages"
            "?$top=10&$select=from&$orderby=receivedDateTime desc",
            token,
        )
        for msg in data.get("value", []):
            address = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
            if "@" in address:
                log.info("Allowed senders resolved via bot inbox: %s", address)
                return {address}

        if not logged_waiting:
            log.info(
                "Bot inbox empty — waiting for first email from USER_MAILBOX to BOT_MAILBOX. "
                "Send a message to activate the bridge."
            )
            logged_waiting = True
        await asyncio.sleep(SENDER_POLL_INTERVAL)


# ── Hermes relay ─────────────────────────────────────────────────────────────

async def ask_hermes(prompt: str) -> str | None:
    try:
        resp = await _client.post(
            HERMES_URL,
            json={"model": "hermes-agent", "messages": [{"role": "user", "content": prompt}]},
            timeout=1200,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        log.exception("Error calling Hermes API")
        return None


# ── Inbox polling ────────────────────────────────────────────────────────────

async def poll_inbox() -> int:
    global _delta_link
    token = await get_access_token()

    # Graph delta queries for messages don't support $filter — filter client-side instead.
    path = (
        _delta_link
        or f"users/{BOT_MAILBOX}/mailFolders/inbox/messages/delta"
           "?$select=id,subject,body,from,isRead"
    )

    messages: list[dict] = []
    try:
        data = await graph_get(path, token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 410):
            # Delta link expired (syncStateNotFound) — reset and retry from scratch next poll
            log.warning("Delta link expired (status %d) — resetting state", exc.response.status_code)
            _delta_link = None
            return 0
        raise

    while True:
        messages.extend(
            msg for msg in data.get("value", [])
            if not msg.get("@removed") and not msg.get("isRead", False)
        )
        if next_link := data.get("@odata.nextLink"):
            data = await graph_get(next_link, token)
        else:
            break
    if dl := data.get("@odata.deltaLink"):
        _delta_link = dl

    if messages:
        results = await asyncio.gather(
            *[_handle_message(msg, token) for msg in messages],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                log.exception("Error handling message: %s", r)

    return len(messages)


async def _handle_message(msg: dict, token: str) -> None:
    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
    if ALLOWED_SENDERS and sender not in ALLOWED_SENDERS:
        log.info("Ignoring message from non-allowed sender: %s", sender)
        await _mark_read(msg["id"], token)
        return

    subject = msg.get("subject", "(no subject)")
    body = msg.get("body", {}).get("content", "")
    prompt = f"Email from {sender}\nSubject: {subject}\n\n{body}"

    log.info("Processing message from %s: %s", sender, subject)
    reply = await ask_hermes(prompt)
    if reply:
        await _send_reply(msg["id"], reply, token)
    await _mark_read(msg["id"], token)


async def _send_reply(msg_id: str, reply: str, token: str) -> None:
    try:
        await graph_post(
            f"users/{BOT_MAILBOX}/messages/{msg_id}/reply",
            {"comment": reply},
            token,
        )
        log.info("Sent reply to message %s", msg_id)
    except Exception:
        log.exception("Error sending reply for message %s", msg_id)


async def _mark_read(msg_id: str, token: str) -> None:
    try:
        await graph_patch(f"users/{BOT_MAILBOX}/messages/{msg_id}", {"isRead": True}, token)
    except Exception:
        log.exception("Error marking message %s as read", msg_id)


# ── Adaptive poll loop ───────────────────────────────────────────────────────

async def _poll_loop(shutdown: asyncio.Event) -> None:
    global _consecutive_empty
    while not shutdown.is_set():
        try:
            count = await poll_inbox()
        except Exception:
            log.exception("Error during inbox poll")
            count = 0

        if count > 0:
            _consecutive_empty = 0
            interval = MIN_POLL_INTERVAL
        else:
            _consecutive_empty += 1
            interval = MAX_POLL_INTERVAL if _consecutive_empty >= BACKOFF_AFTER else MIN_POLL_INTERVAL

        # shutdown.wait() lets SIGTERM wake us immediately instead of sleeping the full interval
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ── Scheduled jobs ───────────────────────────────────────────────────────────

def _load_jobs() -> list[dict]:
    if not os.path.exists(JOBS_FILE):
        log.info("No jobs file at %s — scheduled jobs disabled", JOBS_FILE)
        return []
    try:
        with open(JOBS_FILE) as f:
            jobs = json.load(f)
        for job in jobs:
            log.info("Loaded job '%s' at %s daily", job.get("name", "?"), job.get("time", "?"))
        return jobs
    except Exception:
        log.exception("Failed to load %s", JOBS_FILE)
        return []


async def _job_loop(jobs: list[dict], shutdown: asyncio.Event) -> None:
    last_day = -1
    while not shutdown.is_set():
        try:
            now = datetime.datetime.now()
            if now.day != last_day:  # reset daily-fire flags at midnight
                for job in jobs:
                    job.pop("_fired_today", None)
                last_day = now.day
            time_str = now.strftime("%H:%M")
            for job in jobs:
                if job.get("time") == time_str and not job.get("_fired_today"):
                    job["_fired_today"] = True
                    asyncio.create_task(_run_job(job))  # fire in background, doesn't block loop
        except Exception:
            log.exception("Error in job loop tick")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


async def _run_job(job: dict) -> None:
    prompt = job.get("prompt", "")
    if not prompt:
        log.warning("Job '%s' has no prompt — skipping", job.get("name", "?"))
        return
    log.info("Running scheduled job: %s", job.get("name", prompt[:50]))
    reply = await ask_hermes(prompt)
    if not reply:
        return
    try:
        token = await get_access_token()
        to_address = job.get("to", USER_MAILBOX)
        subject = job.get("subject", f"Scheduled: {job.get('name', 'report')}")
        await graph_post(
            f"users/{BOT_MAILBOX}/sendMail",
            {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": reply},
                    "toRecipients": [{"emailAddress": {"address": to_address}}],
                }
            },
            token,
        )
        log.info("Sent scheduled email for job '%s' to %s", job.get("name", "?"), to_address)
    except Exception:
        log.exception("Error sending scheduled email for job '%s'", job.get("name", "?"))


# ── Main ─────────────────────────────────────────────────────────────────────

async def _async_main() -> None:
    global _client, ALLOWED_SENDERS
    log.info("Outlook bridge starting (HERMES_HOME=%s)", HERMES_HOME)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: (log.info("Shutdown signal received"), shutdown.set()))

    # httpx.AsyncClient reads HTTPS_PROXY from env automatically
    async with httpx.AsyncClient() as client:
        _client = client
        await wait_for_hermes()

        try:
            ALLOWED_SENDERS = await resolve_allowed_senders()
        except httpx.RemoteProtocolError:
            log.error(
                "Outlook bridge: L7 proxy disconnected during token request. "
                "OUTLOOK_BASIC_AUTH may not be in the provider — re-run "
                "`nemoclaw onboard` with Outlook credentials sourced to add it."
            )
            sys.exit(1)
        except httpx.HTTPStatusError as exc:
            log.error(
                "Outlook bridge: token request returned HTTP %d. "
                "Check OUTLOOK_CLIENT_ID / OUTLOOK_CLIENT_SECRET in the provider.",
                exc.response.status_code,
            )
            sys.exit(1)
        jobs = _load_jobs()
        log.info(
            "Bridge ready — polling inbox (%ds active / %ds quiet)",
            MIN_POLL_INTERVAL, MAX_POLL_INTERVAL,
        )
        coros = [_poll_loop(shutdown)]
        if jobs:
            coros.append(_job_loop(jobs, shutdown))
        await asyncio.gather(*coros)


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
