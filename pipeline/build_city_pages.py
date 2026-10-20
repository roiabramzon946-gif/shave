"""
Generate one static page per city (for Google), a cities index page and the sitemap.

Runs automatically at the end of build_model.py. Can also be run alone:
    python pipeline/build_city_pages.py

Output:
    docs/city/<city-name>.html     e.g. docs/city/רעננה.html
    docs/city/index.html           all cities, grouped by district
    docs/sitemap.xml
"""
from __future__ import annotations

import html
import json
import math
import re
import sys
from datetime import datetime
from urllib.parse import quote
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "city"
SITE = "https://roiabramzon946-gif.github.io/shave/"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_model import CITY_DISTRICT, features  # noqa: E402

DISTRICT_ORDER = ["תל אביב", "מרכז", "ירושלים", "חיפה", "צפון", "דרום", "ארצי"]
DISTRICT_TITLE = {"תל אביב": "מחוז תל אביב", "מרכז": "מחוז המרכז", "ירושלים": "מחוז ירושלים",
                  "חיפה": "מחוז חיפה", "צפון": "מחוז הצפון", "דרום": "מחוז הדרום",
                  "ארצי": "יהודה ושומרון"}
MIN_STREET_DEALS = 12
e = html.escape


def slug(name: str) -> str:
    s = re.sub(r"[\"'׳״()]", "", name)
    return re.sub(r"[\s/]+", "-", s.strip())


def ils(v: float) -> str:
    return "₪" + f"{round(v / 1000) * 1000:,.0f}"


def ils_exact(v: float) -> str:
    return "₪" + f"{v:,.0f}"


def pct(v: float) -> str:
    return ("+" if v > 0 else "") + f"{v * 100:.1f}%".replace("-", "−")


def q_label(q: str) -> str:
    return f"{q[5:]}/{q[:4]}"


# ----------------------------------------------------------------------------- model helpers

def model_value(meta, city, area, street_eff=0.0):
    """Value of a standard apartment (floor 3 of 6, built 2000, regular, second-hand) like the site computes it."""
    row = pd.DataFrame([{"floor": 3, "bfloors": 6, "built": 2000, "rooms": np.nan, "area": area,
                         "new": False, "status": "", "type": "דירה בבית קומות"}])
    x = features(row, meta["ref_year"])[0]
    logp = meta["intercept"] + city["eff"] + float(np.dot(meta["coef"], x)) + street_eff
    return math.exp(logp) * area


def trend_change(trend, years):
    if len(trend) < 2:
        return None
    last_q, last_v = trend[-1]
    target = f"{int(last_q[:4]) - years}{last_q[4:]}"
    for q, v in trend:
        if q == target:
            return last_v / v - 1
    return None


def trend_svg(trend):
    t = trend[-24:] if len(trend) > 24 else trend
    if len(t) < 2:
        return ""
    W, H, L, R, T, B = 640, 190, 10, 70, 16, 26
    ys = [v for _, v in t]
    lo, hi = min(ys), max(ys)
    x = lambda i: L + i / (len(t) - 1) * (W - L - R)
    y = lambda v: T + (1 - (v - lo) / ((hi - lo) or 1)) * (H - T - B)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(t))
    parts = [f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
             f'<text x="{W - R + 8}" y="{y(v) + 4:.1f}">{v:,.0f}</text>' for v in (lo, hi)]
    parts.append(f'<polygon points="{x(0):.1f},{H - B} {pts} {x(len(t) - 1):.1f},{H - B}" class="area"/>')
    parts.append(f'<polyline points="{pts}" class="line"/>')
    parts.append(f'<circle cx="{x(len(t) - 1):.1f}" cy="{y(t[-1][1]):.1f}" r="4" class="dot"/>')
    years = [(i, q[:4]) for i, (q, _) in enumerate(t) if q.endswith("Q1")]
    step = max(1, math.ceil(len(years) / 5))
    for k, (i, yr) in enumerate(years):
        if k % step == 0:
            anchor = "start" if i == 0 else "middle"
            parts.append(f'<text x="{x(i):.1f}" y="{H - 6}" text-anchor="{anchor}">{yr}</text>')
    label = f'מחיר טיפוסי למ"ר מ־{q_label(t[0][0])} עד {q_label(t[-1][0])}'
    return f'<svg class="trend" viewBox="0 0 {W} {H}" role="img" aria-label="{e(label)}">{"".join(parts)}</svg>'


# ----------------------------------------------------------------------------- page parts

CSS = """
:root{--paper:#f3f5f6;--sheet:#fff;--ink:#17212b;--muted:#5d6b78;--rule:#d5dce2;--accent:#2b4c7e;--accent-soft:#e3eaf4;
--good:#1f7a4d;--bad:#b23a2e;--display:"Suez One","IBM Plex Sans Hebrew",Arial,sans-serif;
--body:"IBM Plex Sans Hebrew","Segoe UI",Arial,sans-serif;--num:"IBM Plex Mono",ui-monospace,Consolas,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#111820;--sheet:#18222c;--ink:#e7edf2;--muted:#9aa8b5;
--rule:#2c3a47;--accent:#8fb1e3;--accent-soft:#22324a;--good:#6fcf9c;--bad:#f08a7e;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#111820;--sheet:#18222c;--ink:#e7edf2;--muted:#9aa8b5;--rule:#2c3a47;--accent:#8fb1e3;
--accent-soft:#22324a;--good:#6fcf9c;--bad:#f08a7e;color-scheme:dark}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--paper);color:var(--ink);font:400 16px/1.6 var(--body)}
.wrap{max-width:880px;margin:0 auto;padding-block:24px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:22px}
a{color:var(--accent)}a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;font-size:.9rem}
.brand{font:400 1.6rem/1 var(--display);color:var(--accent);text-decoration:none}
.crumbs{color:var(--muted)}.crumbs a{color:var(--muted)}
h1{font:400 clamp(1.9rem,5.5vw,2.7rem)/1.15 var(--display);margin:0;text-wrap:balance}
h2{font:600 1.15rem var(--body);margin:0 0 10px}
.lead{margin:0;font-size:1.08rem;max-width:62ch;text-wrap:pretty}
.sheet{background:var(--sheet);border:1px solid var(--rule);border-radius:10px;padding:20px;min-width:0}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}
.fig{display:grid;gap:2px}.fig .k{font-size:.82rem;color:var(--muted)}
.fig .v{font:500 1.45rem/1.25 var(--num);direction:ltr;text-align:right;font-variant-numeric:tabular-nums}
.fig .s{font-size:.82rem;color:var(--muted)}
.up{color:var(--good)}.down{color:var(--bad)}
.num{font-family:var(--num);font-variant-numeric:tabular-nums;direction:ltr;unicode-bidi:isolate}
svg.trend{width:100%;height:auto;display:block;direction:ltr;max-height:240px}
svg.trend text{fill:var(--muted);font:11px var(--num)}svg.trend .grid{stroke:var(--rule)}
svg.trend .area{fill:var(--accent-soft)}svg.trend .line{fill:none;stroke:var(--accent);stroke-width:2}svg.trend .dot{fill:var(--accent)}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:22px}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:.92rem}
th{text-align:right;font-weight:500;color:var(--muted);font-size:.8rem;border-bottom:1px solid var(--rule);padding:6px 8px;white-space:nowrap}
td{border-bottom:1px solid var(--rule);padding:7px 8px;white-space:nowrap}
td.n{font-family:var(--num);font-variant-numeric:tabular-nums;direction:ltr;text-align:right}
.cta{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap;background:var(--accent-soft);
border-radius:10px;padding:18px 20px}.cta p{margin:0;flex:1 1 260px}
.button{display:inline-block;font-weight:600;background:var(--accent);color:var(--sheet);border-radius:6px;padding:11px 20px;text-decoration:none}
.links{display:flex;flex-wrap:wrap;gap:8px 14px;margin:0;padding:0;list-style:none}
.district{display:grid;gap:8px}
.city-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:4px 18px;margin:0;padding:0;list-style:none}
.city-list li{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid var(--rule);padding:6px 0}
.note{color:var(--muted);font-size:.85rem;margin:0;max-width:75ch}
footer{color:var(--muted);font-size:.82rem;display:grid;gap:6px}footer p{margin:0;max-width:75ch}
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Hebrew:wght@400;500;600'
         '&family=IBM+Plex+Mono:wght@500&family=Suez+One&display=swap">')
ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' "
        "rx='6' fill='%232b4c7e'/%3E%3Ctext x='16' y='23' font-size='20' text-anchor='middle' fill='white' "
        "font-family='Arial'%3E%E2%82%AA%3C/text%3E%3C/svg%3E")


def page(title, description, url, body, jsonld):
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="website"><meta property="og:locale" content="he_IL">
<meta property="og:site_name" content="שווה?"><meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}"><meta property="og:url" content="{e(url)}">
<link rel="icon" href="{ICON}">
{FONTS}
<link rel="stylesheet" href="city.css">
<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>
</head>
<body>
<div class="wrap">
{body}
<footer>
  <p>הנתונים: מאגר עסקאות הנדל"ן של רשות המסים (odata.org.il, CC BY), ומדד מחירי הדירות של הלמ"ס לעדכון המחירים להיום.
  המספרים הם חישוב סטטיסטי על עסקאות עבר, לא שמאות ולא ייעוץ.</p>
</footer>
</div>
</body>
</html>
"""


def city_page(meta, city, slugs, district, peers, gen_date):
    name = city["name"]
    url = SITE + "city/" + quote(slugs[name]) + ".html"
    trend = city["trend"]
    ppsqm = trend[-1][1] if trend else None
    ch1, ch5 = trend_change(trend, 1), trend_change(trend, 5)
    asof = q_label(meta["latest_quarter"])
    ta = meta.get("typical_area", {})
    apt = []
    for rooms in ("3", "4", "5"):
        if rooms in ta:
            apt.append((rooms, ta[rooms], model_value(meta, city, ta[rooms])))
    std100 = model_value(meta, city, 100) / 100

    # streets: price per m² of the standard apartment on that street, today
    st = [(s, std100 * math.exp(v[0]), v[1]) for s, v in city["streets"].items() if v[1] >= MIN_STREET_DEALS]
    st.sort(key=lambda r: -r[1])
    n_show = 8 if len(st) >= 16 else len(st) // 2
    top, bottom = st[:n_show], list(reversed(st[-n_show:])) if n_show else []

    cols = {c: i for i, c in enumerate(city["deals_cols"])}
    deals = sorted(city["deals"], key=lambda r: r[cols["date"]], reverse=True)[:15]

    def chg(v, label):
        if v is None:
            return ""
        cls = "up" if v > 0 else "down"
        return f'<div class="fig"><span class="k">{label}</span><span class="v {cls}">{pct(v)}</span></div>'

    figs = (f'<div class="fig"><span class="k">מחיר טיפוסי למ"ר</span><span class="v">{ils_exact(ppsqm)}</span>'
            f'<span class="s">רבעון {asof}</span></div>' if ppsqm else "")
    figs += chg(ch1, "שינוי בשנה האחרונה") + chg(ch5, "שינוי ב־5 שנים")

    apt_rows = "".join(f'<tr><td>{r} חדרים</td><td class="n">{a}</td><td class="n">{ils(v)}</td></tr>'
                       for r, a, v in apt)
    apt_block = (f'<div class="sheet"><h2>כמה עולה דירה ב{e(name)}?</h2><div class="table-wrap"><table>'
                 f'<thead><tr><th>דירה</th><th>שטח טיפוסי (מ"ר)</th><th>שווי משוער</th></tr></thead><tbody>{apt_rows}</tbody></table></div>'
                 f'<p class="note">לדירה יד שנייה בקומה 3 בבניין משנת 2000, לפי מודל המחירים של האתר. '
                 f'המחיר בפועל תלוי ברחוב, בקומה, בגיל הבניין ובמצב הדירה.</p></div>') if apt else ""

    def street_table(rows):
        return ("<div class=\"table-wrap\"><table><thead><tr><th>רחוב</th><th>מחיר למ\"ר</th><th>עסקאות</th></tr></thead><tbody>"
                + "".join(f'<tr><td>{e(s)}</td><td class="n">{ils_exact(round(p, -1))}</td><td class="n">{n:,}</td></tr>'
                          for s, p, n in rows) + "</tbody></table></div>")

    streets_block = ""
    if n_show >= 3:
        streets_block = (f'<div class="cols"><div class="sheet"><h2>הרחובות היקרים ב{e(name)}</h2>{street_table(top)}</div>'
                         f'<div class="sheet"><h2>הרחובות הזולים ב{e(name)}</h2>{street_table(bottom)}</div></div>'
                         f'<p class="note">מחיר למ"ר משוער היום לדירה סטנדרטית ברחוב, ברחובות עם {MIN_STREET_DEALS} עסקאות לפחות.</p>')

    def d(s):
        return f"{s[8:10]}/{s[5:7]}/{s[2:4]}"

    deal_rows = "".join(
        f'<tr><td>{e(r[cols["address"]] or r[cols["street"]])}</td><td class="n">{d(r[cols["date"]])}</td>'
        f'<td class="n">{"קרקע" if r[cols["floor"]] == 0 else ("–" if r[cols["floor"]] is None else r[cols["floor"]])}</td>'
        f'<td class="n">{r[cols["area"]]}</td><td class="n">{ils_exact(r[cols["price"]])}</td>'
        f'<td class="n">{ils_exact(round(r[cols["price"]] / r[cols["area"]], -1))}</td></tr>' for r in deals)
    deals_block = (f'<div class="sheet"><h2>עסקאות אחרונות ב{e(name)}</h2><div class="table-wrap"><table><thead><tr>'
                   f'<th>כתובת</th><th>תאריך</th><th>קומה</th><th>מ"ר</th><th>מחיר</th><th>למ"ר</th></tr></thead>'
                   f'<tbody>{deal_rows}</tbody></table></div></div>') if deals else ""

    peer_links = "".join(f'<li><a href="{e(slugs[p])}.html">מחירי דירות ב{e(p)}</a></li>' for p in peers)
    peers_block = (f'<div><h2>ערים נוספות ב{e(DISTRICT_TITLE[district])}</h2><ul class="links">{peer_links}</ul></div>'
                   if peers else "")

    lead_parts = []
    if ppsqm:
        lead_parts.append(f'המחיר הטיפוסי למ"ר בדירות יד שנייה ב{e(name)} הוא כ־{ils_exact(round(ppsqm, -2))}')
    if ch1 is not None:
        lead_parts.append(f"ובשנה האחרונה המחירים {'עלו' if ch1 > 0 else 'ירדו'} ב־{abs(ch1) * 100:.1f}%")
    lead = (", ".join(lead_parts) + "." if lead_parts else "") + \
        f" הנתונים מבוססים על {meta_city_n(meta, name):,} עסקאות שנרשמו ברשות המסים."

    checker = "../?city=" + quote(name)
    body = f"""<div class="top"><a class="brand" href="../">שווה?</a>
<nav class="crumbs" aria-label="ניווט"><a href="../">בדיקת מחיר</a> › <a href="./">מחירי דירות לפי עיר</a> › {e(name)}</nav></div>
<h1>מחירי דירות ב{e(name)}</h1>
<p class="lead">{lead}</p>
<div class="sheet"><div class="figs">{figs}</div></div>
<div class="cta"><p><strong>מצאתם דירה ב{e(name)}?</strong> בדקו אם המחיר המבוקש הוגן לפי הרחוב, הקומה והגודל.</p>
<a class="button" href="{checker}">בדיקת מחיר דירה ב{e(name)}</a></div>
<div class="sheet"><h2>מגמת המחירים ב{e(name)}</h2>{trend_svg(trend)}</div>
{apt_block}
{streets_block}
{deals_block}
{peers_block}
<p class="note">עודכן {gen_date}. מחירי העבר מותאמים להיום לפי מדד מחירי הדירות של הלמ"ס ב{e(DISTRICT_TITLE[district])}.</p>"""

    desc = f'מחירי דירות ב{name}: מחיר טיפוסי למ"ר' + (f" {ils_exact(round(ppsqm, -2))}" if ppsqm else "") + \
        (f", שינוי של {pct(ch1)} בשנה" if ch1 is not None else "") + \
        ", הרחובות היקרים והזולים ועסקאות אחרונות, לפי עסקאות אמת של רשות המסים."
    jsonld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "שווה?", "item": SITE},
        {"@type": "ListItem", "position": 2, "name": "מחירי דירות לפי עיר", "item": SITE + "city/"},
        {"@type": "ListItem", "position": 3, "name": f"מחירי דירות ב{name}", "item": url}]}
    return page(f'מחירי דירות ב{name} {meta["latest_quarter"][:4]}: מחיר למ"ר ועסקאות | שווה?', desc, url, body, jsonld)


def meta_city_n(meta, name):
    return next((c["n"] for c in meta["cities"] if c["name"] == name), 0)


def index_page(meta, rows, slugs, gen_date):
    url = SITE + "city/"
    groups = []
    for dist in DISTRICT_ORDER:
        items = sorted([r for r in rows if r["district"] == dist], key=lambda r: -r["n"])
        if not items:
            continue
        lis = "".join(
            f'<li><a href="{e(slugs[r["name"]])}.html">{e(r["name"])}</a>'
            f'<span class="num">{ils_exact(round(r["ppsqm"], -2)) if r["ppsqm"] else ""}</span></li>' for r in items)
        groups.append(f'<section class="district"><h2>{e(DISTRICT_TITLE[dist])}</h2><ul class="city-list">{lis}</ul></section>')
    body = f"""<div class="top"><a class="brand" href="../">שווה?</a>
<nav class="crumbs" aria-label="ניווט"><a href="../">בדיקת מחיר</a> › מחירי דירות לפי עיר</nav></div>
<h1>מחירי דירות לפי עיר</h1>
<p class="lead">מחיר טיפוסי למ"ר בדירות יד שנייה ב־{len(rows)} ערים ויישובים, לפי עסקאות אמת של רשות המסים,
מעודכן לרבעון {q_label(meta["latest_quarter"])}. לחצו על עיר לפרטים: מגמת מחירים, הרחובות היקרים והזולים ועסקאות אחרונות.</p>
{"".join(groups)}
<p class="note">עודכן {gen_date}.</p>"""
    desc = f'מחירי דירות למ"ר ב־{len(rows)} ערים בישראל, לפי עסקאות אמת של רשות המסים ומדד מחירי הדירות של הלמ"ס.'
    jsonld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "שווה?", "item": SITE},
        {"@type": "ListItem", "position": 2, "name": "מחירי דירות לפי עיר", "item": url}]}
    return page(f'מחירי דירות לפי עיר {meta["latest_quarter"][:4]} | שווה?', desc, url, body, jsonld)


def inject_home_links(rows, slugs):
    """Write links to the biggest cities into the home page, between the CITY_LINKS markers."""
    home = DOCS / "index.html"
    s = home.read_text(encoding="utf-8")
    start, end = "<!--CITY_LINKS-->", "<!--/CITY_LINKS-->"
    if start not in s or end not in s:
        return
    top = sorted(rows, key=lambda r: -r["n"])[:24]
    links = "".join(f'<li><a href="city/{e(slugs[r["name"]])}.html">מחירי דירות ב{e(r["name"])}</a></li>' for r in top)
    block = f'{start}<ul class="city-links">{links}<li><a href="city/">כל הערים ›</a></li></ul>{end}'
    s = s[:s.index(start)] + block + s[s.index(end) + len(end):]
    home.write_text(s, encoding="utf-8")


def main():
    meta = json.loads((DOCS / "data" / "meta.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.html"):
        old.unlink()
    (OUT / "city.css").write_text(CSS.strip() + "\n", encoding="utf-8")

    cities = []
    for c in meta["cities"]:
        data = json.loads((DOCS / "data" / "cities" / f"{c['id']}.json").read_text(encoding="utf-8"))
        cities.append(data)
    slugs, used = {}, set()
    for c in cities:
        s = slug(c["name"])
        while s in used:
            s += "-2"
        used.add(s)
        slugs[c["name"]] = s

    gen_date = datetime.now().strftime("%d/%m/%Y")
    rows = []
    for c in cities:
        dist = CITY_DISTRICT.get(c["name"], "ארצי")
        rows.append({"name": c["name"], "district": dist, "n": meta_city_n(meta, c["name"]),
                     "ppsqm": c["trend"][-1][1] if c["trend"] else None})
    for c in cities:
        dist = CITY_DISTRICT.get(c["name"], "ארצי")
        peers = [r["name"] for r in sorted(rows, key=lambda r: -r["n"]) if r["district"] == dist and r["name"] != c["name"]][:10]
        (OUT / f"{slugs[c['name']]}.html").write_text(city_page(meta, c, slugs, dist, peers, gen_date), encoding="utf-8")
    (OUT / "index.html").write_text(index_page(meta, rows, slugs, gen_date), encoding="utf-8")

    today = datetime.now().strftime("%Y-%m-%d")
    urls = [SITE, SITE + "city/", SITE + "embed.html"] + [SITE + "city/" + quote(slugs[c["name"]]) + ".html" for c in cities]
    sm = "".join(f"  <url><loc>{e(u)}</loc><lastmod>{today}</lastmod></url>\n" for u in urls)
    (DOCS / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                      f"{sm}</urlset>\n", encoding="utf-8")
    inject_home_links(rows, slugs)
    print(f"  city pages: {len(cities)} pages + index in {OUT}, sitemap with {len(urls)} addresses")


if __name__ == "__main__":
    main()
