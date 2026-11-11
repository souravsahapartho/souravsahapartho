#!/usr/bin/env python3
"""Render the animated SVG cards used by the profile README.

    python scripts/render_cards.py             animated cards   -> cards/
    STATIC=1 python scripts/render_cards.py    final frame only -> cards-static/ (+ preview.html)

Environment (all optional):
    DATA       contribution JSON           (default data/contributions.json)
    FONT_DIR   @fontsource directory       (default node_modules/@fontsource)
    OUT_DIR    output directory

The cards are shown through <img>, so everything is self-contained: an inline <style>
with CSS keyframes, SMIL <animate> for decorative loops, and fonts subset with
pyftsubset and embedded as base64 WOFF data URIs. Under prefers-reduced-motion every
animation is switched off and the final frame is what you see.
"""
from __future__ import annotations

import base64
import bisect
import calendar
import datetime as dt
import html
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from functools import lru_cache

from fontTools.ttLib import TTFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = os.environ.get("STATIC") == "1"
OUT = pathlib.Path(os.environ.get("OUT_DIR") or ROOT / ("cards-static" if STATIC else "cards"))
DATA = pathlib.Path(os.environ.get("DATA") or ROOT / "data" / "contributions.json")
FONT_DIR = pathlib.Path(os.environ.get("FONT_DIR") or ROOT / "node_modules" / "@fontsource")
ASSET_FONTS = ROOT / "assets" / "fonts"

# --------------------------------------------------------------------------- brand
# Palette carried over from the previous README and the website's buttons (#c2003a).
P = dict(
    bg="#080808", surf="#111111", surf2="#171717", line="#2A0A0E",
    brand="#FF1744", deep="#C2003A", glow="#FF3030",
    text="#F5F5F5", mute="#A1A1AA", dim="#71717A",
)
# Contribution graph ramp in the brand colour (same ramp the old snake used).
RAMP = ["#161616", "#4A0A12", "#8B0000", "#D0102F", "#FF3030"]

# --------------------------------------------------------------------------- content
PROFILE = dict(
    name="Sourav Saha",
    kicker="// HI, I'M",
    tagline="CSE student and full-stack developer building tools for the UIU community.",
    pills=["CSE Student", "Full-Stack Developer", "Problem Solver"],
    initials="SS",
)
TILES = [  # (file, icon, label, title, subtitle)
    ("tile-website", "globe", "WEBSITE", "sourav.com.bd", "Projects, articles and contact"),
    ("tile-linkedin", "linkedin", "LINKEDIN", "souravsahapartho", "Let's connect"),
    ("tile-blog", "pen", "WRITING", "TechTalk with Sourav", "Tech, AI and digital growth"),
]
FEATURED = [  # repo name on GitHub; text here is the fallback when the API has no description
    dict(repo="uiu-student-brain", title="Student Brain", kicker="TEAM ACADEMIC PLATFORM",
         blurb="Study planning, GPA forecasting and AI quizzes. I led the Flutter mobile app.",
         tags=["Flutter", "Team project"]),
    dict(repo="UIU-Learning-Management-System-LMS-", title="eLMS", kicker="LEARNING MANAGEMENT SYSTEM",
         blurb="Full-stack LMS with role-based dashboards, secure course management and real-time chat.",
         tags=["Node.js", "Express", "MySQL"]),
    dict(repo="UIU-Student-Assistant-Telegram-Bot", title="UIU Student Assistant", kicker="TELEGRAM BOT",
         blurb="CGPA and fee calculators, scholarship estimates, notices and calendar updates for UIU students.",
         tags=["Python", "Telegram"]),
    dict(repo="Human-Emotion-Detection-from-Text", title="Human Emotion Detection", kicker="MACHINE LEARNING",
         blurb="NLP web app that detects 11 human emotions from text using TF-IDF and XGBoost.",
         tags=["NLP", "TF-IDF", "XGBoost"]),
]
GITHUB_USER = "souravsahapartho"

# --------------------------------------------------------------------------- fonts
FACES = {  # class -> (fontsource package, weight)
    "sg5": ("space-grotesk", 500),
    "sg7": ("space-grotesk", 700),
    "jb5": ("jetbrains-mono", 500),
    "jb7": ("jetbrains-mono", 700),
}
FALLBACK = {"sg": "'Segoe UI',Inter,Arial,sans-serif", "jb": "'Cascadia Code',Consolas,Menlo,monospace"}


def font_path(face: str) -> pathlib.Path:
    pkg, weight = FACES[face]
    for base in (FONT_DIR / pkg / "files", ASSET_FONTS):
        p = base / f"{pkg}-latin-{weight}-normal.woff"
        if p.exists():
            return p
    sys.exit(f"font not found: {pkg} {weight}. Run: npm install --no-save @fontsource/{pkg}")


@lru_cache(None)
def metrics(face: str):
    f = TTFont(font_path(face))
    return f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm


def clean(face: str, s: str) -> str:
    cmap = metrics(face)[0]
    out = "".join(ch for ch in s if ord(ch) in cmap)
    if out != s:
        print(f"warning: dropped glyphs missing from {face}: {set(s) - set(out)}", file=sys.stderr)
    return out


def tw(face: str, s: str, size: float, ls: float = 0.0) -> float:
    cmap, hmtx, upm = metrics(face)
    return sum(hmtx[cmap[ord(c)]][0] for c in s) * size / upm + ls * len(s)


def fit(face: str, s: str, size: float, maxw: float, minsize: float = 14) -> float:
    while size > minsize and tw(face, s, size) > maxw:
        size -= 1
    return size


def wrap(face: str, s: str, size: float, maxw: float, maxlines: int = 2) -> list[str]:
    s = clean(face, s)
    lines, cur = [], ""
    for word in s.split():
        trial = (cur + " " + word).strip()
        if cur and tw(face, trial, size) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        last = lines[-1]
        while " " in last and tw(face, last + "...", size) > maxw:
            last = last.rsplit(" ", 1)[0]
        lines[-1] = last + "..."
    return lines


def pyftsubset_cmd() -> list[str]:
    exe = shutil.which("pyftsubset")
    return [exe] if exe else [sys.executable, "-m", "fontTools.subset"]


@lru_cache(None)
def subset_b64(face: str, chars: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        txt, out = pathlib.Path(td, "chars.txt"), pathlib.Path(td, "out.woff")
        txt.write_text(chars, encoding="utf-8")
        subprocess.run(
            [*pyftsubset_cmd(), str(font_path(face)), f"--text-file={txt}", "--flavor=woff",
             "--layout-features=kern", "--no-hinting", f"--output-file={out}"],
            check=True, capture_output=True,
        )
        return base64.b64encode(out.read_bytes()).decode()


# --------------------------------------------------------------------------- css
BASE_CSS = """
.a{animation-duration:.7s;animation-timing-function:cubic-bezier(.2,.8,.2,1);animation-fill-mode:both;animation-delay:var(--d,0s)}
.up{animation-name:up}@keyframes up{from{opacity:0;transform:translateY(12px)}}
.fade{animation-name:fade}@keyframes fade{from{opacity:0}}
.pop{animation-name:pop;transform-box:fill-box;transform-origin:50% 50%}@keyframes pop{from{opacity:0;transform:scale(.4)}}
.sweep{animation-name:sweep;animation-duration:.9s;transform-box:fill-box;transform-origin:0 50%}@keyframes sweep{from{transform:scaleX(0)}}
.grow{animation-name:grow;animation-duration:.9s;transform-box:fill-box;transform-origin:50% 100%}@keyframes grow{from{transform:scaleY(0)}}
.draw{animation-name:draw;animation-duration:1.4s;stroke-dasharray:1}@keyframes draw{from{stroke-dashoffset:1}}
.trv{opacity:0;animation:trv 10s linear infinite;animation-delay:var(--d,0s)}
@keyframes trv{0%{opacity:1;stroke-dashoffset:0}22%{opacity:1;stroke-dashoffset:-1000}22.1%,100%{opacity:0;stroke-dashoffset:-1000}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}.smil{display:none}}
"""
STATIC_CSS = "*,*::before,*::after{animation:none!important}.smil{display:none}"

ICONS = {  # 24x24 stroke icons
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20z"/>',
    "linkedin": '<rect x="2" y="2" width="20" height="20" rx="5"/><path d="M8 10v7M8 6.8v.01M12 17v-4a2.5 2.5 0 0 1 5 0v4M12 10v7"/>',
    "pen": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "arrow": '<path d="M7 17L17 7M8 7h9v9"/>',
    "fork": '<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    "star": '<path d="M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.1 1.2-6.5L2.5 9.4l6.6-.9z"/>',
}


def icon(name: str, x: float, y: float, size: float, color: str, sw: float = 2, extra: str = "") -> str:
    k = size / 24
    return (f'<g transform="translate({x} {y}) scale({k:.3f})" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round"{extra}>{ICONS[name]}</g>')


# --------------------------------------------------------------------------- card
class Card:
    def __init__(self, name: str, w: int, h: int, title: str, desc: str, base: float):
        self.name, self.w, self.h, self.title, self.desc, self.base = name, w, h, title, desc, base
        self.parts: list[str] = []
        self.defs: list[str] = []
        self.chars: dict[str, set] = defaultdict(set)

    def d(self, off: float = 0.0) -> str:
        return f"{self.base + off:.2f}"

    def add(self, s: str):
        self.parts.append(s)

    def at(self, kind: str, off: float = 0.0) -> str:
        return f'class="a {kind}" style="--d:{self.d(off)}s"'

    def smil(self, markup: str) -> str:
        """Decorative SMIL loops. Hidden under reduced motion; omitted entirely in STATIC mode."""
        return "" if STATIC else f'<g class="smil">{markup}</g>'

    def text(self, face, s, x, y, size, fill, anchor="start", ls=0.0, anim=None, off=0.0):
        s = clean(face, s)
        self.chars[face].update(s)
        cls = f"{face} a {anim}" if anim else face
        sty = f' style="--d:{self.d(off)}s"' if anim else ""
        anc = f' text-anchor="{anchor}"' if anchor != "start" else ""
        lsa = f' letter-spacing="{ls}"' if ls else ""
        self.parts.append(f'<text class="{cls}" x="{x}" y="{y}" font-size="{size}" fill="{fill}"{anc}{lsa}{sty}>{html.escape(s)}</text>')

    def frame(self, r: int = 22, glow: bool = True):
        w, h = self.w, self.h
        self.defs.append(f'<clipPath id="card"><rect width="{w}" height="{h}" rx="{r}"/></clipPath>')
        self.defs.append(f'<radialGradient id="gl" cx="1" cy="0" r="1"><stop offset="0" stop-color="{P["glow"]}" stop-opacity=".16"/>'
                         f'<stop offset="1" stop-color="{P["glow"]}" stop-opacity="0"/></radialGradient>')
        self.add(f'<rect width="{w}" height="{h}" fill="{P["bg"]}"/>')
        if glow:
            self.add(f'<rect width="{w}" height="{h}" fill="url(#gl)"/>')
        self.add(f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="{r-1}" fill="none" stroke="{P["line"]}" stroke-width="2"/>')
        self.add(f'<rect class="trv" style="--d:{self.d(1.0)}s" pathLength="1000" x="1" y="1" width="{w-2}" height="{h-2}" rx="{r-1}" '
                 f'fill="none" stroke="{P["brand"]}" stroke-width="3" stroke-linecap="round" stroke-dasharray="90 910"/>')

    def svg(self) -> str:
        faces = ""
        for face in FACES:
            if face in self.chars:
                chars = "".join(sorted(self.chars[face] | {" "}))
                fam = face.upper()
                faces += (f"@font-face{{font-family:'{fam}';font-weight:{FACES[face][1]};"
                          f"src:url(data:font/woff;base64,{subset_b64(face, chars)}) format('woff')}}")
        classes = "".join(f".{f}{{font-family:'{f.upper()}',{FALLBACK[f[:2]]};font-weight:{FACES[f][1]}}}" for f in FACES)
        css = faces + classes + BASE_CSS + (STATIC_CSS if STATIC else "")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" '
                f'role="img" aria-labelledby="t d">\n<title id="t">{html.escape(self.title)}</title>'
                f'<desc id="d">{html.escape(self.desc)}</desc>\n<defs>{"".join(self.defs)}</defs>\n<style>{css}</style>\n'
                f'<g clip-path="url(#card)">{"".join(self.parts)}</g>\n</svg>\n')


# --------------------------------------------------------------------------- data
def load_data() -> dict:
    if DATA.exists():
        return json.loads(DATA.read_text(encoding="utf-8"))
    return {"days": [], "repos": {}, "generated": None, "total": 0}


def calc(days: list[tuple[dt.date, int]]):
    if not days:
        return None
    active = sum(1 for _, c in days if c > 0)
    longest = run = 0  # the API returns every calendar day, so the list is contiguous
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    i = len(days) - 1
    if days[i][1] == 0:  # today may not be finished yet
        i -= 1
    current = 0
    while i >= 0 and days[i][1] > 0:
        current += 1
        i -= 1
    busiest = max(days, key=lambda x: x[1])
    return dict(active=active, longest=longest, current=current, busiest=busiest)


def monthly(days: list[tuple[dt.date, int]], end: dt.date):
    months = []
    y, m = end.year, end.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    totals = {k: 0 for k in months}
    for d, c in days:
        if (d.year, d.month) in totals:
            totals[(d.year, d.month)] += c
    return [(k, totals[k]) for k in months]


def fmt_date(d: dt.date) -> str:
    return f"{calendar.month_abbr[d.month]} {d.day}, {d.year}"


# --------------------------------------------------------------------------- cards
def portrait_uri() -> str | None:
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = ROOT / "assets" / f"portrait.{ext}"
        if p.exists():
            try:
                from PIL import Image
                import io
                im = Image.open(p).convert("RGB")
                s = min(im.size)
                l, t = (im.width - s) // 2, (im.height - s) // 4  # bias toward the top: faces sit high
                im = im.crop((l, max(0, t), l + s, max(0, t) + s)).resize((260, 260), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=82, optimize=True)
                return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            except ImportError:
                mime = "image/png" if ext == "png" else "image/webp" if ext == "webp" else "image/jpeg"
                return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    return None


def hero(base: float) -> Card:
    c = Card("hero", 900, 330, PROFILE["name"], f"{PROFILE['name']}. {PROFILE['tagline']}", base)
    c.defs.append(f'<pattern id="grid" width="30" height="30" patternUnits="userSpaceOnUse"><path d="M30 0H0V30" fill="none" stroke="#fff" stroke-opacity=".035"/></pattern>')
    c.frame()
    c.add('<rect width="900" height="330" fill="url(#grid)"/>')
    c.add(f'<rect x="1" y="30" width="4" height="270" rx="2" fill="{P["brand"]}"/>')

    c.text("jb5", PROFILE["kicker"], 56, 84, 17, P["brand"], ls=2, anim="up", off=0.15)
    c.text("sg7", PROFILE["name"], 56, 162, 70, P["text"], anim="up", off=0.30)
    c.add(f'<rect {c.at("sweep", 0.7)} x="56" y="180" width="84" height="4" rx="2" fill="{P["brand"]}"/>')
    for i, line in enumerate(wrap("sg5", PROFILE["tagline"], 23, 520)):
        c.text("sg5", line, 56, 224 + i * 30, 23, P["mute"], anim="up", off=0.55 + i * 0.1)

    x = 56
    for i, label in enumerate(PROFILE["pills"]):
        label = clean("jb5", label)
        w = tw("jb5", label, 14, 0.5) + 28
        c.add(f'<g {c.at("up", 0.9 + i * 0.12)}><rect x="{x:.1f}" y="274" width="{w:.1f}" height="30" rx="15" fill="{P["surf2"]}" stroke="{P["line"]}" stroke-width="2"/></g>')
        c.text("jb5", label, round(x + 14, 1), 294, 14, P["mute"], ls=0.5, anim="up", off=0.95 + i * 0.12)
        x += w + 12

    cx, cy, r = 752, 165, 84
    uri = portrait_uri()
    c.add(f'<circle cx="{cx}" cy="{cy}" r="{r+16}" fill="none" stroke="{P["line"]}" stroke-width="2" stroke-dasharray="3 9"/>')
    if uri:
        c.defs.append(f'<clipPath id="pf"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>')
        c.add(f'<image {c.at("fade", 0.4)} href="{uri}" x="{cx-r}" y="{cy-r}" width="{r*2}" height="{r*2}" preserveAspectRatio="xMidYMid slice" clip-path="url(#pf)"/>')
    else:
        c.add(f'<circle {c.at("fade", 0.4)} cx="{cx}" cy="{cy}" r="{r}" fill="{P["surf2"]}"/>')
        c.text("sg7", PROFILE["initials"], cx, cy + 22, 64, P["brand"], anchor="middle", anim="up", off=0.5)
    c.add(f'<circle class="a draw" style="--d:{c.d(0.5)}s" pathLength="1" cx="{cx}" cy="{cy}" r="{r+8}" fill="none" stroke="{P["brand"]}" stroke-width="3" stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>')
    b = c.base + 1.6
    c.add(c.smil(
        f'<circle cx="{cx}" cy="{cy}" r="{r+8}" fill="none" stroke="{P["brand"]}" stroke-width="2" opacity="0">'
        f'<animate attributeName="r" values="{r+8};{r+34}" dur="3.2s" begin="{b:.2f}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values=".5;0" dur="3.2s" begin="{b:.2f}s" repeatCount="indefinite"/></circle>'
        f'<circle r="3.5" fill="{P["glow"]}" opacity="0"><animate attributeName="opacity" values="0;1" dur=".1s" begin="{b:.2f}s" fill="freeze"/>'
        f'<animateMotion dur="9s" begin="{b:.2f}s" repeatCount="indefinite" path="M{cx} {cy-r-16} a{r+16} {r+16} 0 1 1 -.01 0z"/></circle>'))
    return c


def tile(i: int, base: float) -> Card:
    name, ic, label, title, sub = TILES[i]
    c = Card(name, 300, 150, f"{label.title()}: {title}", f"{label.title()}. {title}. {sub}.", base)
    c.frame(18, glow=False)
    c.add(f'<rect {c.at("sweep", 0.1)} x="1" y="1" width="298" height="4" fill="{P["brand"]}"/>')
    c.add(f'<g transform="translate(24 24)"><g {c.at("pop", 0.15)}>{icon(ic, 0, 0, 26, P["brand"], 2)}</g></g>')
    c.text("jb5", label, 62, 43, 13, P["mute"], ls=2, anim="fade", off=0.2)
    size = fit("sg7", title, 26, 252)
    c.text("sg7", title, 24, 98, size, P["text"], anim="up", off=0.25)
    c.text("sg5", sub, 24, 126, fit("sg5", sub, 16, 220, 12), P["mute"], anim="up", off=0.35)
    c.add(f'<g transform="translate(256 106)"><g {c.at("fade", 0.4)}>{icon("arrow", 0, 0, 22, P["brand"], 2.2)}</g></g>')
    b = c.base + 1.2
    c.add(c.smil(f'<circle cx="267" cy="117" r="14" fill="none" stroke="{P["brand"]}" stroke-width="2" opacity="0">'
                 f'<animate attributeName="r" values="14;28" dur="2.6s" begin="{b:.2f}s" repeatCount="indefinite"/>'
                 f'<animate attributeName="opacity" values=".45;0" dur="2.6s" begin="{b:.2f}s" repeatCount="indefinite"/></circle>'))
    return c


def chart(base: float, data: dict) -> Card:
    days = [(dt.date.fromisoformat(d), n) for d, n in data.get("days", [])]
    have = bool(days)
    end = days[-1][0] if have else dt.date.today()
    mon = monthly(days, end)
    st = calc(days)
    total = sum(v for _, v in mon)
    if have:
        desc = (f"Contributions per month, {calendar.month_abbr[mon[0][0][1]]} {mon[0][0][0]} to {calendar.month_abbr[mon[-1][0][1]]} {mon[-1][0][0]}: "
                f"{total} in total. Current streak {st['current']} days, longest {st['longest']}, {st['active']} active days, "
                f"busiest day {fmt_date(st['busiest'][0])} with {st['busiest'][1]} contributions.")
    else:
        desc = "Contributions per month. Data appears after the first daily refresh."
    c = Card("chart", 900, 370, "Contributions per month", desc, base)
    c.frame()
    c.text("jb5", "// CONTRIBUTIONS PER MONTH", 40, 52, 15, P["brand"], ls=2, anim="up", off=0.1)
    big = f"{total:,}" if have else "—"
    big = clean("sg7", big)
    c.text("sg7", big, 40, 108, 46, P["text"], anim="up", off=0.2)
    if have:
        a, z = mon[0][0], mon[-1][0]
        sub = f"{calendar.month_abbr[a[1]]} {a[0]} – {calendar.month_abbr[z[1]]} {z[0]}"
    else:
        sub = "waiting for the first data refresh"
    c.text("sg5", sub, 40 + tw("sg7", big, 46) + 16, 108, 19, P["mute"], anim="up", off=0.3)
    if data.get("generated"):
        c.text("jb5", f"UPDATED {data['generated']}", 860, 52, 13, P["dim"], anchor="end", ls=1, anim="fade", off=0.2)

    # bars
    x0, base_y, top_y, bw, gap = 40, 300, 158, 28, 14
    peak = max((v for _, v in mon), default=0) or 1
    for g in range(4):
        y = base_y - (base_y - top_y) * g / 3
        c.add(f'<rect {c.at("fade", 0.3)} x="{x0-8}" y="{y:.1f}" width="{12*bw+11*gap+16}" height="1" fill="{P["line"]}"/>')
    for i, ((yy, mm), v) in enumerate(mon):
        h = max(3.0, (base_y - top_y) * v / peak) if have else 3.0
        x = x0 + i * (bw + gap)
        last = i == len(mon) - 1
        fill = P["brand"] if v == peak and have else P["deep"] if not last else P["glow"]
        c.add(f'<rect {c.at("grow", 0.35 + i * 0.07)} x="{x}" y="{base_y - h:.1f}" width="{bw}" height="{h:.1f}" rx="5" fill="{fill}"/>')
        if have and v:
            c.text("jb5", str(v), x + bw / 2, base_y - h - 9, 12, P["mute"], anchor="middle", anim="fade", off=0.9 + i * 0.07)
        c.text("jb5", calendar.month_abbr[mm].upper(), x + bw / 2, base_y + 22, 12, P["dim"], anchor="middle", ls=0.5, anim="fade", off=0.5 + i * 0.05)

    # stat boxes
    boxes = [
        ("CURRENT STREAK", str(st["current"]) if st else "—", "days"),
        ("LONGEST STREAK", str(st["longest"]) if st else "—", "days"),
        ("ACTIVE DAYS", str(st["active"]) if st else "—", "in the last year"),
        ("BUSIEST DAY", str(st["busiest"][1]) if st else "—", fmt_date(st["busiest"][0]) if st else "contributions"),
    ]
    for k, (label, val, sub) in enumerate(boxes):
        bx, by = 584 + (k % 2) * 148, 130 + (k // 2) * 112
        o = 0.5 + k * 0.15
        c.add(f'<g {c.at("up", o)}><rect x="{bx}" y="{by}" width="136" height="100" rx="14" fill="{P["surf2"]}" stroke="{P["line"]}" stroke-width="2"/>'
              f'<rect x="{bx+14}" y="{by+1}" width="40" height="3" rx="1.5" fill="{P["brand"]}"/></g>')
        c.text("jb5", label, bx + 14, by + 28, 11.5, P["mute"], ls=0.8, anim="up", off=o + 0.05)
        c.text("sg7", val, bx + 14, by + 68, 38, P["text"], anim="up", off=o + 0.1)
        c.text("jb5", sub, bx + 14, by + 88, 12, P["dim"], anim="up", off=o + 0.15)
    if data.get("generated"):
        b = c.base + 1.5
        c.add(c.smil(f'<circle cx="{860 - tw("jb5", "UPDATED " + data["generated"], 13, 1) - 14:.1f}" cy="47" r="3.5" fill="{P["brand"]}">'
                     f'<animate attributeName="opacity" values="1;.25;1" dur="2.2s" begin="{b:.2f}s" repeatCount="indefinite"/></circle>'))
    return c


def repo_card(i: int, base: float, data: dict) -> Card:
    f = FEATURED[i]
    info = (data.get("repos") or {}).get(f["repo"], {})
    blurb = info.get("description") or f["blurb"]
    c = Card(f"repo-{i+1}", 900, 214, f"{f['title']}: GitHub repository", f"{f['title']}. {blurb}", base)
    c.frame()
    c.add(f'<rect {c.at("grow", 0.1)} x="1" y="28" width="4" height="158" rx="2" fill="{P["brand"]}"/>')
    c.text("jb5", f"// {i+1:02d} · {f['kicker']}", 40, 46, 14, P["brand"], ls=1.6, anim="up", off=0.1)
    c.text("sg7", f["title"], 40, 92, fit("sg7", f["title"], 36, 700), P["text"], anim="up", off=0.2)
    for k, line in enumerate(wrap("sg5", blurb, 19, 740)):
        c.text("sg5", line, 40, 126 + k * 25, 19, P["mute"], anim="up", off=0.35 + k * 0.1)

    x = 40
    for k, tag in enumerate(f["tags"]):
        tag = clean("jb5", tag)
        w = tw("jb5", tag, 12.5, 0.4) + 24
        c.add(f'<g {c.at("up", 0.55 + k * 0.08)}><rect x="{x:.1f}" y="172" width="{w:.1f}" height="28" rx="14" fill="{P["surf2"]}" stroke="{P["line"]}" stroke-width="2"/></g>')
        c.text("jb5", tag, round(x + 12, 1), 191, 12.5, P["mute"], ls=0.4, anim="up", off=0.6 + k * 0.08)
        x += w + 10

    # live stats (only when the data file has them)
    if info:
        rx = 860
        for name, val in (("fork", info["forks"]), ("star", info["stars"])):
            s = str(val)
            c.text("jb7", s, rx, 191, 14, P["text"], anchor="end", anim="fade", off=0.7)
            rx -= tw("jb7", s, 14) + 24
            c.add(f'<g {c.at("fade", 0.7)}>{icon(name, rx, 175, 18, P["brand"], 2)}</g>')
            rx -= 20
    c.add(f'<g {c.at("pop", 0.3)}><circle cx="846" cy="50" r="22" fill="none" stroke="{P["brand"]}" stroke-width="2.5"/></g>')
    c.add(f'<g transform="translate(834 38)">{icon("arrow", 0, 0, 24, P["brand"], 2.2)}</g>')
    return c


def graph(base: float, data: dict) -> Card:
    days = [(dt.date.fromisoformat(d), n) for d, n in data.get("days", [])]
    counts = {d: n for d, n in days}
    end = days[-1][0] if days else dt.date.today()
    total = data.get("total") or sum(counts.values())
    desc = (f"Contribution graph for the last year in red: {total} contributions." if days
            else "Contribution graph. Data appears after the first daily refresh.")
    c = Card("graph", 900, 250, "Contribution graph", desc, base)
    c.frame()
    c.text("jb5", "// CONTRIBUTION GRAPH", 40, 48, 15, P["brand"], ls=2, anim="up", off=0.1)
    head = f"{total:,} contributions in the last year" if days else "waiting for the first data refresh"
    c.text("jb5", head, 860, 48, 13, P["mute"], anchor="end", ls=0.6, anim="up", off=0.15)

    nz = sorted(v for v in counts.values() if v > 0)
    mx = nz[-1] if nz else 1
    cuts = [mx * .25, mx * .5, mx * .75]

    def level(n: int) -> int:
        return 0 if n == 0 else 1 + bisect.bisect_left(cuts, n)

    gx, gy, pitch, cell = 92, 98, 14, 11
    first = end - dt.timedelta(days=(end.weekday() + 1) % 7) - dt.timedelta(weeks=52)
    last_label = -9
    for w in range(53):
        start = first + dt.timedelta(weeks=w)
        rects = ""
        for r in range(7):
            d = start + dt.timedelta(days=r)
            if d > end:
                break
            rects += f'<rect x="{gx + w * pitch}" y="{gy + r * pitch}" width="{cell}" height="{cell}" rx="2.5" fill="{RAMP[level(counts.get(d, 0))]}"/>'
        c.add(f'<g {c.at("up", 0.25 + w * 0.02)}>{rects}</g>')
        prev = start - dt.timedelta(weeks=1)
        if (w == 0 or start.month != prev.month) and w - last_label >= 3 and w <= 50:
            c.text("jb5", calendar.month_abbr[start.month].upper(), gx + w * pitch, gy - 12, 11, P["dim"], ls=0.5, anim="fade", off=0.4 + w * 0.02)
            last_label = w
    for r, lab in ((1, "MON"), (3, "WED"), (5, "FRI")):
        c.text("jb5", lab, 40, gy + r * pitch + 9, 10, P["dim"], ls=0.5, anim="fade", off=0.3)

    # legend
    lx = 860 - 5 * pitch - tw("jb5", "MORE", 11, .5) - 8
    c.text("jb5", "MORE", 860, 226, 11, P["dim"], anchor="end", ls=0.5, anim="fade", off=1.0)
    for k in range(5):
        c.add(f'<rect {c.at("pop", 1.0 + k * 0.06)} x="{lx + k * pitch:.1f}" y="216" width="{cell}" height="{cell}" rx="2.5" fill="{RAMP[k]}"/>')
    c.text("jb5", "LESS", lx - 8, 226, 11, P["dim"], anchor="end", ls=0.5, anim="fade", off=1.0)

    # decorative shimmer across the grid
    b = c.base + 1.8
    gw = 53 * pitch
    c.defs.append(f'<linearGradient id="shim"><stop offset="0" stop-color="{P["glow"]}" stop-opacity="0"/><stop offset=".5" stop-color="{P["glow"]}" stop-opacity=".16"/><stop offset="1" stop-color="{P["glow"]}" stop-opacity="0"/></linearGradient>')
    c.defs.append(f'<clipPath id="gridclip"><rect x="{gx}" y="{gy}" width="{gw}" height="{7 * pitch}"/></clipPath>')
    c.add(c.smil(f'<g clip-path="url(#gridclip)"><rect y="{gy}" width="90" height="{7 * pitch}" fill="url(#shim)" x="{gx - 90}">'
                 f'<animate attributeName="x" values="{gx - 90};{gx + gw}" dur="6s" begin="{b:.2f}s" repeatCount="indefinite"/></rect></g>'))
    return c


# --------------------------------------------------------------------------- main
def main() -> None:
    data = load_data()
    cards: list[Card] = [hero(0.0)]
    cards += [tile(i, 0.45 + i * 0.12) for i in range(len(TILES))]
    cards.append(chart(1.2, data))
    cards += [repo_card(i, 1.8 + i * 0.3, data) for i in range(len(FEATURED))]
    cards.append(graph(1.8 + len(FEATURED) * 0.3 + 0.1, data))

    OUT.mkdir(parents=True, exist_ok=True)
    for c in cards:
        (OUT / f"{c.name}.svg").write_text(c.svg(), encoding="utf-8")
        print(f"{c.name}.svg  {len(c.svg()) / 1024:.1f} KB")

    if STATIC:  # a README-shaped page to screenshot before pushing
        def img(c, w="100%"):
            return f'<img src="{c.name}.svg" style="width:{w}">'
        tiles = "".join(img(c, "32.5%") for c in cards if c.name.startswith("tile"))
        rest = "".join(f"<p>{img(c)}</p>" for c in cards if not c.name.startswith(("tile", "hero")))
        (OUT / "preview.html").write_text(
            "<!doctype html><meta charset=utf-8><title>README preview</title>"
            "<body style='background:#0d1117;margin:0;padding:24px'><main style='width:830px;margin:auto;font-size:0'>"
            f"<p>{img(cards[0])}</p><p style='display:flex;gap:1.25%'>{tiles}</p>{rest}</main>"
            "<style>p{margin:0 0 16px}</style>", encoding="utf-8")
        print(f"static final frames written to {OUT} (open preview.html and screenshot)")


if __name__ == "__main__":
    main()
