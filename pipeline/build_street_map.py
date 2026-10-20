"""
Download street lines from OpenStreetMap (via the Overpass API) and match them to the street
names in the Tax Authority data, so the site can draw streets on a map.

Map data © OpenStreetMap contributors, ODbL (the site shows the attribution on every map).

Usage (after build_model.py, needs internet; takes ~10-20 minutes the first time):
    python pipeline/build_street_map.py
    python pipeline/build_street_map.py --cities 5      # only the 5 biggest cities, for a quick look

Output:
    docs/data/streets/<city>.json     street lines per city ([lat, lon] points)
    docs/data/streets/index.json      which cities have a map, and how many streets matched
    data/osm_cache/                   raw Overpass answers (re-runs are fast, delete to refresh)

Street lines change rarely: run this once, and again maybe once a year.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "data" / "streets"
CACHE = ROOT / "data" / "osm_cache"
OVERPASS = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
BBOX = (29.3, 34.0, 33.5, 36.0)            # south, west, north, east: Israel incl. Judea & Samaria
SIMPLIFY = 0.00008                          # ~8 m: keeps the files small, invisible at city zoom

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_city_pages import slug  # noqa: E402

S = requests.Session()
S.headers.update({"User-Agent": "shave-apartment-price-checker (github.com/roiabramzon946-gif/shave)"})


# ----------------------------------------------------------------------------- Overpass

def overpass(query: str, cache_name: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{cache_name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    for attempt in range(5):
        try:
            r = S.post(OVERPASS, data={"data": query}, timeout=300)
        except requests.RequestException as e:
            wait = 15 * (attempt + 1)
            print(f"    network error ({e.__class__.__name__}), retrying in {wait}s")
            time.sleep(wait)
            continue
        if r.status_code in (429, 502, 503, 504):
            wait = 30 * (attempt + 1)
            print(f"    Overpass is busy (HTTP {r.status_code}), retrying in {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = r.json()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        time.sleep(2)                       # be polite to the shared Overpass server
        return data
    raise SystemExit("Overpass did not answer after several tries. Try again later.")


# ----------------------------------------------------------------------------- names

STREET_TYPES = {"רחוב", "רח", "שדרות", "שדרת", "שד", "סמטת", "סמטה", "סמ", "שביל", "כיכר", "ככר", "מעלה", "נתיב", "משעול"}


def clean(s: str) -> str:
    s = (s or "").replace("־", "-")
    s = re.sub(r"[\"'`׳״]", "", s)                 # ז'בוטינסקי -> זבוטינסקי, רמב"ם -> רמבם
    s = re.sub(r"[.,()\-–/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def street_key(s: str) -> str:
    words = clean(s).split(" ")
    while len(words) > 1 and words[0] in STREET_TYPES:
        words = words[1:]
    return " ".join(words)


def skeleton(s: str) -> str:
    """Spelling-insensitive key: 'וייצמן' == 'ויצמן', 'קריית' == 'קרית'."""
    return re.sub(r"[יו\s]", "", street_key(s))


TITLES = {"הרב", "רב", "רבי", "הנשיא", "דר", "פרופ", "פרופסור", "המשורר", "הסופר", "האלוף", "אלוף", "השופט",
          "סרן", "רסן", "סגן", "המלך", "המלכה", "הנביא", "רבנו", "רבינו"}


def tokens(s: str) -> frozenset:
    """Order-insensitive word set without titles, spelling-insensitive: 'כהן יעקב' == 'יעקב כהן'."""
    words = [w for w in street_key(s).split(" ") if w not in TITLES]
    return frozenset(re.sub(r"[יו]", "", w) for w in words if w)


def main_word(s: str) -> str:
    """First word after titles: the surname in the Tax Authority's 'Surname Firstname' order."""
    words = [w for w in street_key(s).split(" ") if w and w not in TITLES]
    return re.sub(r"[יו]", "", words[0]) if words else ""


def city_key(s: str) -> str:
    return re.sub(r"[יו\s]", "", clean(s))


def names_of(tags: dict):
    for k in ("name:he", "name", "alt_name:he", "alt_name", "old_name:he", "old_name", "official_name:he", "official_name"):
        v = tags.get(k)
        if v:
            for part in v.split(";"):
                if re.search(r"[א-ת]", part):
                    yield part.strip()


# ----------------------------------------------------------------------------- geometry

def simplify(points, tol=SIMPLIFY):
    """Ramer-Douglas-Peucker on [lat, lon] points."""
    if len(points) < 3:
        return points
    (y1, x1), (y2, x2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
    far, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        y, x = points[i]
        d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
        if d > far:
            far, idx = d, i
    if far <= tol:
        return [points[0], points[-1]]
    return simplify(points[: idx + 1], tol)[:-1] + simplify(points[idx:], tol)


# ----------------------------------------------------------------------------- main

def find_city_areas(cities):
    s, w, n, e = BBOX
    q = (f'[out:json][timeout:300];rel["boundary"="administrative"]["admin_level"~"^(6|7|8|9)$"]'
         f'({s},{w},{n},{e});out tags;')
    data = overpass(q, "boundaries")
    by_key = {}
    for el in data.get("elements", []):
        level = int(el["tags"].get("admin_level", 9))
        for nm in names_of(el["tags"]):
            k = city_key(nm)
            if k not in by_key or abs(level - 8) < abs(by_key[k][1] - 8):   # prefer municipal level 8
                by_key[k] = (el["id"], level, nm)
    found = {}
    for c in cities:
        k = city_key(c)
        if k in by_key:
            found[c] = by_key[k][0]
            continue
        close = difflib.get_close_matches(k, list(by_key), n=1, cutoff=0.9)
        if close:
            found[c] = by_key[close[0]][0]
    return found


def city_ways(city, rel_id, idx, n_ours=0):
    ways = _city_ways(city, rel_id, idx)
    named = {w.get("tags", {}).get("name") for w in ways}
    if rel_id and len(named) < 0.4 * n_ours:
        # the boundary on the map is incomplete: add the streets around the town center
        print(f"    {city}: only {len(named)} street names inside the boundary, adding the area around the center")
        seen = {w.get("id") for w in ways}
        ways += [w for w in _city_ways(city, None, idx, radius=4500) if w.get("id") not in seen]
    return ways


def _city_ways(city, rel_id, idx, radius=2500):
    if rel_id:
        q = (f'[out:json][timeout:300];area(id:{3600000000 + rel_id})->.a;'
             f'way(area.a)["highway"]["name"];out tags geom;')
    else:  # small places without a boundary: streets around the place point
        s, w, n, e = BBOX
        safe = city.replace('"', '\\"')
        q = (f'[out:json][timeout:300];node["place"]["name"="{safe}"]({s},{w},{n},{e})->.p;'
             f'way(around.p:{radius})["highway"]["name"];out tags geom;')
    name = f"city_{idx:03d}" if radius == 2500 else f"city_{idx:03d}_around{radius}"
    return overpass(q, name).get("elements", [])


def match_streets(our_streets, ways):
    """{our street name: [line, ...]} for the streets we can find on the map."""
    osm = {}                                   # key -> list of lines
    for w in ways:
        geom = w.get("geometry")
        if not geom or len(geom) < 2:
            continue
        line = [[round(p["lat"], 5), round(p["lon"], 5)] for p in geom]
        for nm in set(names_of(w.get("tags", {}))):
            osm.setdefault(street_key(nm), []).append(line)
    by_skel, by_tok = {}, {}
    for k, lines in osm.items():
        by_skel.setdefault(skeleton(k), []).extend(lines)
        by_tok.setdefault(tokens(k), []).extend(lines)
    keys = list(osm)
    tok_keys = [t for t in by_tok if t]
    out = {}
    for st in our_streets:
        k, t = street_key(st), tokens(st)
        # 1 exact, 2 spelling, 3 word order ("כהן יעקב" / "יעקב כהן")
        lines = osm.get(k) or by_skel.get(skeleton(st)) or (by_tok.get(t) if t else None)
        # 4 one name contains the other ("שפרינצק" / "יוסף שפרינצק"), only when it points to one street
        if not lines and t and max(len(w) for w in t) >= 3:
            mw = main_word(st)
            # the map name may be longer ("מינץ" / "בנימין מינץ"), or shorter only if it keeps the main
            # name ("הרב יחבוב אהרון" / "הרב יחבוב"), never just a first name ("סמבורסקי שמואל" / "שמואל")
            cands = [c for c in tok_keys if t < c or (c < t and mw in c)]
            if cands:
                best = min(len(c ^ t) for c in cands)
                top = [c for c in cands if len(c ^ t) == best]
                if len(top) == 1:
                    lines = by_tok[top[0]]
        # 5 near-identical spelling
        if not lines:
            close = difflib.get_close_matches(k, keys, n=1, cutoff=0.88)
            lines = osm.get(close[0]) if close else None
        if lines:
            out[st] = [simplify(l) for l in lines]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", type=int, default=0, help="only the N biggest cities")
    args = ap.parse_args()

    meta = json.loads((DOCS / "data" / "meta.json").read_text(encoding="utf-8"))
    cities = meta["cities"][: args.cities] if args.cities else meta["cities"]
    names = [c["name"] for c in cities]
    OUT.mkdir(parents=True, exist_ok=True)

    print("Finding city boundaries on OpenStreetMap ...")
    areas = find_city_areas(names)
    print(f"  {len(areas)} of {len(names)} cities have a boundary; the rest use the area around the town center")

    index, tot_m, tot_n = {}, 0, 0
    for i, c in enumerate(cities):
        city = json.loads((DOCS / "data" / "cities" / f"{c['id']}.json").read_text(encoding="utf-8"))
        ours = [s for s in city["streets"] if s]
        try:
            ways = city_ways(c["name"], areas.get(c["name"]), i, len(ours))
        except SystemExit:
            raise
        except Exception as e:
            print(f"  {c['name']}: failed ({e}), skipping")
            continue
        matched = match_streets(ours, ways)
        # deals-weighted match rate: big streets matter more than tiny ones
        n_all = sum(city["streets"][s][1] for s in ours) or 1
        n_hit = sum(city["streets"][s][1] for s in matched)
        tot_m += n_hit; tot_n += n_all
        print(f"  {c['name']:22s} {len(matched):4d}/{len(ours):<4d} streets matched "
              f"({n_hit / n_all:.0%} of the deals)")
        if not matched:
            continue
        pts = [p for lines in matched.values() for l in lines for p in l]
        bounds = [[min(p[0] for p in pts), min(p[1] for p in pts)], [max(p[0] for p in pts), max(p[1] for p in pts)]]
        f = slug(c["name"])
        (OUT / f"{f}.json").write_text(json.dumps({"city": c["name"], "bounds": bounds, "streets": matched},
                                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index[c["name"]] = {"file": f, "matched": len(matched), "streets": len(ours), "deal_share": round(n_hit / n_all, 3)}

    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    size = sum(p.stat().st_size for p in OUT.glob("*.json")) / 1e6
    print(f"Done: maps for {len(index)} cities, {tot_m / max(tot_n, 1):.0%} of all deals are on a mapped street, {size:.1f} MB.")
    print("Next: python pipeline/build_city_pages.py   (adds the maps to the city pages)")


if __name__ == "__main__":
    main()
