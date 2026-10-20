"""
Clean the raw deals, train the price model, evaluate it and export the website data.

Usage:
    python pipeline/build_model.py          # uses the real files in data/raw/
    python pipeline/build_model.py --test   # uses only data/raw/TEST_*.csv (fake data)

Output:
    docs/data/meta.json        model coefficients, city list, accuracy numbers
    docs/data/cities/c<N>.json per-city street effects, price trend, recent deals
    data/report.txt            plain-text accuracy report

Model (log price per sqm):
    log(ppsqm) = intercept + features·coef + city effect + street effect
    - every deal is first adjusted to today's price level with a quarterly city price index
    - street effects are shrunk toward the city average when a street has few deals
    - the same formula is re-implemented in docs/estimator.js
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SITE_DATA = ROOT / "docs" / "data"

MIN_YEAR = 2012
STREET_SHRINK_K = 8          # pseudo-deals pulling a street toward its city
DEALS_PER_STREET = 60        # recent deals exported per street (for "similar deals")
RECENT_YEARS = 4             # how far back the exported deals go
MIN_CITY_DEALS = 30          # smaller cities are dropped from the site

# ----------------------------------------------------------------------------- parsing

FLOOR_UNITS = {"אחת": 1, "אחד": 1, "שתיים": 2, "שתים": 2, "שניים": 2, "שלוש": 3, "שלושה": 3,
               "ארבע": 4, "ארבעה": 4, "חמש": 5, "חמישה": 5, "שש": 6, "שישה": 6, "שבע": 7,
               "שבעה": 7, "שמונה": 8, "תשע": 9, "תשעה": 9}
FLOOR_ORDINALS = {"קרקע": 0, "מרתף": -1, "ראשונה": 1, "שניה": 2, "שנייה": 2, "שלישית": 3,
                  "רביעית": 4, "חמישית": 5, "שישית": 6, "שביעית": 7, "שמינית": 8,
                  "תשיעית": 9, "עשירית": 10}
FLOOR_TENS = {"עשרים": 20, "שלושים": 30, "ארבעים": 40, "חמישים": 50, "שישים": 60}
FLOOR_LETTERS = {"א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9, "י": 10,
                 "יא": 11, "יב": 12, "יג": 13, "יד": 14, "טו": 15, "טז": 16, "יז": 17, "יח": 18}


def parse_floor(val) -> float:
    """'שלישית' -> 3, 'אחת עשרה' -> 11, 'עשרים ושתיים' -> 22, '7' -> 7, 'קרקע' -> 0."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return np.nan
    s = str(val).strip()
    if not s:
        return np.nan
    m = re.search(r"-?\d+", s)
    if m:
        return float(m.group())
    s = s.replace("־", " ").replace("-", " ")
    bare = s.replace("'", "").replace("׳", "").strip()
    if bare in FLOOR_LETTERS:                     # 'א' -> 1, 'ב' -> 2
        return float(FLOOR_LETTERS[bare])
    first = s.split("מתוך")[0].strip()          # 'שלישית מתוך 5' -> 'שלישית'
    for word, num in FLOOR_ORDINALS.items():
        if first.startswith(word):
            return float(num)
    if "עשרה" in first:                          # 11-19
        for word, num in FLOOR_UNITS.items():
            if first.startswith(word):
                return float(10 + num)
    for word, num in FLOOR_TENS.items():          # 20, 21-29, ...
        if first.startswith(word):
            rest = first[len(word):].strip().lstrip("ו").strip()
            return float(num + FLOOR_UNITS.get(rest, 0))
    return np.nan


def to_num(series: pd.Series) -> pd.Series:
    cleaned = (series.astype("string")
               .str.replace(r"[,₪\s]", "", regex=True)
               .str.replace("ש\"ח", "", regex=False))
    # plain float64: blanks become NaN (not pd.NA), so comparisons on them are simply False
    return pd.to_numeric(cleaned, errors="coerce").astype("float64")


def norm_text(series: pd.Series) -> pd.Series:
    return (series.astype("string").fillna("")
            .str.replace("׳", "'", regex=False).str.replace("״", '"', regex=False)
            .str.replace(r"\s+", " ", regex=True).str.strip())


def norm_city(series: pd.Series) -> pd.Series:
    """'תל אביב -יפו' and 'תל אביב-יפו' -> the same name."""
    return norm_text(series).str.replace(r"\s*[-־]\s*", "-", regex=True)


def pick(df: pd.DataFrame, *names: str) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


# ----------------------------------------------------------------------------- load & clean

def load_raw(test: bool) -> pd.DataFrame:
    files = sorted(RAW_DIR.glob("*.csv"))
    files = [f for f in files if f.name.startswith("TEST_") == test]
    if not files:
        hint = "python pipeline/make_test_data.py" if test else "python pipeline/download_data.py"
        raise SystemExit(f"No CSV files in {RAW_DIR}. Run first: {hint}")
    frames = []
    for f in files:
        print(f"  reading {f.name}")
        part = pd.read_csv(f, dtype=str, encoding="utf-8-sig", low_memory=False)
        part.columns = [c.strip().lower() for c in part.columns]
        frames.append(part)
    return pd.concat(frames, ignore_index=True)


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    c = {
        "date": pick(raw, "dealdatetime", "dealdate"),
        "price": pick(raw, "dealamount"),
        "rooms": pick(raw, "assetroomno", "assetroomnum", "rooms"),
        "floor": pick(raw, "floorno", "floor"),
        "type": pick(raw, "dealnaturedescription"),
        "city": pick(raw, "city_name", "city"),
        "street": pick(raw, "street", "streetname"),
        "built": pick(raw, "buildingyear", "yearbuilt"),
        "bfloors": pick(raw, "buildingfloors"),
        "new": pick(raw, "newprojectext"),
        "address": pick(raw, "displayaddress", "fulladdress"),
    }
    missing = [k for k in ("date", "price", "city") if c[k] is None]
    if missing:
        raise SystemExit(f"Missing required columns {missing}. Columns found: {list(raw.columns)}")

    # Area: in this dataset the square metres sit in 'dealnature'. Verify before trusting it.
    area_col = None
    for cand in ("dealnature", "assetarea", "area", "surface", "sqm"):
        if cand in raw.columns:
            med = to_num(raw[cand]).median()
            if pd.notna(med) and 30 <= med <= 250:
                area_col = cand
                break
    if area_col is None:
        raise SystemExit("Could not find a square-metres column (checked dealnature/assetarea/area). "
                         f"Columns found: {list(raw.columns)}")
    print(f"  area column: {area_col}")

    df = pd.DataFrame({
        "price": to_num(raw[c["price"]]),
        "area": to_num(raw[area_col]),
        "rooms": to_num(raw[c["rooms"]]) if c["rooms"] else np.nan,
        "floor": raw[c["floor"]].map(parse_floor) if c["floor"] else np.nan,
        "type": norm_text(raw[c["type"]]) if c["type"] else "",
        "city": norm_city(raw[c["city"]]),
        "street": norm_text(raw[c["street"]]) if c["street"] else "",
        "built": to_num(raw[c["built"]]) if c["built"] else np.nan,
        "bfloors": to_num(raw[c["bfloors"]]) if c["bfloors"] else np.nan,
        "new": (norm_text(raw[c["new"]]) != "") if c["new"] else False,
        "address": norm_text(raw[c["address"]]) if c["address"] else "",
        "source": np.where(raw["type"].astype("string").fillna("") == "govmap", "govmap", "odata")
        if "type" in raw.columns else "odata",
    })
    d = pd.to_datetime(raw[c["date"]], errors="coerce", format="ISO8601")
    if d.isna().mean() > 0.5:
        d = pd.to_datetime(raw[c["date"]], errors="coerce", dayfirst=True, format="mixed")
    df["date"] = d
    n0 = len(df)

    # apartments only
    t = df["type"]
    is_apt = t.str.contains("דירה|דירת|דופלקס|פנטהאוז|טריפלקס", regex=True)
    not_apt = t.str.contains("מגרש|חנות|משרד|מחסן|חניה|קרקע חקלאית|מבנה תעשי", regex=True)
    if is_apt.sum() > 0:
        df = df[is_apt & ~not_apt]
    else:
        print("  warning: no apartment type labels found, keeping all property types")

    df = df[(df["date"].dt.year >= MIN_YEAR) & (df["date"] <= pd.Timestamp.now())]
    df = df[df["price"].between(150_000, 60_000_000) & df["area"].between(20, 500)]
    df = df[df["rooms"].isna() | df["rooms"].between(1, 12)]
    df = df[df["city"] != ""]
    df["built"] = df["built"].where(df["built"].between(1900, datetime.now().year + 4))
    df["bfloors"] = df["bfloors"].where(df["bfloors"].between(1, 80))
    df["floor"] = df["floor"].where(df["floor"].between(-2, 80))

    # the two sources overlap for a few months: keep one copy of each deal (prefer GovMap, it has rooms)
    df["_k"] = (df["city"] + "|" + df["street"] + "|" + df["date"].dt.strftime("%Y-%m-%d")
                + "|" + df["price"].round(-3).astype("Int64").astype(str) + "|" + df["area"].round().astype("Int64").astype(str))
    if df["source"].nunique() > 1:
        gm_keys = set(df.loc[df["source"] == "govmap", "_k"])
        drop = (df["source"] == "odata") & df["_k"].isin(gm_keys)
        df = df[~drop]
        if drop.sum():
            print(f"  removed {int(drop.sum()):,} deals that appear in both sources")
    df = df.drop(columns="_k")

    df["ppsqm"] = df["price"] / df["area"]
    df = df[df["ppsqm"].between(2_000, 200_000)]
    df["y"] = np.log(df["ppsqm"])

    # robust outlier removal inside each city-year
    df["year"] = df["date"].dt.year
    g = df.groupby(["city", "year"])["y"]
    med = g.transform("median")
    mad = g.transform(lambda s: (s - s.median()).abs().median()).replace(0, np.nan).fillna(0.2)
    df = df[(df["y"] - med).abs() <= 4 * 1.4826 * mad]

    city_n = df["city"].value_counts()
    df = df[df["city"].isin(city_n[city_n >= MIN_CITY_DEALS].index)]
    df["q"] = df["date"].dt.to_period("Q")
    print(f"  kept {len(df):,} of {n0:,} rows ({len(df) / max(n0, 1):.0%}) "
          f"in {df['city'].nunique()} cities")
    for src, g in df.groupby("source"):
        print(f"    {src}: {len(g):,} deals, {g['date'].min():%Y-%m} to {g['date'].max():%Y-%m}, "
              f"with rooms: {g['rooms'].notna().mean():.0%}")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------- time index

def build_index(df: pd.DataFrame):
    """Quarterly log price-per-sqm level per city (falls back to the national trend)."""
    quarters = pd.period_range(df["q"].min(), df["q"].max(), freq="Q")
    vol = df.groupby("q").size().reindex(quarters, fill_value=0)
    # latest usable quarter: enough deals, and not the newest one (still being reported)
    ok = vol[(vol >= 150) & (vol >= 0.15 * vol.median())]
    latest = ok.index.max()
    if latest == quarters.max() and len(ok) > 1 and vol[latest] < 0.6 * vol[ok.index[-2]]:
        latest = ok.index[-2]

    def smooth(s: pd.Series) -> pd.Series:
        s = s.reindex(quarters).interpolate(limit_direction="both")
        return s.rolling(3, center=True, min_periods=1).mean()

    national = smooth(df.groupby("q")["y"].median())
    idx = {}
    for city, grp in df.groupby("city"):
        per_q = grp.groupby("q")["y"].agg(["median", "size"])
        if len(grp) >= 400 and per_q["size"].median() >= 8:
            idx[city] = smooth(per_q["median"])
        else:  # follow national shape, shifted to the city's level
            shift = (grp["y"] - national.reindex(grp["q"]).values).median()
            idx[city] = national + shift
    return idx, latest, quarters


def adjust_to_latest(df, idx, latest):
    offs = np.empty(len(df))
    for city, rows in df.groupby("city").groups.items():
        s = idx[city]
        offs[df.index.get_indexer(rows)] = s[latest] - s.reindex(df.loc[rows, "q"]).values
    return offs


# ----------------------------------------------------------------------------- features
# KEEP IN SYNC with featureVector() in docs/estimator.js

FEATURES = ["floor", "floor_missing", "ground", "top", "log_area", "rooms_dev",
            "age_new", "age_3_10", "age_26_45", "age_46p", "age_missing",
            "new_ready", "presale", "highrise", "garden", "penthouse", "duplex"]


def features(df: pd.DataFrame, ref_year) -> np.ndarray:
    """ref_year: the year of each deal when training (age and newness at the time of sale),
    or a single year when estimating today's value."""
    num = lambda col: pd.to_numeric(df[col], errors="coerce").astype("float64")
    ref = pd.Series(np.broadcast_to(np.asarray(ref_year, dtype=float), (len(df),)), index=df.index)
    floor = num("floor")
    # status: "" (work it out from the building year), "ready" (new, ready to move in), "presale" (off-plan)
    new_flag = df["new"].astype(bool)
    status = df["status"].astype("string").fillna("") if "status" in df.columns else pd.Series("", index=df.index)
    status = status.where(status != "", np.where(new_flag, "ready", ""))
    built = num("built")
    built = built.where(~((status == "ready") & built.isna()), ref)
    built = built.where(~((status == "presale") & built.isna()), ref + 2)
    age = (ref - built).clip(0, 80)
    # off-plan: the building was finished after the year of the sale
    presale = (status == "presale") | ((status == "") & (built > ref))
    # new and ready: bought in the year the building was finished or the year after
    new_ready = (status == "ready") | ((status == "") & (built >= ref - 1) & (built <= ref))
    bf = num("bfloors")
    area = num("area")
    rooms = num("rooms").fillna(area / 27)
    t = df["type"].astype("string").fillna("")
    cols = {
        "floor": floor.fillna(2).clip(0, 30),
        "floor_missing": floor.isna(),
        "ground": floor == 0,
        "top": floor.notna() & bf.notna() & (floor >= bf) & (bf >= 3),
        "log_area": np.log(area / 90),
        "rooms_dev": rooms - area / 27,
        "age_new": age <= 2,
        "age_3_10": age.between(3, 10),
        "age_26_45": age.between(26, 45),
        "age_46p": age >= 46,
        "age_missing": age.isna(),
        "new_ready": new_ready,
        "presale": presale,
        "highrise": bf >= 9,
        "garden": t.str.contains("גן"),
        "penthouse": t.str.contains("גג|פנטהאוז", regex=True),
        "duplex": t.str.contains("דופלקס"),
    }
    X = np.column_stack([np.asarray(pd.Series(cols[f]).fillna(False) if f not in ("floor", "log_area", "rooms_dev")
                                    else cols[f], dtype=float) for f in FEATURES])
    bad = [f for f, ok in zip(FEATURES, np.isfinite(X).all(axis=0)) if not ok]
    if bad:
        raise SystemExit(f"Feature columns still contain missing values: {bad}")
    return X


# ----------------------------------------------------------------------------- model

def fit(df: pd.DataFrame, y_adj: np.ndarray, X: np.ndarray, rounds: int = 5):
    loc = np.zeros(len(df))
    city = df["city"].values
    street = (df["city"] + "|" + df["street"]).values
    for _ in range(rounds):
        reg = Ridge(alpha=1.0).fit(X, y_adj - loc)
        resid = y_adj - reg.predict(X)
        city_eff = pd.Series(resid).groupby(city).mean()
        r2 = resid - city_eff.reindex(city).values
        sg = pd.Series(r2).groupby(street)
        street_eff = sg.sum() / (sg.size() + STREET_SHRINK_K)
        street_eff[[k.endswith("|") for k in street_eff.index]] = 0.0  # unknown street name
        loc = city_eff.reindex(city).values + street_eff.reindex(street).values
    street_n = pd.Series(street).value_counts()
    return reg, city_eff, street_eff, street_n


def predict(df, X, reg, city_eff, street_eff):
    street = (df["city"] + "|" + df["street"]).values
    return (reg.predict(X) + city_eff.reindex(df["city"].values).fillna(0).values
            + street_eff.reindex(street).fillna(0).values)


def n_bucket(n):
    return np.select([n < 5, n < 20], ["few", "some"], "many")


def evaluate(df, idx, latest):
    rng = np.random.default_rng(0)
    test = rng.random(len(df)) < 0.15
    tr, te = df[~test], df[test]
    off_tr = adjust_to_latest(tr, idx, latest)
    ref_year = latest.year
    reg, ce, se, sn = fit(tr, tr["y"].values + off_tr, features(tr, tr["date"].dt.year))
    # predict at the deal's own quarter, then compare to the actual price
    pred_adj = predict(te, features(te, te["date"].dt.year), reg, ce, se)
    pred = pred_adj - adjust_to_latest(te, idx, latest)
    err = te["y"].values - pred                                  # log error
    ape = np.abs(np.exp(err) - 1)
    n_street = sn.reindex((te["city"] + "|" + te["street"]).values).fillna(0).values
    buckets = n_bucket(n_street)
    quant = {}
    for b in ("few", "some", "many"):
        e = err[buckets == b]
        if len(e) < 50:
            e = err
        quant[b] = {"p10": float(np.quantile(e, .10)), "p90": float(np.quantile(e, .90))}
    lo = np.array([quant[b]["p10"] for b in buckets])
    hi = np.array([quant[b]["p90"] for b in buckets])
    # naive baseline: city median price per sqm in the same quarter
    city_q_med = tr.groupby(["city", "q"])["y"].median()
    base = city_q_med.reindex(pd.MultiIndex.from_arrays([te["city"], te["q"]])).values
    base_ape = np.abs(np.exp(te["y"].values - base) - 1)
    metrics = {
        "test_deals": int(len(te)),
        "median_abs_pct_error": float(np.median(ape)),
        "share_within_10pct": float(np.mean(ape <= 0.10)),
        "share_within_20pct": float(np.mean(ape <= 0.20)),
        "interval_80_coverage": float(np.mean((err >= lo) & (err <= hi))),
        "baseline_city_median_abs_pct_error": float(np.nanmedian(base_ape)),
    }
    return metrics, quant



# ----------------------------------------------------------------------------- CBS roll-forward
# CBS dwelling-price districts. Judea & Samaria settlements are not in the district index -> national.
CITY_DISTRICT = {}
for _d, _cities in {
    "תל אביב": "תל אביב-יפו|בת ים|חולון|רמת גן|בני ברק|גבעתיים|קריית אונו|אור יהודה|רמת השרון|הרצלייה|אזור",
    "ירושלים": "ירושלים|בית שמש|מבשרת ציון|מוצא עילית|צור הדסה",
    "חיפה": "חיפה|קריית אתא|חדרה|קריית ים|קריית ביאליק|נשר|אור עקיבא|חריש|טירת כרמל|פרדס חנה-כרכור|"
            "קריית מוצקין|זכרון יעקב|קרית טבעון|בנימינה-גבעת עדה|עתלית|קיסריה|קציר|נופית",
    "צפון": "נהרייה|עפולה|טבריה|עכו|מגדל העמק|צפת|קריית שמונה|יקנעם עילית|מעלות-תרשיחא|בית שאן|כרמיאל|"
            "שלומי|חצור הגלילית|רמת ישי|קצרין|כפר ורדים|נצרת|ראש פינה|מנחמיה|מטולה|שמשית|אחוזת ברק|גן נר",
    "מרכז": "פתח תקווה|ראשון לציון|נתניה|ראש העין|כפר סבא|רחובות|רעננה|לוד|נס ציונה|מודיעין-מכבים-רעות|"
            "הוד השרון|יבנה|שוהם|רמלה|כפר יונה|גני תקווה|יהוד-מונוסון|גדרה|גבעת שמואל|מזכרת בתיה|קדימה-צורן|"
            "צור יצחק|בני עי\"ש|גן יבנה|קריית עקרון|אבן יהודה|באר יעקב|בית דגן|נחלים|בת חפר|כוכב יאיר|פרדסייה|"
            "צור משה|מתן|כפר ברא",
    "דרום": "אשדוד|באר שבע|אשקלון|אילת|קריית גת|שדרות|ערד|דימונה|קריית מלאכי|אופקים|נתיבות|ירוחם|מצפה רמון|"
            "מיתר|עומר|להבים",
}.items():
    for _c in _cities.split("|"):
        CITY_DISTRICT[_c] = _d


def cbs_rollforward(df, latest):
    """Factor per city from the data's last quarter to the newest CBS dwelling-price index value."""
    path = ROOT / "data" / "cbs_dwelling_index.csv"
    if not path.exists():
        return None
    cbs = pd.read_csv(path, encoding="utf-8-sig")
    cbs["period"] = pd.PeriodIndex.from_fields(year=cbs["year"], month=cbs["month"], freq="M")
    base_m = latest.asfreq("M", how="end")
    out = {"cities": {}, "districts": {}}
    for dist, g in cbs.groupby("district"):
        g = g.sort_values("period")
        before = g[g["period"] <= base_m]
        if before.empty or len(g) < 2:
            continue
        b, last = before.iloc[-1], g.iloc[-1]
        # quarterly points after the base, for the price chart and for "in today's prices"
        after = g[g["period"] > b["period"]].copy()
        after["q"] = after["period"].dt.asfreq("Q")
        steps = [(str(q), float(v / b["value"])) for q, v in after.groupby("q")["value"].last().items()]
        out["districts"][dist] = {"factor": float(last["value"] / b["value"]), "from": str(b["period"]),
                                  "to": str(last["period"]), "steps": steps}
    if "ארצי" not in out["districts"]:
        print("  warning: no national series in cbs_dwelling_index.csv, skipping the CBS update")
        return None
    for city in df["city"].unique():
        d = CITY_DISTRICT.get(city)
        out["cities"][city] = d if d in out["districts"] else "ארצי"
    out["to"] = out["districts"]["ארצי"]["to"]
    return out


# ----------------------------------------------------------------------------- export

def trend_end(latest, roll):
    return pd.Period(roll["to"], freq="M").asfreq("Q") if roll else latest


def typical_area(df):
    """Median sqm by number of rooms (used when the visitor leaves the area empty)."""
    r = df.dropna(subset=["rooms"])
    r = r[r["rooms"].between(1, 8)]
    if len(r) < 500:  # no room data: a rough national rule of thumb
        return {str(k): v for k, v in {1.5: 42, 2: 52, 2.5: 62, 3: 74, 3.5: 85, 4: 98, 4.5: 110,
                                       5: 124, 5.5: 138, 6: 152, 7: 175}.items()}
    med = r.groupby((r["rooms"] * 2).round() / 2)["area"].median()
    return {f"{k:g}": int(round(v)) for k, v in med.items() if (r["rooms"] == k).sum() >= 50}


def export(df, idx, latest, reg, city_eff, street_eff, street_n, metrics, quant, test, roll=None):
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "cities").mkdir(exist_ok=True)
    for old in (SITE_DATA / "cities").glob("*.json"):
        old.unlink()

    cutoff = latest.end_time - pd.DateOffset(years=RECENT_YEARS)
    cities = []
    for i, (city, grp) in enumerate(sorted(df.groupby("city"), key=lambda kv: -len(kv[1]))):
        cid = f"c{i}"
        s = idx[city]
        trend = [[str(q), round(float(np.exp(s[q])))] for q in s.index if q.year >= latest.year - 8 and q <= latest]
        eff = float(city_eff.get(city, 0.0))
        if roll:
            dist = roll["districts"][roll["cities"][city]]
            eff += float(np.log(dist["factor"]))
            base = float(np.exp(s[latest]))
            trend += [[q, round(base * f)] for q, f in dist["steps"] if q > str(latest)]
        streets = {}
        for st, sg in grp.groupby("street"):
            if not st:
                continue
            key = f"{city}|{st}"
            recent = sg[sg["date"] >= cutoff]
            streets[st] = [round(float(street_eff.get(key, 0.0)), 4), int(street_n.get(key, 0)),
                           int(recent["ppsqm"].median()) if len(recent) else None]
        recent = grp[grp["date"] >= cutoff].sort_values("date", ascending=False)
        recent = recent.groupby("street", group_keys=False).head(DEALS_PER_STREET)
        deals = [[r.street, r.date.strftime("%Y-%m-%d"), None if pd.isna(r.rooms) else float(r.rooms),
                  None if pd.isna(r.floor) else int(r.floor), int(r.area), int(r.price), r.address]
                 for r in recent.itertuples()]
        payload = {"name": city, "eff": round(eff, 4),
                   "trend": trend, "streets": streets,
                   "deals_cols": ["street", "date", "rooms", "floor", "area", "price", "address"],
                   "deals": deals}
        (SITE_DATA / "cities" / f"{cid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        cities.append({"id": cid, "name": city, "n": int(len(grp))})

    meta = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "version": datetime.now().strftime("%Y%m%d%H%M%S"),   # cache-busting for the site files
        "test_data": test,
        "data_through": latest.end_time.strftime("%Y-%m-%d"),
        "latest_quarter": str(trend_end(latest, roll)),
        "deals_through_quarter": str(latest),
        "cbs_update": None if not roll else {d: round(v["factor"], 4) for d, v in roll["districts"].items()},
        "ref_year": int(trend_end(latest, roll).year),
        "n_deals": int(len(df)),
        "features": FEATURES,
        "coef": [round(float(c), 6) for c in reg.coef_],
        "intercept": round(float(reg.intercept_), 6),
        "interval": quant,
        "metrics": metrics,
        "typical_area": typical_area(df),
        "cities": cities,
        "source": ("מאגר עסקאות הנדל\"ן (odata.org.il), CC-BY" +
                   (" ועסקאות עדכניות מ-GovMap" if (df["source"] == "govmap").any() else "")),
    }
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    size = sum(f.stat().st_size for f in SITE_DATA.rglob("*.json")) / 1e6
    print(f"  exported {len(cities)} cities, {size:.1f} MB to {SITE_DATA}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="use the fake TEST_ data")
    args = ap.parse_args()

    print("1/4 loading")
    df = clean(load_raw(args.test))
    print("2/4 building price index")
    idx, latest, _ = build_index(df)
    print(f"  prices adjusted to {latest}")
    print("3/4 evaluating on held-out deals")
    metrics, quant = evaluate(df, idx, latest)
    for k, v in metrics.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")
    print("4/4 training on all deals and exporting")
    off = adjust_to_latest(df, idx, latest)
    reg, ce, se, sn = fit(df, df["y"].values + off, features(df, df["date"].dt.year))
    roll = cbs_rollforward(df, latest)
    if roll:
        print(f"  CBS index: prices moved forward from {latest} to {roll['to']}:")
        for d, v in roll["districts"].items():
            print(f"    {d:8s} {(v['factor'] - 1) * 100:+.1f}%")
    else:
        print("  no CBS index file (data/cbs_dwelling_index.csv): prices stay at", latest)
    export(df, idx, latest, reg, ce, se, sn, metrics, quant, args.test, roll)

    report = ["Apartment price model report", f"generated {datetime.now():%Y-%m-%d %H:%M}",
              f"fake test data: {args.test}", f"deals used: {len(df):,}", f"price level: {latest}", ""]
    report += [f"{k}: {v:.3f}" if isinstance(v, float) else f"{k}: {v}" for k, v in metrics.items()]
    if roll:
        report += ["", f"CBS roll-forward to {roll['to']}:"]
        report += [f"  {d}: {(v['factor'] - 1) * 100:+.1f}%" for d, v in roll["districts"].items()]
    report += ["", "coefficients (effect on log price per sqm):"]
    report += [f"  {f:14s} {c:+.4f}" for f, c in zip(FEATURES, reg.coef_)]
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "report.txt").write_text("\n".join(report), encoding="utf-8")
    import build_city_pages
    build_city_pages.main()
    print("Done.")


if __name__ == "__main__":
    main()
