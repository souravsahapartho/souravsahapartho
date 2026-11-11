#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub API (stdlib only).

Env:
  GH_TOKEN   token used for the API (the workflow passes SUMMARY_CARDS_TOKEN or GITHUB_TOKEN)
  GH_LOGIN   GitHub login (default: souravsahapartho)

On any failure the existing JSON is left untouched and the script exits non-zero,
so a bad API day never wipes the cards.
"""
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "contributions.json"
LOGIN = os.environ.get("GH_LOGIN", "souravsahapartho")
TOKEN = os.environ.get("GH_TOKEN", "")

GQL = """query($login: String!) { user(login: $login) {
  name
  contributionsCollection { contributionCalendar { totalContributions
    weeks { contributionDays { date contributionCount } } } }
} }"""


def call(url, body=None):
    headers = {"User-Agent": "profile-cards", "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"bearer {TOKEN}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def main():
    if not TOKEN:
        sys.exit("GH_TOKEN is required")

    out = call("https://api.github.com/graphql", {"query": GQL, "variables": {"login": LOGIN}})
    if out.get("errors"):
        sys.exit("GraphQL error: " + "; ".join(e["message"] for e in out["errors"]))
    user = out["data"]["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = sorted(
        (d["date"], d["contributionCount"])
        for w in cal["weeks"]
        for d in w["contributionDays"]
    )

    repos = {}
    page = 1
    while True:
        batch = call(f"https://api.github.com/users/{LOGIN}/repos?per_page=100&type=owner&sort=pushed&page={page}")
        for r in batch:
            if r["fork"]:
                continue
            repos[r["name"]] = {
                "description": r.get("description"),
                "language": r.get("language"),
                "stars": r["stargazers_count"],
                "forks": r["forks_count"],
                "url": r["html_url"],
                "pushed": r["pushed_at"][:10],
            }
        if len(batch) < 100:
            break
        page += 1

    payload = {
        "login": LOGIN,
        "name": user.get("name"),
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        "total": cal["totalContributions"],
        "days": [list(d) for d in days],
        "repos": repos,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(days)} days, {payload['total']} contributions, {len(repos)} repos")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # keep the old file on any failure
        sys.exit(f"fetch failed: {exc}")
