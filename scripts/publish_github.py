#!/usr/bin/env python3
"""Create saleemakthar-star/bot-desk and enable GitHub Pages using GITHUB_TOKEN."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

OWNER = "saleemakthar-star"
REPO = "bot-desk"
API = "https://api.github.com"
PAGES_URL = f"https://{OWNER}.github.io/{REPO}/"


def token() -> str:
    value = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not value:
        sys.stderr.write(
            "GITHUB_TOKEN is not set. Export a classic PAT with repo and workflow scopes,\n"
            "or a fine-grained token that can create repositories and manage Pages.\n"
        )
        sys.exit(2)
    return value


def request(method: str, path: str, tok: str, body: dict | None = None, ok: tuple[int, ...] = (200, 201)):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bot-desk-publisher",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            payload = json.loads(raw) if raw else {}
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        if exc.code in ok:
            return exc.code, json.loads(raw) if raw else {}
        sys.stderr.write(f"{method} {path} -> {exc.code}\n{raw}\n")
        raise


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    tok = token()
    status, user = request("GET", "/user", tok)
    login = user.get("login")
    print(f"Authenticated as {login}")
    if login and login.lower() != OWNER:
        print(f"Warning: token user is {login}, repo owner requested is {OWNER}")

    try:
        status, repo = request("GET", f"/repos/{OWNER}/{REPO}", tok, ok=(200, 404))
    except urllib.error.HTTPError:
        raise

    if status == 404:
        print(f"Creating public repo {OWNER}/{REPO}")
        body = {
            "name": REPO,
            "description": "Bot Desk — Robinhood agentic trading dashboard",
            "homepage": PAGES_URL,
            "private": False,
            "has_issues": False,
            "has_projects": False,
            "has_wiki": False,
            "auto_init": False,
        }
        path = f"/orgs/{OWNER}/repos" if user.get("type") == "Organization" else "/user/repos"
        if login and login.lower() == OWNER:
            path = "/user/repos"
        _, repo = request("POST", path, tok, body, ok=(201, 422))
    else:
        print(f"Repo already exists: {repo.get('html_url')}")

    remote = f"https://x-access-token:{tok}@github.com/{OWNER}/{REPO}.git"
    remotes = subprocess.check_output(["git", "remote"], text=True).split()
    if "github" in remotes:
        run(["git", "remote", "set-url", "github", remote])
    else:
        run(["git", "remote", "add", "github", remote])
    run(["git", "push", "-u", "github", "HEAD:main"])

    print("Enabling GitHub Pages (workflow)")
    try:
        request(
            "POST",
            f"/repos/{OWNER}/{REPO}/pages",
            tok,
            {"build_type": "workflow"},
            ok=(201, 204, 409),
        )
    except urllib.error.HTTPError as exc:
        if exc.code not in (409,):
            request(
                "PUT",
                f"/repos/{OWNER}/{REPO}/pages",
                tok,
                {"build_type": "workflow"},
                ok=(204, 200),
            )

    print("Waiting for Pages")
    for _ in range(24):
        try:
            st, pages = request("GET", f"/repos/{OWNER}/{REPO}/pages", tok, ok=(200, 404))
            if st == 200:
                html_url = pages.get("html_url") or PAGES_URL
                print(html_url)
                print(json.dumps(pages, indent=2))
                return
        except urllib.error.HTTPError:
            pass
        time.sleep(5)
    print(PAGES_URL)


if __name__ == "__main__":
    main()
