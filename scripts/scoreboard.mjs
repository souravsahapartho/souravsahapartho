// Generates assets/stadium/generated/scoreboard.svg from the GitHub GraphQL API.
// Zero dependencies (Node 20+). Env: GH_TOKEN (required), GH_LOGIN, OUT.
import fs from 'node:fs';
import path from 'node:path';

const TOKEN = process.env.GH_TOKEN;
const LOGIN = process.env.GH_LOGIN || 'souravsahapartho';
const OUT = process.env.OUT || 'assets/stadium/generated/scoreboard.svg';
if (!TOKEN) { console.error('GH_TOKEN is required'); process.exit(1); }

const C = { night: '#05070D', mid: '#0B1530', edge: '#1E2A47', panel: '#0A0F1E', chalk: '#E9F2EC', el: '#2EE6FF', led: '#FFB020', ledoff: '#2A1D05', muted: '#8A97B5' };
const FONT = "'Segoe UI','Helvetica Neue',Roboto,Arial,sans-serif";
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

const REPO_FIELDS = 'pageInfo { hasNextPage endCursor } nodes { stargazerCount primaryLanguage { name color } }';
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
  const langs = new Map();
  for (const r of repos) {
    if (!r.primaryLanguage) continue;
    const l = langs.get(r.primaryLanguage.name) || { name: r.primaryLanguage.name, color: r.primaryLanguage.color || C.muted, n: 0 };
    l.n++;
    langs.set(r.primaryLanguage.name, l);
  }
  return {
    repos: user.repositories.totalCount,
    stars: repos.reduce((s, r) => s + r.stargazerCount, 0),
    followers: user.followers.totalCount,
    following: await restFollowing(),
    contributions: user.contributionsCollection.contributionCalendar.totalContributions,
    current, best,
    langs: [...langs.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name)),
  };
}

// ---- 7-segment rendering
const SEG = { 0: 'abcdef', 1: 'bc', 2: 'abged', 3: 'abgcd', 4: 'fgbc', 5: 'afgcd', 6: 'afgedc', 7: 'abc', 8: 'abcdefg', 9: 'abcdfg' };
const H = (x, y, len, t) => `M${n1(x)} ${n1(y)}l${n1(t / 2)} ${n1(-t / 2)}h${n1(len - t)}l${n1(t / 2)} ${n1(t / 2)}l${n1(-t / 2)} ${n1(t / 2)}h${n1(-(len - t))}z`;
const V = (x, y, len, t) => `M${n1(x)} ${n1(y)}l${n1(t / 2)} ${n1(t / 2)}v${n1(len - t)}l${n1(-t / 2)} ${n1(t / 2)}l${n1(-t / 2)} ${n1(-t / 2)}v${n1(-(len - t))}z`;
function digit(d, x, y, w, h) {
  const t = h * 0.14, q = h / 2 - t * 1.1;
  const p = {
    a: H(x + t * 0.1, y + t / 2, w - t * 0.2, t), g: H(x + t * 0.1, y + h / 2, w - t * 0.2, t), d: H(x + t * 0.1, y + h - t / 2, w - t * 0.2, t),
    f: V(x + t / 2, y + t * 0.6, q, t), b: V(x + w - t / 2, y + t * 0.6, q, t), e: V(x + t / 2, y + h / 2 + t * 0.5, q, t), c: V(x + w - t / 2, y + h / 2 + t * 0.5, q, t),
  };
  const on = SEG[d];
  const lit = [...on].map(k => p[k]).join('');
  const off = Object.keys(p).filter(k => !on.includes(k)).map(k => p[k]).join('');
  return { lit, off };
}
let step = 0;
function number(value, x, y, maxW, h = 84) {
  const s = String(value);
  let w = h * 0.55, gap = 8;
  const total = () => s.length * w + (s.length - 1) * gap;
  while (total() > maxW && w > 20) { w -= 2; gap = Math.max(4, gap - 0.5); h -= 3; }
  let out = '';
  [...s].forEach((ch, i) => {
    const { lit, off } = digit(ch, x + i * (w + gap), y, w, h);
    out += `<g><path d="${off}" fill="${C.ledoff}"/><path class="dg" style="animation-delay:${(step++ * 0.05).toFixed(2)}s" d="${lit}" fill="${C.led}"/></g>`;
  });
  return { svg: out, width: total() };
}
const text = (s, x, y, size, fill, w = 700) => `<text x="${n1(x)}" y="${n1(y)}" font-size="${size}" font-weight="${w}" fill="${fill}">${esc(s)}</text>`;

function build(d, date) {
  const W = 1000, Hh = 780;
  let body = `<rect width="${W}" height="${Hh}" fill="${C.night}"/><rect x="10" y="10" width="${W - 20}" height="${Hh - 20}" rx="22" fill="${C.mid}" stroke="${C.edge}" stroke-width="4"/>`;
  body += text('MATCHDAY SCOREBOARD', 40, 70, 38, C.led, 800) + text(`UPDATED ${date}`, 960, 66, 30, C.muted, 600).replace('<text ', '<text text-anchor="end" ');
  body += `<path d="M40 92H960" stroke="${C.el}" stroke-opacity=".35" stroke-width="2"/>`;
  const panel = (x, y, w, label, value, suffix) => {
    let s = `<rect x="${x}" y="${y}" width="${w}" height="160" rx="14" fill="${C.panel}" stroke="${C.edge}" stroke-width="3"/>` + text(label, x + 20, y + 46, 32, C.muted, 700);
    const num = number(value, x + 20, y + 66, w - 40 - (suffix ? 44 : 0));
    s += num.svg;
    if (suffix) s += text(suffix, x + 20 + num.width + 10, y + 66 + 76, 40, C.led, 800);
    return s;
  };
  const a = [['REPOS', d.repos], ['STARS', d.stars], ['FOLLOWERS', d.followers], ['FOLLOWING', d.following]];
  const wa = (W - 80 - 3 * 14) / 4;
  a.forEach(([l, v], i) => { body += panel(40 + i * (wa + 14), 112, wa, l, v); });
  const b = [['CONTRIBUTIONS', d.contributions, '', 340], ['STREAK', d.current, 'D', 276], ['BEST RUN', d.best, 'D', 276]];
  let bxp = 40;
  b.forEach(([l, v, s, w]) => { body += panel(bxp, 292, w, l, v, s); bxp += w + 14; });
  // formation bar
  body += text('FORMATION · REPOS BY LANGUAGE', 40, 504, 32, C.muted, 700);
  const top = d.langs.slice(0, 6), rest = d.langs.slice(6).reduce((s, l) => s + l.n, 0);
  const items = top.map(l => ({ name: l.name, color: l.color, n: l.n })).concat(rest ? [{ name: 'Other', color: C.muted, n: rest }] : []);
  const total = items.reduce((s, l) => s + l.n, 0) || 1;
  let bx = 40;
  body += `<clipPath id="bar"><rect x="40" y="520" width="920" height="40" rx="10"/></clipPath><g clip-path="url(#bar)">`;
  items.forEach(it => { const w = 920 * it.n / total; body += `<rect x="${n1(bx)}" y="520" width="${n1(w)}" height="40" fill="${esc(it.color)}"/>`; bx += w; });
  body += '</g>';
  items.forEach((it, i) => {
    const x = 40 + (i % 2) * 470, y = 612 + Math.floor(i / 2) * 46;
    body += `<rect x="${x}" y="${y - 24}" width="24" height="24" rx="5" fill="${esc(it.color)}"/>` + text(`${it.name} ${it.n}`, x + 36, y, 34, C.chalk, 600);
  });
  body += `<rect class="scan" x="10" y="-6" width="${W - 20}" height="2" fill="#fff" fill-opacity=".08"/>`;
  const desc = `${d.repos} public repositories, ${d.stars} stars, ${d.followers} followers, ${d.following} following, ${d.contributions} contributions in the last 12 months, current streak ${d.current} days, best streak ${d.best} days. Repositories by language: ${items.map(i => `${i.name} ${i.n}`).join(', ')}.`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${Hh}" role="img" aria-labelledby="t d">
<title id="t">Matchday scoreboard: live GitHub statistics</title><desc id="d">${esc(desc)}</desc>
<style>text{font-family:${FONT}}.dg{animation:segIn .3s ease-out both}.scan{animation:scan 6s linear infinite}@keyframes segIn{from{opacity:0}to{opacity:1}}@keyframes scan{to{transform:translateY(790px)}}@media (prefers-reduced-motion: reduce){*{animation:none!important}}</style>
${body}
</svg>
`;
}

try {
  const data = await load();
  const svg = build(data, new Date().toISOString().slice(0, 10));
  const strip = s => s.replace(/UPDATED \d{4}-\d{2}-\d{2}/g, 'UPDATED');
  const prev = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : '';
  if (strip(prev) === strip(svg)) console.log('scoreboard unchanged');
  else {
    fs.mkdirSync(path.dirname(OUT), { recursive: true });
    fs.writeFileSync(OUT, svg);
    console.log('scoreboard written', JSON.stringify({ ...data, langs: data.langs.length }));
  }
} catch (e) {
  console.error('scoreboard failed:', e.message); // existing SVG is left untouched
  process.exit(1);
}
