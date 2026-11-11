#!/usr/bin/env python3
"""Refresh data/contributions.json (stdlib only).

Pulls, each independently:
  * contribution calendar for the last year             (required)
  * public, non-fork repo stats
  * per-year totals since you joined -> all-time total
  * pinned repositories
  * latest articles from the blog RSS feed

Env:
  GH_TOKEN   token used for the API (the workflow passes SUMMARY_CARDS_TOKEN or GITHUB_TOKEN)
  GH_LOGIN   GitHub login (default: souravsahapartho)
  BLOG_FEED  RSS feed (default: https://blog.sourav.com.bd/rss.xml)

If the calendar fails the script exits non-zero and leaves the file alone. If an optional
section fails, its previous value is kept and a warning is printed.
"""
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "contributions.json"
LOGIN = os.environ.get("GH_LOGIN", "souravsahapartho")
TOKEN = os.environ.get("GH_TOKEN", "")
FEED = os.environ.get("BLOG_FEED", "https://blog.sourav.com.bd/rss.xml")
MAX_ARTICLES = 5


def call(url, body=None, auth=True):
    headers = {"User-Agent": "profile-cards", "Accept": "application/vnd.github+json"}
    if auth and TOKEN:
        headers["Authorization"] = f"bearer {TOKEN}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def gql(query, variables=None):
    out = json.loads(call("https://api.github.com/graphql", {"query": query, "variables": variables or {"login": LOGIN}}))
    if out.get("errors"):
        raise RuntimeError("; ".join(e["message"] for e in out["errors"]))
    return out["data"]


def rest(path):
    return json.loads(call(f"https://api.github.com{path}"))


# ----------------------------------------------------------------------------- sections
def calendar():
    d = gql("""query($login: String!) { user(login: $login) { name
      contributionsCollection { contributionCalendar { totalContributions
        weeks { contributionDays { date contributionCount } } } } } }""")["user"]
    cal = d["contributionsCollection"]["contributionCalendar"]
    days = sorted((x["date"], x["contributionCount"]) for w in cal["weeks"] for x in w["contributionDays"])
    return {"name": d.get("name"), "total": cal["totalContributions"], "days": [list(x) for x in days]}


def repos():
    out, page = {}, 1
    while True:
        batch = rest(f"/users/{LOGIN}/repos?per_page=100&type=owner&sort=pushed&page={page}")
        for r in batch:
            if r["fork"]:
                continue
            out[r["name"]] = {
                "description": r.get("description"), "language": r.get("language"),
                "stars": r["stargazers_count"], "forks": r["forks_count"],
                "url": r["html_url"], "pushed": r["pushed_at"][:10],
            }
        if len(batch) < 100:
            return {"repos": out}
        page += 1


def years():
    """All-time total = sum of one calendar-year window per year since the account was created."""
    u = gql("query($login: String!) { user(login: $login) { createdAt contributionsCollection { contributionYears } } }")["user"]
    ys = sorted(u["contributionsCollection"]["contributionYears"])
    now = dt.datetime.now(dt.timezone.utc)
    parts = []
    for y in ys:
        end = min(dt.datetime(y, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc), now)
        parts.append(f'y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{end.strftime("%Y-%m-%dT%H:%M:%SZ")}") '
                     "{ contributionCalendar { totalContributions } }")
    d = gql("query($login: String!) { user(login: $login) { " + " ".join(parts) + " } }")["user"]
    per_year = {str(y): d[f"y{y}"]["contributionCalendar"]["totalContributions"] for y in ys}
    return {"years": per_year, "all_time": sum(per_year.values()), "joined": u["createdAt"][:10]}


def pinned():
    d = gql("""query($login: String!) { user(login: $login) { pinnedItems(first: 6, types: REPOSITORY) { nodes {
      ... on Repository { name description url isPrivate stargazerCount forkCount primaryLanguage { name color } } } } } }""")
    items = []
    for n in d["user"]["pinnedItems"]["nodes"]:
        if not n or n.get("isPrivate"):
            continue
        lang = n.get("primaryLanguage") or {}
        items.append({"name": n["name"], "description": n.get("description"), "url": n["url"],
                      "language": lang.get("name"), "color": lang.get("color"),
                      "stars": n["stargazerCount"], "forks": n["forkCount"]})
    if not items:
        raise RuntimeError("no public pinned repositories returned")
    return {"pinned": items}


def articles():
    root = ET.fromstring(call(FEED, auth=False))
    items = []
    for it in root.iter("item"):
        title, link, pub = (it.findtext(k) or "" for k in ("title", "link", "pubDate"))
        if not title or not link:
            continue
        try:
            date = parsedate_to_datetime(pub).date().isoformat()
        except Exception:
            date = None
        items.append({"title": " ".join(title.split()), "url": link.strip(), "date": date})
        if len(items) == MAX_ARTICLES:
            break
    if not items:
        raise RuntimeError("feed had no items")
    return {"articles": items}


# ----------------------------------------------------------------------------- main
def write(data):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(f'"{k}":{json.dumps(v, separators=(",", ":"), ensure_ascii=False)}' for k, v in data.items())
    OUT.write_text("{\n" + body + "\n}\n", encoding="utf-8")


def main():
    if not TOKEN:
        sys.exit("GH_TOKEN is required")
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    data.update(calendar())  # required: any failure aborts and keeps the old file
    data["login"] = LOGIN
    data["generated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    for label, fn in (("repos", repos), ("years", years), ("pinned", pinned), ("articles", articles)):
        try:
            data.update(fn())
        except Exception as exc:
            print(f"warning: {label} not refreshed, keeping previous value: {exc}", file=sys.stderr)
    write(data)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(data['days'])} days, last-year {data['total']}, "
          f"all-time {data.get('all_time')}, {len(data.get('pinned', []))} pinned, {len(data.get('articles', []))} articles")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.exit(f"fetch failed: {exc}")
