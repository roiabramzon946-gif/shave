"""
Download the Israeli real-estate deals dataset (nadlan) from odata.org.il.

Source:  https://www.odata.org.il/dataset/nadlan
License: Creative Commons Attribution (credit the source on your site).

Usage:
    python pipeline/download_data.py

Files are saved to data/raw/. Already-downloaded files are skipped.
"""
from pathlib import Path
import sys

import requests

API = "https://www.odata.org.il/api/3/action/package_show"
DATASET_IDS = ["nadlan", "84f2bc2d-87a0-474e-a3ea-63d7bb9b5447"]
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def get_resources():
    last_error = None
    for ds in DATASET_IDS:
        try:
            r = requests.get(API, params={"id": ds}, timeout=60)
            r.raise_for_status()
            payload = r.json()
            if payload.get("success"):
                return payload["result"]["resources"]
        except Exception as e:  # try the next id
            last_error = e
    raise SystemExit(f"Could not read the dataset listing from odata.org.il: {last_error}")


def download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done / total:5.1%} of {total / 1e6:.0f} MB", end="")
    print()
    tmp.replace(dest)


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    resources = [r for r in get_resources()
                 if (r.get("format") or "").lower() == "csv" or r.get("url", "").lower().endswith(".csv")]
    if not resources:
        raise SystemExit("No CSV files found in the dataset listing.")

    print(f"Found {len(resources)} CSV file(s).")
    for i, res in enumerate(resources, 1):
        name = (res.get("name") or f"part_{i}").replace("/", "_").replace(" ", "_")
        if not name.lower().endswith(".csv"):
            name += ".csv"
        dest = RAW_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  {name}: already downloaded, skipping")
            continue
        print(f"  downloading {name} ...")
        download(res["url"], dest)

    print(f"Done. Files are in {RAW_DIR}")


if __name__ == "__main__":
    sys.exit(main())
