"""
Checks that the website's estimator.js gives the same prices as the Python model.

Usage (after build_model.py):
    python tests/parity_test.py
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from build_model import features  # noqa: E402

meta = json.loads((ROOT / "docs/data/meta.json").read_text(encoding="utf-8"))
rng = np.random.default_rng(1)
cases = []
for c in meta["cities"][:6]:
    city = json.loads((ROOT / f"docs/data/cities/{c['id']}.json").read_text(encoding="utf-8"))
    streets = list(city["streets"]) + ["רחוב שלא קיים"]
    for _ in range(40):
        t = rng.choice(["regular", "garden", "penthouse", "duplex"])
        a = {
            "street": str(rng.choice(streets)), "area": float(rng.integers(35, 220)),
            "rooms": float(rng.choice([2, 3, 3.5, 4, 5])) if rng.random() > .1 else None,
            "floor": int(rng.integers(0, 20)) if rng.random() > .1 else None,
            "buildingFloors": int(rng.integers(2, 25)) if rng.random() > .2 else None,
            "yearBuilt": int(rng.integers(1940, 2027)) if rng.random() > .2 else None,
            "newStatus": str(rng.choice(["", "", "", "ready", "presale"])), "type": str(t),
        }
        type_txt = {"regular": "דירה בבית קומות", "garden": "דירת גן",
                    "penthouse": "דירת גג (פנטהאוז)", "duplex": "דופלקס"}[t]
        row = pd.DataFrame([{
            "floor": np.nan if a["floor"] is None else a["floor"],
            "bfloors": np.nan if a["buildingFloors"] is None else a["buildingFloors"],
            "built": np.nan if a["yearBuilt"] is None else a["yearBuilt"],
            "rooms": np.nan if a["rooms"] is None else a["rooms"],
            "area": a["area"], "new": False, "status": a["newStatus"], "type": type_txt}])
        x = features(row, meta["ref_year"])[0]
        st = city["streets"].get(a["street"])
        logp = meta["intercept"] + city["eff"] + float(np.dot(meta["coef"], x)) + (st[0] if st else 0)
        cases.append({"cityId": c["id"], "input": a, "py_value": float(np.exp(logp) * a["area"])})

(ROOT / "tests/_cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
js = r"""
const fs = require('fs'); const E = require('./docs/estimator.js');
const meta = JSON.parse(fs.readFileSync('docs/data/meta.json'));
const cases = JSON.parse(fs.readFileSync('tests/_cases.json'));
let worst = 0;
for (const c of cases) {
  const city = JSON.parse(fs.readFileSync(`docs/data/cities/${c.cityId}.json`));
  const v = E.estimate(meta, city, c.input).value;
  worst = Math.max(worst, Math.abs(v / c.py_value - 1));
}
console.log(JSON.stringify({cases: cases.length, worst}));
"""
out = subprocess.run(["node", "-e", js], cwd=ROOT, capture_output=True, text=True, check=True)
res = json.loads(out.stdout)
(ROOT / "tests/_cases.json").unlink()
print(f"{res['cases']} cases, worst difference {res['worst']:.2e}")
sys.exit(0 if res["worst"] < 1e-4 else 1)
