"""Nellis Google sign-in.

A signed-out or failed watch-list connection reconnects through
https://www.nellisauction.com/oauth/google. NELLIS_USERNAME and
NELLIS_PASSWORD are that Google account, read only from the environment.
They are never printed, logged, or written into the repository.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from nellis.connector import USER_AGENT, NellisError, _iso

SESSION_FILE = Path("/tmp/nellis-connector-session")
RECONNECT_PROFILE = Path("/tmp/nellis-reconnect-profile")
RECONNECT_COOKIE = Path("/tmp/nellis-reconnect-cookie")
RECONNECT_SCRIPT = Path(__file__).with_name("google_reconnect.mjs")
MISSING_GOOGLE_ACCOUNT = (
    "Nellis is signed out and cannot reconnect. "
    "NELLIS_USERNAME and NELLIS_PASSWORD are not set in the environment. "
    "Those are the Google account for Nellis sign-in, not a Nellis username and password."
)

NELLIS_GOOGLE_START = (
    "https://www.nellisauction.com/oauth/google"
    "?intent=login&redirect=%2Fdashboard%2Fauctions%2Fwatchlist"
)
WATCHLIST_URL = "https://www.nellisauction.com/dashboard/auctions/watchlist"
GOOGLE_AUTH_HOST = "accounts.google.com"


def google_account_configured():
    """True when both Google sign-in values are present. Values are not returned."""
    return bool(os.environ.get("NELLIS_USERNAME")) and bool(os.environ.get("NELLIS_PASSWORD"))


def watchlist_status():
    """Return the signed-in watch list, reconnecting once if the session is out."""
    try:
        items = fetch_watchlist_items()
    except NellisError as exc:
        return _watchlist_error(str(exc))
    return {
        "ok": True,
        "sign_in_url": NELLIS_GOOGLE_START,
        "watchlist_url": WATCHLIST_URL,
        "count": len(items),
        "items": items,
    }


def fetch_watchlist_items():
    """Read the watch list. A signed-out or failed read reconnects once."""
    session = _read_session()
    if session:
        try:
            items = _watchlist_items(session)
        except NellisError:
            items = None
        if items is not None:
            return items
    session = reconnect_google_session()
    items = _watchlist_items(session)
    if items is None:
        raise NellisError("Nellis watch list was still signed out after Google reconnect.")
    return items


def reconnect_google_session():
    """Sign in through Nellis's Google OAuth URL using the environment account."""
    if not google_account_configured():
        raise NellisError(MISSING_GOOGLE_ACCOUNT)
    RECONNECT_PROFILE.mkdir(parents=True, exist_ok=True)
    if RECONNECT_COOKIE.exists():
        RECONNECT_COOKIE.unlink()
    try:
        completed = subprocess.run(
            ["node", str(RECONNECT_SCRIPT), str(RECONNECT_PROFILE), str(RECONNECT_COOKIE)],
            cwd=str(RECONNECT_SCRIPT.parent),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NellisError("Nellis Google reconnect timed out.") from exc
    payload = _reconnect_payload(completed.stdout)
    if completed.returncode != 0 or not payload.get("ok"):
        raise NellisError(payload.get("error") or "Nellis Google reconnect failed.")
    if not RECONNECT_COOKIE.is_file():
        raise NellisError("Nellis Google reconnect did not return a session.")
    session = RECONNECT_COOKIE.read_text(encoding="utf-8").strip()
    RECONNECT_COOKIE.unlink(missing_ok=True)
    if not session:
        raise NellisError("Nellis Google reconnect did not return a session.")
    SESSION_FILE.write_text(session, encoding="utf-8")
    SESSION_FILE.chmod(0o600)
    return session


def _watchlist_error(message):
    return {
        "ok": False,
        "error": message,
        "sign_in_url": NELLIS_GOOGLE_START,
        "watchlist_url": WATCHLIST_URL,
        "items": [],
    }


def _read_session():
    if not SESSION_FILE.is_file():
        return ""
    return SESSION_FILE.read_text(encoding="utf-8").strip()


def _watchlist_items(session):
    """Return items, or None when this session is signed out."""
    status, location, body = _signed_request(
        "https://www.nellisauction.com/dashboard/auctions/watchlist.data",
        session,
    )
    if _signed_out(status, location, body):
        return None
    if status != 200:
        raise NellisError(f"Nellis watch list returned HTTP {status}.")
    text = body.decode("utf-8", errors="replace").lstrip()
    if not text.startswith("["):
        if "login" in text[:500].lower():
            return None
        raise NellisError("Nellis watch list did not return a session payload.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NellisError("Nellis watch list payload was not JSON.") from exc
    return _lots_from_payload(payload)


def _signed_out(status, location, body):
    target = location or ""
    if status in (301, 302, 303, 307, 308) and "/login" in target:
        return True
    sample = body[:800].decode("utf-8", errors="replace").lower()
    return "routes/login" in sample or "please log in" in sample


def _signed_request(url, session):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/x-script, application/json, */*",
            "Cookie": f"__session={session}",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=25) as response:
            return response.status, response.headers.get("Location"), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location"), exc.read()
    except urllib.error.URLError as exc:
        raise NellisError(f"Could not reach the Nellis watch list: {exc.reason}") from exc


def _lots_from_payload(data):
    if not isinstance(data, list):
        return []
    lots = []
    for item in data:
        if not _is_ref_object(item):
            continue
        keys = {}
        valid = True
        for raw_key, value_index in item.items():
            try:
                key_index = int(raw_key[1:])
            except ValueError:
                valid = False
                break
            if key_index < 0 or key_index >= len(data) or not isinstance(data[key_index], str):
                valid = False
                break
            keys[data[key_index]] = value_index
        if not valid or "title" not in keys or "currentPrice" not in keys or "id" not in keys:
            continue
        title = _at(data, keys["title"])
        lot_id = _at(data, keys["id"])
        if not isinstance(title, str) or not lot_id:
            continue
        lots.append(
            {
                "id": lot_id,
                "title": title,
                "current_bid": _at(data, keys.get("currentPrice")),
                "end_time": _iso(_at(data, keys.get("closeTime"))),
                "url": f"https://www.nellisauction.com/p/item/{lot_id}",
            }
        )
    lots.sort(key=lambda lot: lot.get("end_time") or "")
    return lots


def _at(data, index):
    if not isinstance(index, int) or index < 0 or index >= len(data):
        return None
    return data[index]


def _is_ref_object(value):
    return isinstance(value, dict) and value and all(
        isinstance(key, str) and key.startswith("_") and key[1:].lstrip("-").isdigit()
        for key in value
    )


def _reconnect_payload(stdout):
    line = (stdout or "").strip().splitlines()
    if not line:
        return {}
    try:
        payload = json.loads(line[-1])
    except json.JSONDecodeError:
        return {}
    return _redact_account(payload)


def _redact_account(payload):
    """Drop the Google account values if a child process echoed them."""
    username = os.environ.get("NELLIS_USERNAME") or ""
    password = os.environ.get("NELLIS_PASSWORD") or ""
    error = str(payload.get("error") or "")
    for secret in (password, username):
        if secret:
            error = error.replace(secret, "[redacted]")
    payload["error"] = error
    return payload


def fresh_google_prompt_url():
    """Return the accounts.google.com URL Nellis just issued.

    Nellis mints a one-time state value on each visit. The caller should
    redirect the browser there and stop.
    """
    request = urllib.request.Request(
        NELLIS_GOOGLE_START,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            status = response.status
            location = response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        status = exc.code
        location = exc.headers.get("Location")
    except urllib.error.URLError as exc:
        raise NellisError(f"Could not reach Nellis Google sign-in: {exc.reason}") from exc
    if status not in (301, 302, 303, 307, 308) or not location:
        raise NellisError(
            "Nellis did not redirect Continue with Google to Google's own prompt."
        )
    if not _is_google_prompt(location):
        raise NellisError(
            "Nellis Google sign-in did not redirect to accounts.google.com."
        )
    return location


def render_watchlist_page():
    status = watchlist_status()
    sign_in = html.escape(status["sign_in_url"], quote=True)
    watch = html.escape(status["watchlist_url"], quote=True)
    if status.get("ok"):
        rows = []
        for item in status.get("items") or []:
            title = html.escape(item.get("title") or "Untitled lot")
            url = html.escape(item.get("url") or "", quote=True)
            bid = html.escape(_bid_text(item.get("current_bid")))
            end = html.escape(item.get("end_time") or "")
            rows.append(f"<li><a href=\"{url}\">{title}</a> — {bid} — {end}</li>")
        detail = "<ul>" + "".join(rows) + "</ul>" if rows else "<p>The watch list is empty.</p>"
    else:
        detail = f"<p class=\"error\">{html.escape(status.get('error') or 'Watch list unavailable.')}</p>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nellis watch list sign-in</title>
  <style>
    body {{ margin: 0; font: 16px/1.45 Georgia, serif; color: #1c1915; background: #f6f1e8; }}
    main {{ width: min(720px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
    h1 {{ font-weight: normal; }}
    a {{ color: #6d3b1f; }}
    .meta {{ color: #6d645b; font: 13px/1.3 ui-sans-serif, sans-serif; }}
    .error {{ color: #8c2f22; }}
    .action {{ display: inline-block; margin-top: 12px; padding: 10px 14px; background: #6d3b1f; color: #fffaf3; text-decoration: none; }}
  </style>
</head>
<body>
  <main>
    <h1>Nellis watch list</h1>
    <p class="meta">Public lots stay at <a href="/nellis">/nellis</a>. The blotter stays at <a href="/">/</a>.</p>
    {detail}
    <p>A signed-out or failed watch-list connection reconnects through Nellis Google sign-in. NELLIS_USERNAME and NELLIS_PASSWORD are that Google account, read only from the environment. This page has no password field.</p>
    <p><a class="action" href="/nellis/google">Continue with Google</a></p>
    <p class="meta">Nellis start URL: <a href="{sign_in}">{sign_in}</a></p>
    <p class="meta">After Google sends you back, Nellis opens <a href="{watch}">the watch list</a>.</p>
  </main>
</body>
</html>
"""


def watchlist_status_json():
    return json.dumps(watchlist_status(), indent=2)


def _bid_text(value):
    if isinstance(value, bool) or value is None:
        return "not shown"
    if isinstance(value, float) and not value.is_integer():
        return f"${value:,.2f}"
    if isinstance(value, (int, float)):
        return f"${value:,.0f}"
    return str(value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_google_prompt(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != GOOGLE_AUTH_HOST:
        return False
    if not parsed.path.startswith("/o/oauth2/"):
        return False
    query = parse_qs(parsed.query)
    redirect_uri = (query.get("redirect_uri") or [""])[0]
    return redirect_uri == "https://www.nellisauction.com/oauth/google/callback"
