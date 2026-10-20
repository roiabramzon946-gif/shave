"""
Generate FAKE deals in the same column layout as the odata.org.il nadlan CSV.

Used only to test the pipeline and website before the real data is downloaded.
Never publish a site built from this file: the prices are invented.

Usage:
    python pipeline/make_test_data.py            # writes data/raw/TEST_fake_deals.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
rng = np.random.default_rng(7)

CITIES = {  # name: base price per sqm (ILS) in 2015, yearly growth
    "תל אביב -יפו": (38000, 0.055), "ירושלים": (24000, 0.05), "חיפה": (13000, 0.04),
    "ראשון לציון": (19000, 0.05), "פתח תקווה": (18000, 0.05), "אשדוד": (14000, 0.045),
    "נתניה": (17000, 0.05), "באר שבע": (9000, 0.04), "חולון": (19000, 0.05),
    "רמת גן": (24000, 0.055), "הרצליה": (28000, 0.05), "כפר סבא": (22000, 0.045),
    "רעננה": (24000, 0.045), "מודיעין-מכבים-רעות": (17000, 0.045), "צופית": (21000, 0.04),
}
STREETS = ["הרצל", "ויצמן", "בן גוריון", "ז'בוטינסקי", "רוטשילד", "סוקולוב", "אחד העם",
           "ביאליק", "הנביאים", "העצמאות", "הגפן", "התאנה", "הדקל", "האלון", "השקד",
           "רבי עקיבא", "יהודה הלוי", "אלנבי", "דיזנגוף", "המייסדים"]
FLOOR_WORDS = ["קרקע", "ראשונה", "שניה", "שלישית", "רביעית", "חמישית", "שישית", "שביעית",
               "שמינית", "תשיעית", "עשירית", "אחת עשרה", "שתים עשרה", "שלוש עשרה",
               "ארבע עשרה", "חמש עשרה"]
TYPES = ["דירה בבית קומות"] * 8 + ["דירת גן", "דירת גג (פנטהאוז)", "קוטג' חד משפחתי", "מגרש"]


def main(n=200_000):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    city = rng.choice(list(CITIES), n, p=None)
    street = rng.choice(STREETS, n)
    street_eff = {(c, s): rng.normal(0, 0.15) for c in CITIES for s in STREETS}

    days = rng.integers(0, (pd.Timestamp("2026-06-30") - pd.Timestamp("2015-01-01")).days, n)
    date = pd.Timestamp("2015-01-01") + pd.to_timedelta(days, unit="D")
    years = days / 365.25

    rooms = rng.choice([1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 6], n, p=[.03, .08, .07, .18, .14, .22, .12, .12, .04])
    area = np.clip(rooms * rng.normal(27, 4, n), 30, 300).round()
    bfloors = rng.integers(2, 16, n)
    floor = np.minimum(rng.integers(0, 16, n), bfloors)
    built = rng.integers(1950, 2027, n)
    new = (date.year - built <= 1) & (rng.random(n) < 0.7)

    base = np.array([CITIES[c][0] for c in city])
    growth = np.array([CITIES[c][1] for c in city])
    log_ppsqm = (np.log(base) + growth * years
                 + np.array([street_eff[(c, s)] for c, s in zip(city, street)])
                 + 0.008 * floor - 0.12 * (floor == 0) - 0.12 * np.log(area / 90)
                 + 0.08 * new - 0.0015 * np.clip(date.year - built, 0, 70)
                 + rng.normal(0, 0.12, n))
    price = (np.exp(log_ppsqm) * area / 1000).round() * 1000

    df = pd.DataFrame({
        "dealdate": date.strftime("%d/%m/%Y"),
        "dealdatetime": date.strftime("%Y-%m-%dT00:00:00"),
        "fulladdress": [f"{s} {rng.integers(1, 120)}, {c}" for s, c in zip(street, city)],
        "displayaddress": [f"{s} {rng.integers(1, 120)}" for s in street],
        "gush": [f"{rng.integers(6000, 7000)}-{rng.integers(1, 300)}-{rng.integers(0, 40)}" for _ in range(n)],
        "dealnaturedescription": rng.choice(TYPES, n),
        "assetroomno": rooms,
        "floorno": [FLOOR_WORDS[f] if f < len(FLOOR_WORDS) else str(f) for f in floor],
        "dealnature": area,
        "dealamount": [f"{int(p):,}" for p in price],
        "newprojectext": np.where(new, "פרויקט חדש", ""),
        "projectname": "",
        "buildingyear": built,
        "yearbuilt": built,
        "buildingfloors": bfloors,
        "keyvalue": rng.integers(1e8, 9e8, n),
        "type": "דירה",
        "polygon_id": rng.integers(1000, 1100, n),
        "city_name": city,
        "street": street,
        "row_num": np.arange(1, n + 1),
    })
    # blanks like the real file has
    for col, share in [("buildingyear", .3), ("yearbuilt", .3), ("buildingfloors", .4), ("floorno", .05), ("assetroomno", .03)]:
        df[col] = df[col].astype(str)
        df.loc[rng.random(n) < share, col] = ""
    # sprinkle realistic junk
    junk = rng.random(n) < 0.01
    df.loc[junk, "dealamount"] = rng.choice(["1", "0", "", "50,000"], junk.sum())
    out = RAW_DIR / "TEST_fake_deals.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(df):,} FAKE rows to {out}")


if __name__ == "__main__":
    main()
