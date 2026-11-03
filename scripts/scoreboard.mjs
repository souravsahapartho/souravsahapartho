// Generates the REDLINE stats cards (desktop + mobile) from the GitHub API.
// Zero dependencies (Node 20+). Env: GH_TOKEN (required), GH_LOGIN, OUT_DIR.
import fs from 'node:fs';
import path from 'node:path';

const TOKEN = process.env.GH_TOKEN;
const LOGIN = process.env.GH_LOGIN || 'souravsahapartho';
const OUT_DIR = process.env.OUT_DIR || 'assets/redline/generated';
if (!TOKEN) { console.error('GH_TOKEN is required'); process.exit(1); }

const C = { bg: '#080808', surf: '#111111', surf2: '#171717', line: '#2A0A0E', red: '#FF1744', glow: '#FF3030', text: '#F5F5F5', t2: '#A1A1AA' };
const FONT = "'Segoe UI',Inter,Roboto,'Helvetica Neue',Arial,sans-serif";
const MONO = "'JetBrains Mono','Cascadia Code','SF Mono',Consolas,Menlo,monospace";
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const n1 = v => Math.round(v * 10) / 10;

async function gql(query, variables) {
  const res = await fetch('https://api.github.com/graphql', {
    method: 'POST',
    headers: { Authorization: `bearer ${TOKEN}`, 'Content-Type': 'application/json', 'User-Agent': 'matchday-scoreboard' },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) throw new Error(`GitHub API ${res.status}`);
  const json = await res.json();
  if (json.errors) throw new Error(json.errors.map(e => e.message).join('; '));
  return json.data;
}

// GraphQL `following` counts only user accounts; the REST value matches the number on the profile page.
async function restFollowing() {
  const res = await fetch(`https://api.github.com/users/${LOGIN}`, { headers: { Authorization: `bearer ${TOKEN}`, 'User-Agent': 'matchday-scoreboard' } });
  if (!res.ok) throw new Error(`GitHub REST API ${res.status}`);
  return (await res.json()).following;
}

const REPO_FIELDS = 'pageInfo { hasNextPage endCursor } nodes { stargazerCount }';
const MAIN = `query($login: String!) { user(login: $login) {
  followers { totalCount }
  repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100) { totalCount ${REPO_FIELDS} }
  contributionsCollection { contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } }
} }`;
const MORE = `query($login: String!, $after: String) { user(login: $login) {
  repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100, after: $after) { ${REPO_FIELDS} }
} }`;

async function load() {
  const { user } = await gql(MAIN, { login: LOGIN });
  if (!user) throw new Error('user not found');
  let repos = user.repositories.nodes;
  let page = user.repositories.pageInfo;
  while (page.hasNextPage) {
    const more = (await gql(MORE, { login: LOGIN, after: page.endCursor })).user.repositories;
    repos = repos.concat(more.nodes);
    page = more.pageInfo;
  }
  const days = user.contributionsCollection.contributionCalendar.weeks.flatMap(w => w.contributionDays).sort((a, b) => a.date.localeCompare(b.date));
  let best = 0, run = 0;
  for (const d of days) { run = d.contributionCount > 0 ? run + 1 : 0; best = Math.max(best, run); }
  let i = days.length - 1;
  if (i >= 0 && days[i].contributionCount === 0) i--; // today may not be finished yet
  let current = 0;
  for (; i >= 0 && days[i].contributionCount > 0; i--) current++;
  return {
    repos: user.repositories.totalCount,
    stars: repos.reduce((s, r) => s + r.stargazerCount, 0),
    followers: user.followers.totalCount,
    following: await restFollowing(),
    contributions: user.contributionsCollection.contributionCalendar.totalContributions,
    current, best,
  };
}

// ---- rendering
const ICONS = {
  repos: 'M4 19.5V5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2zm0 0a2 2 0 0 0 2 2h13',
  contributions: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 12h6m6 0h6',
  followers: 'M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 5a3 3 0 0 1 0 6M18 14c1.7.6 3 2.4 3 4.5',
  stars: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',
};
let step = 0;
// odometer: each digit is a vertical strip clipped to one cell; base transform = final digit, animation starts from 0
function odometer(value, x, baseline, size, uid) {
  const str = Number(value).toLocaleString('en-US');
  const cw = size * 0.58, ch = size * 1.08;
  let cx = x, out = '', clips = '';
  [...str].forEach((c, i) => {
    if (c === ',') { out += `<text x="${n1(cx - cw * 0.12)}" y="${baseline}" font-size="${size}" font-weight="700" fill="${C.text}">,</text>`; cx += cw * 0.2; return; }
    const id = `${uid}c${i}`;
    clips += `<clipPath id="${id}"><rect x="${n1(cx)}" y="${n1(baseline - size * 0.85)}" width="${n1(cw)}" height="${n1(ch)}"/></clipPath>`;
    const d = Number(c);
    const strip = Array.from({ length: 10 }, (_, k) => `<text x="${n1(cx + cw / 2)}" y="${n1(baseline + k * ch)}" text-anchor="middle" font-size="${size}" font-weight="700" fill="${C.text}">${k}</text>`).join('');
    out += `<g clip-path="url(#${id})"><g class="od" style="transform:translateY(${n1(-d * ch)}px);animation-delay:${(0.2 + (step++) * 0.08).toFixed(2)}s">${strip}</g></g>`;
    cx += cw;
  });
  return { svg: out, clips };
}
function tile(x, y, w, h, key, label, value, numSize, uid) {
  const od = odometer(value, x + 30, y + h - 36, numSize, uid);
  return { defs: od.clips, svg: `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="18" fill="${C.surf2}" stroke="${C.line}" stroke-width="2"/><path d="M${x + 20} ${y + 1.5}H${x + w - 20}" stroke="${C.red}" stroke-width="3" stroke-linecap="round"/>
<circle class="pulse" cx="${x + w * 0.62}" cy="${y + h - 56}" r="${n1(numSize * 0.9)}" fill="url(#gl)"/>
<g transform="translate(${x + 28} ${y + 26}) scale(1.4)"><path d="${ICONS[key]}" fill="none" stroke="${C.red}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></g>
<text x="${x + 78}" y="${y + 52}" font-size="24" font-weight="600" fill="${C.t2}" font-family="${MONO}" letter-spacing="1">${esc(label)}</text>${od.svg}` };
}
const CSS = `.od{animation:od 1.1s cubic-bezier(.2,.8,.2,1) both}@keyframes od{from{transform:translateY(0)}}.pulse{animation:pu 5s ease-in-out infinite alternate}@keyframes pu{from{opacity:.55}to{opacity:1}}@media (prefers-reduced-motion: reduce){*{animation:none!important}}`;
function wrap(W, H, d, body, defs) {
  const desc = `${d.repos} public repositories, ${d.contributions} contributions in the last 12 months, ${d.followers} followers, ${d.stars} stars earned. Following ${d.following}. Current streak ${d.current} days, best streak ${d.best} days.`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="t d">
<title id="t">GitHub statistics</title><desc id="d">${esc(desc)}</desc>
<defs><clipPath id="card"><rect width="${W}" height="${H}" rx="24"/></clipPath><radialGradient id="gl"><stop offset="0" stop-color="${C.glow}" stop-opacity=".1"/><stop offset="1" stop-color="${C.glow}" stop-opacity="0"/></radialGradient>${defs}</defs>
<style>text{font-family:${FONT}}${CSS}</style>
<g clip-path="url(#card)"><rect width="${W}" height="${H}" fill="${C.bg}"/>${body}</g>
</svg>
`;
}
const tiles = d => [['repos', 'REPOSITORIES', d.repos], ['contributions', 'CONTRIBUTIONS', d.contributions], ['followers', 'FOLLOWERS', d.followers], ['stars', 'STARS EARNED', d.stars]];
const foot = (d, date) => [`FOLLOWING ${d.following}  ·  STREAK ${d.current}D  ·  BEST ${d.best}D`, `UPDATED ${date}`];
function desktop(d, date) {
  step = 0;
  const W = 1600, H = 300, w = 365;
  let body = '', defs = '';
  tiles(d).forEach(([k, l, v], i) => { const t = tile(40 + i * (w + 20), 36, w, 200, k, l, v, 96, 'd' + i); body += t.svg; defs += t.defs; });
  body += `<text x="40" y="278" font-size="24" font-weight="500" fill="${C.t2}" font-family="${MONO}">${esc(foot(d, date)[0] + '  ·  CONTRIBUTIONS AND STREAKS: LAST 12 MONTHS  ·  ' + foot(d, date)[1])}</text>`;
  return wrap(W, H, d, body, defs);
}
function mobile(d, date) {
  step = 0;
  const W = 750, H = 580, w = 325;
  let body = '', defs = '';
  tiles(d).forEach(([k, l, v], i) => { const t = tile(40 + (i % 2) * (w + 20), 30 + Math.floor(i / 2) * 240, w, 220, k, l, v, 88, 'm' + i); body += t.svg; defs += t.defs; });
  const f = foot(d, date);
  body += `<text x="40" y="540" font-size="24" font-weight="500" fill="${C.t2}" font-family="${MONO}">${esc(f[0])}</text><text x="40" y="568" font-size="22" font-weight="500" fill="${C.t2}" font-family="${MONO}">${esc(f[1] + '  ·  LAST 12 MONTHS')}</text>`;
  return wrap(W, H, d, body, defs);
}

try {
  const data = await load();
  const date = new Date().toISOString().slice(0, 10);
  const strip = s => s.replace(/UPDATED \d{4}-\d{2}-\d{2}/g, 'UPDATED');
  let changed = 0;
  for (const [name, build] of [['stats-desktop.svg', desktop], ['stats-mobile.svg', mobile]]) {
    const file = path.join(OUT_DIR, name);
    const next = build(data, date);
    const prev = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
    if (strip(prev) === strip(next)) continue;
    fs.mkdirSync(OUT_DIR, { recursive: true });
    fs.writeFileSync(file, next);
    changed++;
  }
  console.log(changed ? 'stats written' : 'stats unchanged', JSON.stringify(data));
} catch (e) {
  console.error('stats failed:', e.message); // existing SVGs are left untouched
  process.exit(1);
}
