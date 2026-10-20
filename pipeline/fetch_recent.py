"""
NOT IN USE (Oct 2026): GovMap now requires a reCAPTCHA token, so automated downloads are
not allowed. Recent prices come from the CBS index instead (fetch_cbs_index.py).
Kept for reference only.

Fetch recent deals (after the odata.org.il file ends) from GovMap's open real-estate API
and save them in the same column layout as the odata CSV, so build_model.py reads them as-is.

GovMap also returns the number of rooms, which the odata file does not have.

Usage:
    python pipeline/fetch_recent.py --probe          # one test request, prints what came back
    python pipeline/fetch_recent.py                  # full run (resumable, ~20-40 minutes)
    python pipeline/fetch_recent.py --max-requests 200   # short run for a quick look

Output:
    data/raw/govmap_recent.csv
    data/govmap_cache/            progress + raw responses (lets you stop and resume)

Needs to run from a normal Israeli internet connection (GovMap blocks some foreign/cloud IPs).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

import os

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "govmap_cache"
OUT = RAW_DIR / "govmap_recent.csv"
BASE = os.environ.get("GOVMAP_BASE", "https://www.govmap.gov.il/api/real-estate")
HEADERS = {"User-Agent": "Mozilla/5.0 (apartment-price-checker; contact via GitHub)",
           "Accept": "application/json"}
PAGE = 1000
OUT_COLS = ["dealdate", "dealdatetime", "fulladdress", "displayaddress", "gush",
            "dealnaturedescription", "assetroomno", "floorno", "dealnature", "dealamount",
            "newprojectext", "projectname", "buildingyear", "yearbuilt", "buildingfloors",
            "keyvalue", "type", "polygon_id", "city_name", "street", "row_num"]


# ----------------------------------------------------------------------------- helpers

def first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", "null"):
            return v
    return None


def blocks_from_odata(since_year: str = "2019", test: bool = False):
    """One representative polygon per block (gush), busiest blocks first."""
    poly_n, gush_best = Counter(), {}
    files = [f for f in sorted(RAW_DIR.glob("*.csv"))
             if f.name.startswith("TEST_") == test and "govmap" not in f.name]
    if not files:
        raise SystemExit("No odata CSV files in data/raw. Run pipeline/download_data.py first.")
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                pid = (r.get("polygon_id") or "").strip()
                if pid and (r.get("dealdatetime") or "")[:4] >= since_year:
                    poly_n[pid] += 1
    gush_n = Counter()
    for pid, n in poly_n.items():
        g = pid.split("-")[0]
        gush_n[g] += n
        if g not in gush_best or n > poly_n[gush_best[g]]:
            gush_best[g] = pid
    return [(g, gush_best[g]) for g, _ in gush_n.most_common()]


class Client:
    def __init__(self, delay: float):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.delay = delay
        self.last = 0.0
        self.deal_type_ok = True   # GovMap removed dealType in mid-2026 on some endpoints

    def get(self, url, params):
        for attempt in range(4):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.s.get(url, params=params, timeout=(10, 45))
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt * 5)
                continue
            return r
        return r

    def neighborhood_deals(self, polygon_id, start, end):
        """All deals in the polygon's neighbourhood between start and end (YYYY-MM)."""
        url = f"{BASE}/neighborhood-deals/{polygon_id}"
        deal_types = [None] if not self.deal_type_ok else [1, 2]
        out, seen = [], set()
        stats = {}
        for dt in deal_types:
            offset = 0
            stats[dt] = [0, 0]  # rows received, new rows
            while True:
                params = {"limit": PAGE, "startDate": start, "endDate": end}
                if dt is not None:
                    params["dealType"] = dt
                if offset:
                    params["offset"] = offset
                r = self.get(url, params)
                if r.status_code in (400, 422) and dt is not None:
                    # dealType no longer accepted: switch to plain requests for the rest of the run
                    self.deal_type_ok = False
                    return self.neighborhood_deals(polygon_id, start, end)
                if r.status_code == 404:
                    return out
                r.raise_for_status()
                data = r.json()
                rows = data.get("data", []) if isinstance(data, dict) else data
                total = data.get("totalCount") if isinstance(data, dict) else None
                new = 0
                for d in rows or []:
                    key = first(d, "objectid", "objectId", "id") or json.dumps(d, sort_keys=True)
                    if key in seen:
                        continue
                    seen.add(key)
                    d["_dealType"] = dt
                    out.append(d)
                    new += 1
                stats[dt][0] += len(rows or [])
                stats[dt][1] += new
                offset += PAGE
                # stop when the page was short, nothing new arrived (offset ignored), or we have it all
                if not rows or len(rows) < PAGE or new == 0 or (total is not None and offset >= total):
                    break
        # if the second-hand request only repeated the first-hand rows, dealType was ignored
        if 2 in stats and stats[2][0] > 0 and stats[2][1] == 0:
            for d in out:
                d["_dealType"] = None
        return out


def to_row(d: dict, i: int):
    date = str(first(d, "dealDate", "deal_date", "dealdate") or "")[:10]
    price = first(d, "dealAmount", "deal_amount", "dealamount")
    area = first(d, "assetArea", "asset_area", "area")
    if not date or price is None or area is None:
        return None
    street = first(d, "streetName", "streetNameHeb", "street") or ""
    house = first(d, "houseNumber", "houseNum") or ""
    floor = first(d, "floorNumber", "floor_number")
    if floor is None:
        floor = first(d, "floor") or ""
    new = d.get("_dealType") == 1 or str(first(d, "dealTypeDescription", "dealType") or "") in ("1", "יד ראשונה")
    pid = first(d, "sourcePolygonId", "polygon_id", "polygonId") or ""
    return {
        "dealdate": date, "dealdatetime": date + "T00:00:00",
        "fulladdress": "", "displayaddress": f"{street} {house}".strip(), "gush": pid,
        "dealnaturedescription": first(d, "propertyTypeDescription", "assetTypeDescription") or "",
        "assetroomno": first(d, "assetRoomNum", "rooms", "roomNum") or "",
        "floorno": floor, "dealnature": area, "dealamount": price,
        "newprojectext": "חדש" if new else "", "projectname": "",
        "buildingyear": first(d, "yearBuilt", "buildingYear", "constructionYear") or "",
        "yearbuilt": "", "buildingfloors": first(d, "buildingFloors", "floorsNum", "numberOfFloors") or "",
        "keyvalue": first(d, "objectid", "objectId") or "", "type": "govmap",
        "polygon_id": pid, "city_name": first(d, "settlementNameHeb", "cityName", "settlementName") or "",
        "street": street, "row_num": i,
    }


def write_csv(deals):
    rows, keys = [], set()
    for d in deals:
        row = to_row(d, len(rows) + 1)
        if not row:
            continue
        k = (row["keyvalue"] or None, row["dealdate"], row["dealamount"], row["polygon_id"], row["dealnature"])
        if k in keys:
            continue
        keys.add(k)
        rows.append(row)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLS)
        w.writeheader()
        w.writerows(rows)
    return rows


# ----------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-04", help="first month to fetch (YYYY-MM)")
    ap.add_argument("--end", default=time.strftime("%Y-%m"), help="last month (YYYY-MM)")
    ap.add_argument("--delay", type=float, default=0.35, help="seconds between requests")
    ap.add_argument("--max-requests", type=int, default=0, help="stop after N blocks (0 = all)")
    ap.add_argument("--probe", action="store_true", help="one request, print the raw answer")
    ap.add_argument("--test", action="store_true", help="use the fake TEST_ data (for development)")
    args = ap.parse_args()
    global OUT, CACHE
    if args.test:
        OUT = RAW_DIR / "TEST_govmap_recent.csv"
        CACHE = ROOT / "data" / "govmap_cache_test"

    CACHE.mkdir(parents=True, exist_ok=True)
    blocks = blocks_from_odata(test=args.test)
    client = Client(args.delay)

    if args.probe:
        g, pid = blocks[0]
        print(f"Testing GovMap with block {g} (polygon {pid}) ...")
        r = client.get(f"{BASE}/neighborhood-deals/{pid}",
                       {"limit": 5, "startDate": args.start, "endDate": args.end})
        print("HTTP", r.status_code)
        print(r.text[:3000])
        try:
            data = r.json()
            rows = data.get("data", []) if isinstance(data, dict) else data
            if rows:
                print("\nParsed first deal as:")
                print(json.dumps(to_row(rows[0], 1), ensure_ascii=False, indent=1))
        except ValueError:
            print("Answer is not JSON.")
        return

    done_file = CACHE / "done_blocks.txt"
    deals_file = CACHE / "deals.jsonl"
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    covered = set(done)
    deals = []
    if deals_file.exists():
        with open(deals_file, encoding="utf-8") as fh:
            deals = [json.loads(line) for line in fh if line.strip()]
        for d in deals:
            pid = str(first(d, "sourcePolygonId", "polygon_id") or "")
            if pid:
                covered.add(pid.split("-")[0])

    todo = [(g, pid) for g, pid in blocks if g not in covered]
    print(f"{len(blocks)} blocks in total, {len(blocks) - len(todo)} already covered, {len(todo)} to go.")
    print(f"Fetching deals from {args.start} to {args.end}. Ctrl+C stops safely; run again to resume.")
    t0, n_req, errors = time.time(), 0, 0
    try:
        with open(deals_file, "a", encoding="utf-8") as dfh, open(done_file, "a") as gfh:
            for g, pid in todo:
                if g in covered:
                    continue
                if args.max_requests and n_req >= args.max_requests:
                    break
                n_req += 1
                try:
                    got = client.neighborhood_deals(pid, args.start, args.end)
                except Exception as e:
                    errors += 1
                    print(f"\n  block {g}: {e}")
                    if errors >= 15 and errors > n_req * 0.5:
                        raise SystemExit("Too many failed requests. Send this output to Claude.")
                    continue
                for d in got:
                    dfh.write(json.dumps(d, ensure_ascii=False) + "\n")
                    p = str(first(d, "sourcePolygonId", "polygon_id") or "")
                    if p:
                        covered.add(p.split("-")[0])
                deals.extend(got)
                covered.add(g)
                gfh.write(g + "\n")
                if n_req % 10 == 0:
                    dfh.flush(); gfh.flush()
                    left = sum(1 for gg, _ in todo if gg not in covered)
                    rate = n_req / (time.time() - t0)
                    print(f"\r  {n_req} requests, {len(deals):,} deals, ~{left} blocks left "
                          f"(~{left / max(rate, 0.01) / 60:.0f} min)   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\nStopped. Progress is saved; run the same command again to continue.")

    rows = write_csv(deals)
    months = Counter(r["dealdate"][:7] for r in rows)
    with_rooms = sum(1 for r in rows if str(r["assetroomno"]).strip())
    print(f"\nSaved {len(rows):,} deals to {OUT}")
    print(f"  with room count: {with_rooms:,}")
    if months:
        print(f"  months: {min(months)} to {max(months)}")
        print("  deals per month (last 6):", ", ".join(f"{m}: {months[m]}" for m in sorted(months)[-6:]))
    print("Next: python pipeline/build_model.py")


if __name__ == "__main__":
    sys.exit(main())
