# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Outlook credential sidecar.
#
# Accepts plain HTTP requests from the bridge/skill on 127.0.0.1:8766,
# swaps `Authorization: Bearer OUTLOOK_TOKEN_PLACEHOLDER` with the live
# access token, and forwards to https://graph.microsoft.com via the
# upstream proxy (OpenShell L7 proxy chain).
#
# Why plain HTTP on the inbound leg:
#   HTTPS_PROXY causes Python HTTP clients to use CONNECT tunneling, which
#   encrypts the request before the proxy sees it. The sidecar must see the
#   Authorization header in plaintext to swap the placeholder. Keeping the
#   bridge-to-sidecar hop on loopback plain HTTP achieves this without any
#   TLS certificate management. The upstream leg (sidecar → Graph) is HTTPS.
#
# Bridge/skill configuration:
#   Set GRAPH_SIDECAR_URL=http://127.0.0.1:8766 (or SIDECAR_LISTEN_PORT override).
#   The bridge replaces https://graph.microsoft.com/v1.0 with this URL.
#   NO_PROXY must include 127.0.0.1 so the client connects directly (not via
#   the OpenShell proxy) — this is already set in start.sh.

import asyncio
import logging
import os
import sys

import aiohttp
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="[outlook-sidecar] %(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

OUTLOOK_TOKEN_PLACEHOLDER = "OUTLOOK_TOKEN_PLACEHOLDER"
GRAPH_UPSTREAM_BASE = "https://graph.microsoft.com"

TOKEN_MANAGER_HOST = os.environ.get("TOKEN_MANAGER_HOST", "host.docker.internal")
TOKEN_MANAGER_PORT = int(os.environ.get("TOKEN_MANAGER_PORT", "8765"))
# Resolved by OpenShell at container init from the provider store.
SESSION_ID = os.environ.get("OUTLOOK_SESSION_UUID")

# Inside the sandbox the sidecar is loopback-only; in the test container we
# bind to 0.0.0.0 so the published port is reachable from the host.
LISTEN_HOST = os.environ.get("SIDECAR_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("SIDECAR_LISTEN_PORT", "8766"))
REFRESH_INTERVAL = 55 * 60  # seconds

# Headers that must not be forwarded between client and upstream
_HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "proxy-connection",
    "transfer-encoding", "te", "trailer", "upgrade",
    "proxy-authorization", "proxy-authenticate",
    "host", "content-length",
])

_live_token: str | None = None
_token_refresh_lock: asyncio.Lock | None = None


# ── Token management ──────────────────────────────────────────────────────────

async def fetch_token() -> str:
    # No explicit proxy: the sidecar's inherited HTTP_PROXY points directly to
    # the OpenShell L7 proxy, so OpenShell correctly attributes this connection
    # to /usr/local/bin/outlook-credential-sidecar for policy enforcement.
    # (Routing through the decode-proxy would mis-attribute to python3.11.)
    if not SESSION_ID:
        raise ValueError("OUTLOOK_SESSION_UUID not set — cannot fetch token without session_id")
    # Pass the session UUID in a header, not a query param.
    # OpenShell's plain-HTTP forward proxy resolves openshell:resolve:env:* placeholders
    # only in HTTP headers, not in URL query strings. Putting SESSION_ID (which may be a
    # placeholder string when running inside the sandbox) in X-Session-Id ensures the proxy
    # rewrites it to the real UUID before the request reaches the token manager.
    token_url = f"http://{TOKEN_MANAGER_HOST}:{TOKEN_MANAGER_PORT}/token"
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.get(
            token_url,
            headers={"X-Session-Id": SESSION_ID},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            if "access_token" not in data:
                raise ValueError(f"Token manager returned no access_token: {data}")
            return data["access_token"]


async def refresh_token(reason: str, stale_token: str | None = None) -> bool:
    global _live_token, _token_refresh_lock
    if _token_refresh_lock is None:
        _token_refresh_lock = asyncio.Lock()
    async with _token_refresh_lock:
        if stale_token is not None and _live_token != stale_token:
            # Another coroutine already refreshed while we were waiting for the lock
            return True
        try:
            _live_token = await fetch_token()
            log.info("Token refreshed (%s, len=%d)", reason, len(_live_token))
            return True
        except Exception as exc:
            log.error("Token refresh failed (%s): %s", reason, exc)
            return False


async def initial_token_loop() -> None:
    """Keep retrying the initial token fetch until it succeeds.

    Runs as a background task so the HTTP server binds port 8766 immediately
    (the startup token fetch no longer delays site.start()). Once the first
    token is in hand, this task exits and the regular refresh_loop takes over.
    """
    attempt = 0
    while _live_token is None:
        attempt += 1
        if await refresh_token("initial acquisition"):
            log.info("Initial token acquired after %d attempt(s)", attempt)
            return
        wait = min(2 ** attempt, 15)
        log.warning(
            "Failed to fetch initial token (attempt %d); retrying in %ds",
            attempt, wait,
        )
        await asyncio.sleep(wait)


async def refresh_loop() -> None:
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        log.info("Refreshing token from token manager…")
        ok = await refresh_token("scheduled refresh")
        if not ok:
            log.error("Continuing with existing token after scheduled refresh failure")


# ── Request forwarding ────────────────────────────────────────────────────────

async def handle(request: web.Request) -> web.StreamResponse:
    # Reconstruct the upstream URL: replace sidecar host with graph.microsoft.com
    upstream_url = GRAPH_UPSTREAM_BASE + str(request.rel_url)

    # Forward all non-hop-by-hop headers, fix Host
    base_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    base_headers["Host"] = "graph.microsoft.com"

    auth = base_headers.get("Authorization", "")
    uses_placeholder = OUTLOOK_TOKEN_PLACEHOLDER in auth

    body = await request.read()

    # auto_decompress=False: pass compressed bytes through as-is so the client
    # can decompress them itself. Without this, aiohttp decompresses the body
    # but the Content-Encoding header is still forwarded, confusing the client.
    async with aiohttp.ClientSession(auto_decompress=False, trust_env=True) as session:
        # Allow one reactive refresh+retry if Graph returns 401 on an
        # authenticated request. Non-placeholder requests are not retried.
        retry_after_refresh = uses_placeholder
        try:
            while True:
                fwd_headers = dict(base_headers)
                if uses_placeholder:
                    if _live_token:
                        fwd_headers["Authorization"] = f"Bearer {_live_token}"
                    else:
                        log.warning("Placeholder in request but no live token available; forwarding as-is")

                async with session.request(
                    method=request.method,
                    url=upstream_url,
                    headers=fwd_headers,
                    data=body or None,
                    timeout=aiohttp.ClientTimeout(total=60),
                    allow_redirects=False,
                    ssl=True,
                ) as upstream:
                    resp_headers = {
                        k: v for k, v in upstream.headers.items()
                        if k.lower() not in _HOP_BY_HOP
                    }

                    if upstream.status == 401 and retry_after_refresh:
                        error_body = await upstream.read()
                        log.warning("Graph returned 401; refreshing token and retrying once")
                        retry_after_refresh = False
                        stale = fwd_headers.get("Authorization", "").removeprefix("Bearer ")
                        if await refresh_token("Graph 401 retry", stale_token=stale):
                            continue
                        # Refresh failed — return the original 401
                        return web.Response(
                            status=401,
                            headers=resp_headers,
                            body=error_body,
                        )

                    response = web.StreamResponse(
                        status=upstream.status,
                        headers=resp_headers,
                    )
                    await response.prepare(request)
                    async for chunk in upstream.content.iter_chunked(65536):
                        await response.write(chunk)
                    await response.write_eof()
                    return response
        except aiohttp.ClientError as exc:
            log.error("Upstream request failed: %s %s → %s", request.method, upstream_url, exc)
            return web.Response(status=502, text=f"Bad Gateway: {exc}")


# ── Startup ───────────────────────────────────────────────────────────────────

async def on_startup(app: web.Application) -> None:
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "(direct)"
    log.info(
        "Starting token acquisition from %s:%d via %s (background)…",
        TOKEN_MANAGER_HOST, TOKEN_MANAGER_PORT, proxy,
    )
    # Launch token fetch as a background task so site.start() runs immediately
    # and port 8766 opens before the first retry completes. This prevents the
    # bridge (which waits for port 8766) from getting ConnectError on startup.
    loop = asyncio.get_event_loop()
    loop.create_task(initial_token_loop(), name="token-init")
    loop.create_task(refresh_loop(), name="token-refresh")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    app = web.Application()
    app.router.add_route("*", "/{path_info:.*}", handle)
    app.on_startup.append(on_startup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()

    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "(direct)"
    log.info(
        "Credential sidecar on %s:%d → %s (proxy: %s)",
        LISTEN_HOST, LISTEN_PORT, GRAPH_UPSTREAM_BASE, proxy,
    )

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
