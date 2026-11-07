#!/usr/bin/env python3
"""Read-only Nellis Auction public catalog.

Lists open lots from the search index the public site publishes in
``window.ENV``, then reads each lot's current bid from the public product
data route (``/p/item/{id}.data``). No login, bidding, or account calls.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

HOME_URL = "https://www.nellisauction.com/"
USER_AGENT = (
    "BotDeskNellis/1.0 (read-only public catalog check; +https://www.nellisauction.com/)"
)
DEFAULT_LIMIT = 8
MAX_LIMIT = 20
_CONFIG_LOCK = threading.Lock()
_ALGOLIA_CONFIG = None


class NellisError(Exception):
    """Public Nellis data could not be read."""


def fetch_public_lots(limit=DEFAULT_LIMIT):
    """Return a small list of current public lots.

    Each lot includes title, current bid when the public product payload
    shows one, suggested retail, end time, and a link to the lot page.
    """
    size = _clamp_limit(limit)
    config = _algolia_config()
    hits = _search_open_lots(config, size)
    lots = _enrich_lots(hits)
    lots.sort(key=lambda lot: lot.get("end_time") or "")
    return {
        "ok": True,
        "source": {
            "site": HOME_URL,
            "catalog": "Public Algolia index published as window.ENV on the Nellis homepage",
            "index": config["index"],
            "current_bid": "https://www.nellisauction.com/p/item/{id}.data",
        },
        "retrieved_at": _iso(time.time()),
        "count": len(lots),
        "lots": lots,
    }


def render_lots_page(payload):
    """Render a standalone check page. It does not share the blotter markup."""
    if not payload.get("ok"):
        body = f"<p class=\"error\">{html.escape(payload.get('error') or 'Unknown error')}</p>"
    else:
        rows = []
        for lot in payload.get("lots") or []:
            bid = _money(lot.get("current_bid"))
            if lot.get("current_bid") is None:
                bid = "not shown"
            retail = _money(lot.get("suggested_retail"))
            end = html.escape(lot.get("end_time") or "")
            title = html.escape(lot.get("title") or "Untitled lot")
            url = html.escape(lot.get("url") or "", quote=True)
            location = html.escape(lot.get("location") or "")
            status = html.escape(lot.get("market_status") or "")
            auction = html.escape(lot.get("auction") or "")
            rows.append(
                "<tr>"
                f"<td><a href=\"{url}\">{title}</a>"
                f"<div class=\"meta\">{auction}</div></td>"
                f"<td>{bid}</td>"
                f"<td>{retail}</td>"
                f"<td>{end}</td>"
                f"<td>{location}</td>"
                f"<td>{status}</td>"
                "</tr>"
            )
        table = (
            "<table>"
            "<thead><tr><th>Title</th><th>Current bid</th><th>Retail</th>"
            "<th>Ends (UTC)</th><th>Location</th><th>Status</th></tr></thead>"
            f"<tbody>{''.join(rows) or '<tr><td colspan=\"6\">No open lots returned.</td></tr>'}</tbody>"
            "</table>"
        )
        body = (
            f"<p>{payload.get('count', 0)} public lots with a close time still in the future. "
            "Current bid comes from the public product data route. "
            "Retail is the suggested price on the public search record.</p>"
            f"<div class=\"table-wrap\">{table}</div>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nellis public lots</title>
  <style>
    body {{ margin: 0; font: 16px/1.45 Georgia, serif; color: #1c1915; background: #f6f1e8; }}
    main {{ width: min(1100px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
    h1 {{ font-weight: normal; margin-bottom: 8px; }}
    a {{ color: #6d3b1f; }}
    .meta {{ color: #6d645b; font: 13px/1.3 ui-sans-serif, sans-serif; margin-top: 4px; }}
    .error {{ color: #8c2f22; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; background: #fffdf8; }}
    th, td {{ text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid #e4d8c8; }}
    th {{ font: 600 12px/1.2 ui-sans-serif, sans-serif; letter-spacing: 0.04em; text-transform: uppercase; color: #6d645b; }}
  </style>
</head>
<body>
  <main>
    <h1>Nellis Auction — public lots</h1>
    <p class="meta">Read-only check. This page is separate from the Bot Desk blotter at <a href="/">/</a>. JSON: <a href="/nellis.json">/nellis.json</a>.</p>
    {body}
  </main>
</body>
</html>
"""


def _clamp_limit(limit):
    try:
        size = int(limit)
    except (TypeError, ValueError) as exc:
        raise NellisError("limit must be an integer") from exc
    if size < 1:
        raise NellisError("limit must be at least 1")
    return min(size, MAX_LIMIT)


def _algolia_config():
    global _ALGOLIA_CONFIG
    with _CONFIG_LOCK:
        if _ALGOLIA_CONFIG is not None:
            return _ALGOLIA_CONFIG
        status, _ctype, body = _request(HOME_URL, timeout=25)
        if status != 200:
            raise NellisError(
                f"Nellis homepage returned HTTP {status}. "
                "Automated access to the public catalog page was not available."
            )
        text = body.decode("utf-8", errors="replace")
        marker = "window.ENV = "
        start = text.find(marker)
        if start < 0:
            raise NellisError(
                "Nellis homepage did not include the public window.ENV search settings. "
                "The page may be blocked or changed."
            )
        brace = text.find("{", start)
        try:
            env, _end = json.JSONDecoder().raw_decode(text[brace:])
        except json.JSONDecodeError as exc:
            raise NellisError("Could not read public window.ENV from the Nellis homepage.") from exc
        app_id = env.get("APP_PUBLIC_ALGOLIA_ID") or ""
        api_key = env.get("APP_PUBLIC_ALGOLIA_API_KEY") or ""
        index = env.get("APP_PUBLIC_ALGOLIA_INDEX") or ""
        if not (app_id and api_key and index):
            raise NellisError("Nellis homepage is missing the public Algolia search settings.")
        _ALGOLIA_CONFIG = {"app_id": app_id, "api_key": api_key, "index": index}
        return _ALGOLIA_CONFIG


def _search_open_lots(config, limit):
    now = int(time.time())
    payload = {
        "query": "",
        "hitsPerPage": limit,
        "numericFilters": [f"Date Closed > {now}"],
        "attributesToRetrieve": [
            "Lead Description",
            "Suggested Retail",
            "Date Closed",
            "objectID",
            "Auction Event Name",
            "Location Name",
            "Shopping Location",
        ],
    }
    url = (
        f"https://{config['app_id']}-dsn.algolia.net/1/indexes/"
        f"{urllib.request.quote(config['index'])}/query"
    )
    status, _ctype, body = _request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Algolia-Application-Id": config["app_id"],
            "X-Algolia-API-Key": config["api_key"],
        },
        timeout=25,
    )
    if status != 200:
        raise NellisError(
            f"Public Nellis search index returned HTTP {status}. "
            "The read-only catalog query was refused."
        )
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise NellisError("Public Nellis search index did not return JSON.") from exc
    hits = data.get("hits")
    if not isinstance(hits, list):
        raise NellisError("Public Nellis search index returned no lot list.")
    return hits


def _enrich_lots(hits):
    prepared = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        lot_id = str(hit.get("objectID") or "").strip()
        if not lot_id:
            continue
        prepared.append((lot_id, hit))
    if not prepared:
        return []

    details = {}
    workers = min(4, len(prepared))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_lot_detail, lot_id): lot_id for lot_id, _hit in prepared}
        for future in as_completed(futures):
            lot_id = futures[future]
            try:
                details[lot_id] = future.result()
            except NellisError:
                details[lot_id] = None

    lots = []
    for lot_id, hit in prepared:
        detail = details.get(lot_id) or {}
        end_raw = detail.get("close_time")
        if end_raw is None:
            end_raw = hit.get("Date Closed")
        title = detail.get("title") or hit.get("Lead Description") or "Untitled lot"
        current = detail.get("current_price")
        lots.append(
            {
                "id": lot_id,
                "title": title,
                "current_bid": current,
                "suggested_retail": _number(hit.get("Suggested Retail")),
                "end_time": _iso(end_raw),
                "url": f"https://www.nellisauction.com/p/item/{urllib.request.quote(lot_id)}",
                "location": hit.get("Shopping Location") or hit.get("Location Name") or "",
                "auction": hit.get("Auction Event Name") or "",
                "market_status": detail.get("market_status") or "",
                "bid_count": detail.get("bid_count"),
            }
        )
    return lots


def _fetch_lot_detail(lot_id):
    url = f"https://www.nellisauction.com/p/item/{urllib.request.quote(lot_id)}.data"
    status, _ctype, body = _request(url, timeout=20)
    if status != 200:
        return None
    text = body.decode("utf-8", errors="replace").lstrip()
    if not text.startswith("["):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _product_fields(payload)


def _product_fields(payload):
    """Pull title and current price out of a React Router single-fetch payload."""
    if not isinstance(payload, list):
        return None
    for item in payload:
        if not isinstance(item, dict) or not item:
            continue
        mapped = {}
        valid = True
        for raw_key, value_index in item.items():
            if not isinstance(raw_key, str) or not raw_key.startswith("_"):
                valid = False
                break
            try:
                key_index = int(raw_key[1:])
            except ValueError:
                valid = False
                break
            if key_index < 0 or key_index >= len(payload) or not isinstance(payload[key_index], str):
                valid = False
                break
            mapped[payload[key_index]] = value_index
        if not valid or "currentPrice" not in mapped or "title" not in mapped:
            continue
        return {
            "title": _resolve(payload, mapped.get("title")),
            "current_price": _number(_resolve(payload, mapped.get("currentPrice"))),
            "close_time": _resolve(payload, mapped.get("closeTime")),
            "market_status": _resolve(payload, mapped.get("marketStatus")),
            "bid_count": _resolve(payload, mapped.get("bidCount")),
        }
    return None


def _resolve(payload, index):
    if isinstance(index, bool) or not isinstance(index, int):
        return index
    if index < 0 or index >= len(payload):
        return None
    value = payload[index]
    if isinstance(value, list) and len(value) == 2 and value[0] == "D":
        return value[1]
    if isinstance(value, (str, int, float)) or value is None:
        return value
    return None


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return None
    return None


def _iso(value):
    if isinstance(value, list) and len(value) == 2 and value[0] == "D":
        value = value[1]
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value or None
    if not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if seconds > 10_000_000_000:
        seconds = seconds / 1000.0
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(value):
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"${value:,.2f}"
    if isinstance(value, (int, float)):
        return f"${value:,.0f}"
    return html.escape(str(value))


def _request(url, data=None, headers=None, timeout=20):
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=req_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()
    except urllib.error.URLError as exc:
        raise NellisError(f"Could not reach Nellis: {exc.reason}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="List current public Nellis Auction lots.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="lots to return (max 20)")
    args = parser.parse_args(argv)
    try:
        payload = fetch_public_lots(args.limit)
    except NellisError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
