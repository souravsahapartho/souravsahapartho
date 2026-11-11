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
    text="#F5F5F5", soft="#D4D4D8", mute="#A1A1AA", dim="#71717A",
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
TILES = [  # (file, icon, label, title, subtitle, link)
    ("tile-website", "globe", "WEBSITE", "sourav.com.bd", "Projects, articles and contact", "https://sourav.com.bd"),
    ("tile-linkedin", "linkedin", "LINKEDIN", "souravsahapartho", "Let's connect", "https://www.linkedin.com/in/souravsahapartho/"),
    ("tile-blog", "pen", "WRITING", "TechTalk with Sourav", "Tech, AI and digital growth", "https://blog.sourav.com.bd"),
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
ABOUT = ("CSE student at UIU, Dhaka. I build full-stack web apps with Node.js, Express and MySQL, and Android apps "
         "with Flutter. Most of what I ship serves the UIU community. I also write about tech at TechTalk with Sourav.")
EDUCATION = dict(degree="B.Sc. in CSE", school="United International University", place="Dhaka, Bangladesh · Ongoing")
SKILLS = ["JavaScript", "Node.js", "Express", "Flutter", "MySQL", "C", "C++", "Java", "HTML", "CSS"]
FOOTER = ("Let's build something.", "Open to collaborations and student projects.")

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
    "book": '<path d="M4 19.5V5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2z"/><path d="M4 19.5a2 2 0 0 0 2 2h13"/>',
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
    name, ic, label, title, sub, _ = TILES[i]
    nice = {"LINKEDIN": "LinkedIn"}.get(label, label.title())
    c = Card(name, 300, 150, f"{nice}: {title}", f"{nice}: {title}. {sub}.", base)
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


def section(i: int, kicker: str, title: str, base: float) -> Card:
    """A slim heading bar. It carries its own dark background so it reads on GitHub's light theme too."""
    c = Card(f"section-{i}", 900, 64, title, f"Section: {title}", base)
    c.frame(16, glow=False)
    c.text("jb7", kicker, 30, 41, 16, P["brand"], ls=1.5, anim="up")
    x = 30 + tw("jb7", clean("jb7", kicker), 16, 1.5) + 16
    c.text("sg7", title, round(x, 1), 44, 30, P["text"], anim="up", off=0.1)
    lx = x + tw("sg7", clean("sg7", title), 30) + 22
    c.add(f'<rect {c.at("sweep", 0.3)} x="{lx:.1f}" y="31" width="{868 - lx:.1f}" height="2" rx="1" fill="{P["line"]}"/>')
    c.add(f'<rect {c.at("sweep", 0.3)} x="{lx:.1f}" y="31" width="64" height="2" rx="1" fill="{P["brand"]}"/>')
    return c


def about(base: float) -> Card:
    c = Card("about", 900, 250, "About me", f"About me. {ABOUT} {EDUCATION['degree']}, {EDUCATION['school']}, {EDUCATION['place']}.", base)
    c.frame()
    c.text("jb5", "// ABOUT ME", 40, 52, 15, P["brand"], ls=2, anim="up", off=0.1)
    for k, line in enumerate(wrap("sg5", ABOUT, 20, 520, 6)):
        c.text("sg5", line, 40, 94 + k * 30, 20, P["soft"], anim="up", off=0.2 + k * 0.08)
    e = EDUCATION
    c.add(f'<g {c.at("up", 0.4)}><rect x="620" y="38" width="240" height="176" rx="16" fill="{P["surf2"]}" stroke="{P["line"]}" stroke-width="2"/>'
          f'<rect x="636" y="39" width="44" height="3" rx="1.5" fill="{P["brand"]}"/></g>')
    c.text("jb5", "EDUCATION", 640, 72, 12, P["mute"], ls=1.5, anim="up", off=0.45)
    c.text("sg7", e["degree"], 640, 110, fit("sg7", e["degree"], 26, 200, 18), P["text"], anim="up", off=0.5)
    for k, line in enumerate(wrap("sg5", e["school"], 16, 200, 2)):
        c.text("sg5", line, 640, 136 + k * 21, 16, P["mute"], anim="up", off=0.55 + k * 0.05)
    c.text("jb5", e["place"], 640, 192, 12, P["dim"], anim="up", off=0.65)
    return c


def skills(base: float) -> Card:
    chips, x, y, row_h = [], 40, 84, 52
    for s in SKILLS:
        s = clean("jb5", s)
        w = tw("jb5", s, 15, 0.3) + 52
        if x + w > 860:
            x, y = 40, y + row_h
        chips.append((s, x, y, w))
        x += w + 12
    h = y + row_h + 14
    c = Card("skills", 900, h, "Tech stack", "Tech stack: " + ", ".join(SKILLS) + ".", base)
    c.frame()
    c.text("jb5", "// TECH STACK", 40, 52, 15, P["brand"], ls=2, anim="up", off=0.1)
    for k, (s, cx, cy, w) in enumerate(chips):
        c.add(f'<g {c.at("pop", 0.2 + k * 0.06)}><rect x="{cx}" y="{cy}" width="{w:.1f}" height="40" rx="20" fill="{P["surf2"]}" stroke="{P["line"]}" stroke-width="2"/>'
              f'<circle cx="{cx + 20}" cy="{cy + 20}" r="4.5" fill="{P["brand"]}"/></g>')
        c.text("jb5", s, round(cx + 34, 1), cy + 25, 15, P["soft"], ls=0.3, anim="fade", off=0.25 + k * 0.06)
    return c


def pinned_items(data: dict) -> list[dict]:
    """Pinned repos from the data file; falls back to the FEATURED list before the first refresh."""
    if data.get("pinned"):
        return data["pinned"]
    out = []
    for f in FEATURED:
        info = (data.get("repos") or {}).get(f["repo"], {})
        out.append(dict(name=f["title"], description=info.get("description") or f["blurb"], language=info.get("language"),
                        color=None, stars=info.get("stars"), forks=info.get("forks"),
                        url=info.get("url") or f"https://github.com/{GITHUB_USER}/{f['repo']}"))
    return out


def pinned_card(i: int, base: float, it: dict) -> Card:
    name, desc = it["name"], (it.get("description") or "No description yet.")
    c = Card(f"pin-{i + 1}", 440, 160, f"{name}: pinned repository", f"{name}. {desc}", base)
    c.frame(18, glow=False)
    c.add(f'<rect {c.at("sweep", 0.1)} x="1" y="1" width="438" height="3" fill="{P["brand"]}"/>')
    c.add(f'<g transform="translate(22 22)"><g {c.at("pop", 0.15)}>{icon("book", 0, 0, 22, P["brand"], 2)}</g></g>')
    c.text("sg7", name, 54, 40, fit("sg7", clean("sg7", name), 21, 330, 14), P["text"], anim="up", off=0.2)
    c.add(f'<g transform="translate(402 22)"><g {c.at("fade", 0.3)}>{icon("arrow", 0, 0, 18, P["mute"], 2.2)}</g></g>')
    for k, line in enumerate(wrap("sg5", desc, 15, 396, 2)):
        c.text("sg5", line, 22, 78 + k * 22, 15, P["mute"], anim="up", off=0.3 + k * 0.08)

    y, x = 138, 22
    lang = it.get("language")
    if lang:
        col = it.get("color") or P["brand"]
        col = col if len(col) == 7 and col.startswith("#") else P["brand"]
        c.add(f'<circle {c.at("pop", 0.5)} cx="{x + 5}" cy="{y - 4}" r="5.5" fill="{col}"/>')
        c.text("jb5", lang, x + 18, y, 12.5, P["mute"], anim="fade", off=0.5)
        x += 18 + tw("jb5", clean("jb5", lang), 12.5) + 22
    for name_, key in (("star", "stars"), ("fork", "forks")):
        if it.get(key) is None:
            continue
        c.add(f'<g {c.at("fade", 0.55)}>{icon(name_, x, y - 14, 17, P["brand"], 2)}</g>')
        s = str(it[key])
        c.text("jb7", s, x + 24, y, 12.5, P["text"], anim="fade", off=0.55)
        x += 24 + tw("jb7", s, 12.5) + 20
    return c


def article(i: int, base: float, a: dict) -> Card:
    title = a["title"]
    date = dt.date.fromisoformat(a["date"]) if a.get("date") else None
    kick = f"// ARTICLE{' · ' + fmt_date(date).upper() if date else ''}"
    c = Card(f"article-{i + 1}", 900, 114, title, f"Article: {title}" + (f", {fmt_date(date)}" if date else ""), base)
    c.frame(18, glow=False)
    c.add(f'<rect {c.at("grow", 0.1)} x="1" y="24" width="4" height="66" rx="2" fill="{P["brand"]}"/>')
    c.text("jb5", kick, 40, 40, 13, P["brand"], ls=1.5, anim="up", off=0.1)
    lines = wrap("sg7", title, 21, 740, 2)
    ys = [80] if len(lines) == 1 else [72, 97]
    for line, y in zip(lines, ys):
        c.text("sg7", line, 40, y, 21, P["text"], anim="up", off=0.2)
    c.add(f'<g {c.at("pop", 0.3)}><circle cx="838" cy="57" r="22" fill="none" stroke="{P["brand"]}" stroke-width="2.5"/></g>')
    c.add(f'<g transform="translate(826 45)">{icon("arrow", 0, 0, 24, P["brand"], 2.2)}</g>')
    return c


def footer(base: float) -> Card:
    c = Card("footer", 900, 130, "Let's build something", f"{FOOTER[0]} {FOOTER[1]}", base)
    c.frame()
    c.add(f'<rect {c.at("grow", 0.1)} x="1" y="26" width="4" height="78" rx="2" fill="{P["brand"]}"/>')
    c.text("sg7", FOOTER[0], 40, 66, 38, P["text"], anim="up", off=0.15)
    c.text("sg5", FOOTER[1], 40, 100, 19, P["mute"], anim="up", off=0.3)
    c.add(f'<g {c.at("pop", 0.4)}><circle cx="820" cy="65" r="30" fill="none" stroke="{P["brand"]}" stroke-width="3"/></g>')
    c.add(f'<g transform="translate(804 49)">{icon("arrow", 0, 0, 32, P["brand"], 2.2)}</g>')
    b = c.base + 1.2
    c.add(c.smil(f'<circle cx="820" cy="65" r="30" fill="none" stroke="{P["brand"]}" stroke-width="2" opacity="0">'
                 f'<animate attributeName="r" values="30;56" dur="3s" begin="{b:.2f}s" repeatCount="indefinite"/>'
                 f'<animate attributeName="opacity" values=".45;0" dur="3s" begin="{b:.2f}s" repeatCount="indefinite"/></circle>'))
    return c


def graph(base: float, data: dict) -> Card:
    days = [(dt.date.fromisoformat(d), n) for d, n in data.get("days", [])]
    counts = {d: n for d, n in days}
    end = days[-1][0] if days else dt.date.today()
    last_year = data.get("total") or sum(counts.values())
    years = {str(k): v for k, v in (data.get("years") or {}).items()}
    all_time = data.get("all_time") or sum(years.values())
    desc = (f"Contribution graph for the last year in red: {last_year} contributions in the last year"
            + (f", {all_time} all time." if all_time else ".") if days else "Contribution graph. Data appears after the first daily refresh.")
    c = Card("graph", 900, 368, "Contribution graph", desc, base)
    c.frame()
    c.text("jb5", "// CONTRIBUTION GRAPH", 40, 48, 15, P["brand"], ls=2, anim="up", off=0.1)
    head = f"{last_year:,} in the last year" if days else "waiting for the first data refresh"
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

    lx = 860 - 5 * pitch - tw("jb5", "MORE", 11, .5) - 8
    c.text("jb5", "MORE", 860, 226, 11, P["dim"], anchor="end", ls=0.5, anim="fade", off=1.0)
    for k in range(5):
        c.add(f'<rect {c.at("pop", 1.0 + k * 0.06)} x="{lx + k * pitch:.1f}" y="216" width="{cell}" height="{cell}" rx="2.5" fill="{RAMP[k]}"/>')
    c.text("jb5", "LESS", lx - 8, 226, 11, P["dim"], anchor="end", ls=0.5, anim="fade", off=1.0)

    # ---- all-time strip under the graph
    c.add(f'<rect {c.at("fade", 1.1)} x="40" y="248" width="820" height="1" fill="{P["line"]}"/>')
    c.text("jb5", "ALL-TIME CONTRIBUTIONS", 40, 282, 12, P["mute"], ls=1.4, anim="up", off=1.2)
    big = clean("sg7", f"{all_time:,}" if all_time else "—")
    c.text("sg7", big, 40, 334, 46, P["text"], anim="up", off=1.3)
    if all_time and data.get("joined"):
        j = dt.date.fromisoformat(data["joined"])
        sub = f"since {calendar.month_abbr[j.month]} {j.year}"
    else:
        sub = "waiting for the first data refresh" if not all_time else "since you joined"
    c.text("sg5", sub, round(40 + tw("sg7", big, 46) + 14, 1), 334, 18, P["mute"], anim="up", off=1.4)
    yrs = sorted(years.items())
    if yrs:
        colw = min(84.0, 440.0 / len(yrs))
        x0 = 860 - colw * len(yrs)
        peak = max(v for _, v in yrs) or 1
        for k, (y, v) in enumerate(yrs):
            bx = x0 + k * colw + colw / 2
            h = max(3.0, 36 * v / peak)
            base_y = 322
            c.add(f'<rect {c.at("grow", 1.3 + k * 0.1)} x="{bx - 9:.1f}" y="{322 - h:.1f}" width="18" height="{h:.1f}" rx="4" '
                  f'fill="{P["brand"] if v == peak else P["deep"]}"/>')
            c.text("jb5", f"{v:,}", round(bx, 1), round(322 - h - 7, 1), 11, P["mute"], anchor="middle", anim="fade", off=1.6 + k * 0.1)
            c.text("jb5", y, round(bx, 1), 346, 11, P["dim"], anchor="middle", anim="fade", off=1.5 + k * 0.1)

    b = c.base + 1.8
    gw = 53 * pitch
    c.defs.append(f'<linearGradient id="shim"><stop offset="0" stop-color="{P["glow"]}" stop-opacity="0"/><stop offset=".5" stop-color="{P["glow"]}" stop-opacity=".16"/><stop offset="1" stop-color="{P["glow"]}" stop-opacity="0"/></linearGradient>')
    c.defs.append(f'<clipPath id="gridclip"><rect x="{gx}" y="{gy}" width="{gw}" height="{7 * pitch}"/></clipPath>')
    c.add(c.smil(f'<g clip-path="url(#gridclip)"><rect y="{gy}" width="90" height="{7 * pitch}" fill="url(#shim)" x="{gx - 90}">'
                 f'<animate attributeName="x" values="{gx - 90};{gx + gw}" dur="6s" begin="{b:.2f}s" repeatCount="indefinite"/></rect></g>'))
    return c


# --------------------------------------------------------------------------- layout + README
def readme(rows: list) -> str:
    out = ["<!-- Generated by scripts/render_cards.py (daily workflow). Edit the layout in main() there, not this file. -->"]
    for row in rows:
        items = []
        for card, href, width in row:
            img = f'<img src="cards/{card.name}.svg" width="{width}" alt="{html.escape(card.desc, quote=True)}">'
            items.append(f'<a href="{href}">{img}</a>' if href else img)
        out.append('<p align="center">\n' + "\n".join(items) + "\n</p>")
    return "\n\n".join(out) + "\n"


def main() -> None:
    data = load_data()
    rows: list[list[tuple]] = []

    def base(k: int = 0) -> float:  # rows start 0.38s apart, items in a row 0.1s apart: the page plays top to bottom
        return round(len(rows) * 0.38 + k * 0.1, 2)

    rows.append([(hero(base()), None, "100%")])
    rows.append([(tile(i, base(i)), TILES[i][5], "32.5%") for i in range(len(TILES))])

    rows.append([(section(1, "// 01", "About", base()), None, "100%")])
    rows.append([(about(base()), None, "100%")])
    rows.append([(skills(base()), None, "100%")])

    pins = pinned_items(data)
    rows.append([(section(2, "// 02", "Pinned projects", base()), None, "100%")])
    for r in range(0, len(pins), 2):
        rows.append([(pinned_card(r + k, base(k), pins[r + k]), pins[r + k]["url"], "49.5%") for k in range(min(2, len(pins) - r))])

    arts = data.get("articles") or []
    if arts:
        rows.append([(section(3, "// 03", "Latest articles", base()), None, "100%")])
        for i, a in enumerate(arts):
            rows.append([(article(i, base(), a), a["url"], "100%")])

    rows.append([(section(4, "// 04", "Activity", base()), None, "100%")])
    rows.append([(chart(base(), data), None, "100%")])
    rows.append([(graph(base(), data), None, "100%")])
    rows.append([(footer(base()), "https://sourav.com.bd", "100%")])

    cards = [c for row in rows for c, _, _ in row]
    OUT.mkdir(parents=True, exist_ok=True)
    keep = {f"{c.name}.svg" for c in cards}
    for old in OUT.glob("*.svg"):  # drop cards that no longer exist (e.g. fewer pinned repos)
        if old.name not in keep:
            old.unlink()
    for c in cards:
        svg = c.svg()
        (OUT / f"{c.name}.svg").write_text(svg, encoding="utf-8")
        print(f"{c.name}.svg  {len(svg) / 1024:.1f} KB")

    if STATIC:  # a README-shaped page to screenshot before pushing
        body = "".join(
            "<div style='display:flex;justify-content:center;gap:1%;margin-bottom:14px'>"
            + "".join(f"<img src='{c.name}.svg' style='width:{w}'>" for c, _, w in row) + "</div>"
            for row in rows)
        (OUT / "preview.html").write_text(
            "<!doctype html><meta charset=utf-8><title>README preview</title>"
            f"<body style='background:#0d1117;margin:0;padding:24px'><main style='width:830px;margin:auto;font-size:0'>{body}</main>",
            encoding="utf-8")
        print(f"static final frames written to {OUT} (open preview.html and screenshot)")
    else:
        (ROOT / "README.md").write_text(readme(rows), encoding="utf-8")
        print("README.md written")


if __name__ == "__main__":
    main()
