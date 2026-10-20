"""
Download the CBS (Central Bureau of Statistics) dwelling price index, national and by district,
so build_model.py can bring the older deal prices up to today's level.

Source: CBS open index API, https://api.cbs.gov.il/index/ (no key needed).

Usage:
    python pipeline/fetch_cbs_index.py

Output:
    data/cbs_dwelling_index.csv   columns: district, year, month, value
    data/cbs_raw/                 raw API answers (for checking)
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cbs_dwelling_index.csv"
RAW = ROOT / "data" / "cbs_raw"
API = "https://api.cbs.gov.il/index"
DISTRICTS = ["ירושלים", "צפון", "חיפה", "מרכז", "תל אביב", "דרום"]
S = requests.Session()
S.headers.update({"User-Agent": "apartment-price-checker"})


_n = 0


def xml_to_obj(el):
    """XML element -> dict (attributes + leaf children as keys, nested children in '_children')."""
    tag = lambda e: e.tag.split("}")[-1]
    d = {k.split("}")[-1]: v for k, v in el.attrib.items()}
    kids = []
    for c in el:
        if len(c) or c.attrib:
            kids.append({tag(c): xml_to_obj(c)} if not len(c) else xml_to_obj(c))
            if len(c) and tag(c) not in d:
                d[tag(c)] = kids[-1]
        else:
            d[tag(c)] = (c.text or "").strip()
    if kids:
        d["_children"] = kids
    return d


def get(path, **params):
    """GET from the CBS API; accepts JSON or XML answers and saves every raw answer."""
    global _n
    last_err = None
    for fmt in ("json", "xml"):
        p = {"format": fmt, "download": "false", "lang": "he", **params}
        r = S.get(f"{API}/{path}", params=p, timeout=60)
        _n += 1
        name = f"{_n:02d}_{path.replace('/', '_')}_{params.get('id', '')}_{fmt}.txt"
        (RAW / name).write_text(f"URL: {r.url}\nHTTP {r.status_code} {r.headers.get('content-type')}\n\n{r.text}",
                                encoding="utf-8")
        body = r.text.lstrip("\ufeff").strip()
        if r.status_code != 200 or not body:
            last_err = f"HTTP {r.status_code}, {len(body)} chars ({name})"
            continue
        if body[0] in "[{":
            return json.loads(body)
        if body[0] == "<" and not body[:200].lower().startswith(("<!doctype html", "<html")):
            import xml.etree.ElementTree as ET
            return xml_to_obj(ET.fromstring(body))
        last_err = f"HTTP {r.status_code}, not JSON/XML: {body[:150]!r} ({name})"
    raise ValueError(last_err)


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def code_and_name(d: dict):
    code = name = None
    for k, v in d.items():
        kl = k.lower()
        if code is None and re.fullmatch(r"(code|id|indexcode|index_code|codeindex)", kl) and re.fullmatch(r"\d{3,7}", str(v).strip()):
            code = str(v).strip()
        if name is None and isinstance(v, str) and re.search(r"name|title|desc", kl) and re.search(r"[א-ת]", v):
            name = v.strip()
    return code, name


def collect(data, found):
    for d in walk(data):
        code, name = code_and_name(d)
        if code and name and "דיר" in name:
            found.setdefault(code, name)


def find_series():
    """Find the dwelling price index series: browse chapter 'aa', then fall back to search."""
    found = {}
    try:
        chapter = get("catalog/chapter", id="aa")
        subjects = set()
        for d in walk(chapter):
            for k, v in d.items():
                if re.search(r"subject|topic|^id$|code", k.lower()) and re.fullmatch(r"\d{1,4}", str(v)):
                    subjects.add(str(v))
        print(f"  chapter 'aa': {len(subjects)} subjects")
        collect(chapter, found)
        for sid in sorted(subjects, key=int):
            try:
                collect(get("catalog/subject", id=sid), found)
            except Exception as e:
                print(f"  subject {sid} failed: {e}")
    except Exception as e:
        print(f"  chapter 'aa' failed: {e}")
    if found:
        return found
    for q in ["מחירי דירות", "מחירי הדירות", "דירות"]:
        try:
            data = get("catalog/tree", q=q)
        except Exception as e:
            print(f"  catalog search '{q}' failed: {e}")
            continue
        collect(data, found)
    return found


def pick_codes(found: dict):
    """national + one series per district; prefer names that say 'מדד מחירי דירות'."""
    chosen = {}
    items = sorted(found.items(), key=lambda kv: ("מדד" not in kv[1], "שכר" in kv[1], len(kv[1])))
    for code, name in items:
        if "שכר" in name or "שכירות" in name or "חדשות" in name:
            continue
        hit = [dname for dname in DISTRICTS if dname in name]
        key = hit[0] if len(hit) == 1 else ("ארצי" if not hit else None)
        if key and key not in chosen:
            chosen[key] = (code, name)
    return chosen


# Fixed CBS codes (catalog chapter 'aa': subject 45 = national, subject 166 = by district)
CODES = {"ארצי": "40010", "ירושלים": "60000", "צפון": "60100", "חיפה": "60200",
         "מרכז": "60300", "תל אביב": "60400", "דרום": "60500"}


def series_values(code: str):
    """{(year, month): index value}, chained through base changes using the period % change."""
    data = get("data/price", id=code, startPeriod="01-2022", endPeriod="12-2100", coef="false", pagesize=1000)
    pts = []
    for d in walk(data):
        y, m = d.get("year"), d.get("month")
        if y in (None, "") or m in (None, ""):
            continue
        base = d.get("currBase") if isinstance(d.get("currBase"), dict) else {}
        try:
            v = float(base.get("value", d.get("value")))
        except (TypeError, ValueError):
            continue
        pct = d.get("percent")
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            pct = None
        pts.append((int(y), int(m), v, base.get("baseDesc"), pct))
    pts = sorted(set(pts))
    if not pts:
        return {}
    bases = {p[3] for p in pts}
    if len(bases) <= 1:
        return {(y, m): v for y, m, v, _, _ in pts}
    # the base changed inside the window: rebuild one continuous series from the % changes
    print(f"    base changed ({len(bases)} bases), chaining by % change")
    out, level = {}, 100.0
    for i, (y, m, v, b, pct) in enumerate(pts):
        if i and pct is not None:
            level *= 1 + pct / 100
        out[(y, m)] = level
    return out


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    for key, code in CODES.items():
        try:
            vals = series_values(code)
        except Exception as e:
            print(f"  {key} ({code}): failed: {e}")
            continue
        if not vals:
            print(f"  {key} ({code}): no values found")
            continue
        (y0, m0), (y1, m1) = min(vals), max(vals)
        first, last = vals[min(vals)], vals[max(vals)]
        print(f"  {key:8s} {len(vals):3d} values, {m0:02d}/{y0} to {m1:02d}/{y1}, "
              f"change over the period {(last / first - 1) * 100:+.1f}%")
        rows += [{"district": key, "year": y, "month": m, "value": v} for (y, m), v in sorted(vals.items())]
    if not any(r["district"] == "ארצי" for r in rows):
        raise SystemExit("The national index could not be read. Run:  head -c 1500 data/cbs_raw/*data_price*40010*\n"
                         "and send Claude what it prints.")
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["district", "year", "month", "value"])
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} values to {OUT}")
    print("Next: python pipeline/build_model.py")


if __name__ == "__main__":
    sys.exit(main())
