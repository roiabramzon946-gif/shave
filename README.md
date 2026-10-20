# Shave? — Israeli apartment price checker

**Live site (Hebrew): https://roiabramzon946-gif.github.io/shave/**

*שווה?* tells a buyer whether an asking price for an apartment in Israel is fair. You enter the city, street, size, floor, building age and asking price; the site returns an estimated value, a likely price range, a verdict (below market → significantly overpriced), the city's price trend and comparable recent sales on the same street.

It is built on ~580,000 recorded apartment sales from the Israel Tax Authority, brought up to date with the Central Bureau of Statistics (CBS) dwelling price index.

## Results

Evaluated on 87,420 held-out sales the model never saw during training:

| Metric | Model | Baseline: city median price/m² |
|---|---|---|
| Median absolute error | **8.8%** | 13.6% |
| Estimates within 10% of the actual price | 55% | — |
| Estimates within 20% of the actual price | 81% | — |
| Share of sales inside the displayed 80% range | 80.0% | — |

## How it works

```
odata.org.il deals (CSV, 1.07M rows) ──► clean & filter ──► quarterly price index per city
                                                │
CBS dwelling price index (API) ─────────────────┤
                                                ▼
                     log(price per m²) = features + city effect + street effect
                                                │
                                                ▼
                        static JSON (per city) ──► static website (GitHub Pages)
```

1. **Cleaning.** Apartments only (no shops, plots or offices), plausible prices and sizes, Hebrew floor names parsed to numbers (`"שלישית"` → 3, `"א"` → 1), and robust outlier removal within each city and year (4 × MAD). 54% of raw rows survive.
2. **Time adjustment.** Every sale is moved to a common price level using a smoothed quarterly index per city (national trend for small cities).
3. **Model.** Ridge regression on log price per m² with:
   - floor, ground floor, top floor, high-rise, apartment size;
   - building age at the time of sale, in bands;
   - new-from-developer, split into *ready to move in* and *off-plan*. The source has no such flag, so it is derived from the building year relative to the sale year;
   - apartment type (garden, penthouse, duplex);
   - city and street location effects, fitted by backfitting. Streets with few sales are shrunk toward the city average so one unusual sale can't set a street's price.
4. **Price range.** The 80% range comes from the model's actual errors on held-out sales, and is wider for streets with fewer sales.
5. **Bringing prices up to date.** The open deals file ends in August 2024. Prices are moved forward to the latest month with the CBS dwelling price index of the city's district (Jerusalem, North, Haifa, Center, Tel Aviv, South; national index for Judea & Samaria).
6. **Serving.** The model is exported as coefficients plus per-city JSON files. The browser recomputes the estimate in JavaScript (`docs/estimator.js`). A parity test checks that the JavaScript and Python estimates match to within 1e-4.

## Findings along the way

- **The open file has no room counts.** The rooms column is empty in every row, so the estimate is driven by size in m², which is also how appraisers work. If a user leaves the size empty, the site uses a typical size for the number of rooms.
- **New apartments are not much more expensive than 5–10-year-old ones.** Compared with a building from 1995, a ready new apartment comes out about 13% higher and an off-plan one about 12% higher, while a building from 2018 comes out about 14% higher. A likely reason is that many developer sales in 2016–2022 were discounted government-lottery apartments (*מחיר למשתכן*), which the data does not flag.
- **Prices since the data ends (Sept 2024 → July 2026, CBS):** Jerusalem +6.9%, North +4.8%, Haifa +1.6%, South +1.4%, Tel Aviv −2.8%, Center −4.1%, national −0.6%.

## Limitations

- An estimate from past sales, not an appraisal. It does not see condition, view, parking, storage or legal status.
- Recent prices rely on a district-level index, not on individual recent sales.
- New-apartment estimates may be slightly low for the open market (see above).

## Run it yourself

Requires Python 3.11+ and Node.js (for the parity test only).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python pipeline/download_data.py      # ~240 MB of deals from odata.org.il
python pipeline/fetch_cbs_index.py    # CBS dwelling price index
python pipeline/build_model.py        # clean, evaluate, train, export to docs/data
python tests/parity_test.py           # JS estimate == Python estimate

python -m http.server 8000 --directory docs   # open http://localhost:8000
```

On Windows, `build_all.bat` runs the whole pipeline and `preview_site.bat` serves the site.

To refresh prices, re-run `fetch_cbs_index.py` and `build_model.py` when the CBS publishes a new index, then push.

## Project structure

```
pipeline/download_data.py    download the deals dataset
pipeline/fetch_cbs_index.py  download the CBS dwelling price index (national + districts)
pipeline/build_model.py      cleaning, price index, model, evaluation, export
pipeline/make_test_data.py   fake data in the same schema, for development
docs/                        the website (served by GitHub Pages)
docs/estimator.js            the estimate formula, mirrored from the Python model
tests/parity_test.py         checks the website and the model agree
```

## Data sources

- Real-estate deals: [מאגר עסקאות הנדל"ן](https://www.odata.org.il/dataset/nadlan) on odata.org.il, Israel Tax Authority data, CC BY.
- Dwelling price index: [Central Bureau of Statistics index API](https://www.cbs.gov.il/en/Pages/Api-Indices.aspx).

---

Built by [Roi Abramzon](https://github.com/roiabramzon946-gif), B.Sc. Statistics & Data Science, Tel Aviv University.
