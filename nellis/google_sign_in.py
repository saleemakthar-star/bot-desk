"""Hand off to Nellis Auction's own Google sign-in.

The browser is sent to Google's OAuth prompt. This module does not ask for,
store, or send a Google password.
"""

from __future__ import annotations

import html
import json
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

from nellis.connector import USER_AGENT, NellisError

NELLIS_GOOGLE_START = (
    "https://www.nellisauction.com/oauth/google"
    "?intent=login&redirect=%2Fdashboard%2Fauctions%2Fwatchlist"
)
WATCHLIST_URL = "https://www.nellisauction.com/dashboard/auctions/watchlist"
GOOGLE_AUTH_HOST = "accounts.google.com"


def watchlist_status():
    """Watch-list items are available only after the user finishes Google's prompt."""
    return {
        "ok": False,
        "error": (
            "The watch list requires Nellis Google sign-in. "
            "Open the sign-in URL and finish it in Google's own prompt. "
            "This app does not take a Google password."
        ),
        "sign_in_url": NELLIS_GOOGLE_START,
        "watchlist_url": WATCHLIST_URL,
        "items": [],
    }


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
    .action {{ display: inline-block; margin-top: 12px; padding: 10px 14px; background: #6d3b1f; color: #fffaf3; text-decoration: none; }}
  </style>
</head>
<body>
  <main>
    <h1>Nellis watch list</h1>
    <p class="meta">Public lots stay at <a href="/nellis">/nellis</a>. The blotter stays at <a href="/">/</a>.</p>
    <p>Sign in with the Google account named firdous. The next step is Google's own prompt. This page does not ask for a Google password.</p>
    <p><a class="action" href="/nellis/google">Continue with Google</a></p>
    <p class="meta">Nellis start URL: <a href="{sign_in}">{sign_in}</a></p>
    <p class="meta">After Google sends you back, Nellis opens <a href="{watch}">the watch list</a>.</p>
  </main>
</body>
</html>
"""


def watchlist_status_json():
    return json.dumps(watchlist_status(), indent=2)


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
