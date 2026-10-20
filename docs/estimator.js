/*
 * Price estimator — mirrors pipeline/build_model.py (features() and predict()).
 * Works in the browser (window.Estimator) and in Node (module.exports) for tests.
 */
(function (root) {
  "use strict";

  // KEEP IN SYNC with FEATURES / features() in pipeline/build_model.py
  function featureVector(a, refYear) {
    const floor = a.floor == null || a.floor === "" ? null : Number(a.floor);
    const bf = a.buildingFloors == null || a.buildingFloors === "" ? null : Number(a.buildingFloors);
    // status: "" (from building year), "ready" (new, ready to move in), "presale" (off-plan)
    const status = a.newStatus || (a.newProject ? "ready" : "");
    let built = a.yearBuilt == null || a.yearBuilt === "" ? null : Number(a.yearBuilt);
    if (built == null && status === "ready") built = refYear;
    if (built == null && status === "presale") built = refYear + 2;
    const age = built == null ? null : Math.min(80, Math.max(0, refYear - built));
    const rooms = a.rooms == null || a.rooms === "" ? a.area / 27 : Number(a.rooms);
    const type = a.type || "regular";
    const b = (x) => (x ? 1 : 0);
    return {
      floor: floor == null ? 2 : Math.min(30, Math.max(0, floor)),
      floor_missing: b(floor == null),
      ground: b(floor === 0),
      top: b(floor != null && bf != null && floor >= bf && bf >= 3),
      log_area: Math.log(a.area / 90),
      rooms_dev: rooms - a.area / 27,
      age_new: b(age != null && age <= 2),
      age_3_10: b(age != null && age >= 3 && age <= 10),
      age_26_45: b(age != null && age >= 26 && age <= 45),
      age_46p: b(age != null && age >= 46),
      age_missing: b(age == null),
      new_ready: b(status === "ready" || (status === "" && built != null && built >= refYear - 1 && built <= refYear)),
      presale: b(status === "presale" || (status === "" && built != null && built > refYear)),
      highrise: b(bf != null && bf >= 9),
      garden: b(type === "garden"),
      penthouse: b(type === "penthouse"),
      duplex: b(type === "duplex"),
    };
  }

  function bucketFor(n) {
    return n < 5 ? "few" : n < 20 ? "some" : "many";
  }

  /*
   * meta: contents of data/meta.json; city: contents of data/cities/cN.json
   * a: {street, rooms, area, floor, buildingFloors, yearBuilt, newProject, type}
   */
  function estimate(meta, city, a) {
    const x = featureVector(a, meta.ref_year);
    const unknown = meta.features.filter((f) => !(f in x));
    if (unknown.length) {
      // the data was built by a newer model than this script (usually an old cached file)
      const err = new Error("version mismatch: " + unknown.join(","));
      err.code = "VERSION_MISMATCH";
      throw err;
    }
    let logp = meta.intercept + city.eff;
    meta.features.forEach((f, i) => { logp += meta.coef[i] * x[f]; });

    const st = a.street && city.streets[a.street];
    const streetEff = st ? st[0] : 0;
    const streetN = st ? st[1] : 0;
    logp += streetEff;

    const q = meta.interval[bucketFor(streetN)];
    const ppsqm = Math.exp(logp);
    return {
      ppsqm,
      value: ppsqm * a.area,
      low: Math.exp(logp + q.p10) * a.area,
      high: Math.exp(logp + q.p90) * a.area,
      streetKnown: !!st,
      streetDeals: streetN,
      streetMedianPpsqm: st ? st[2] : null,
    };
  }

  function verdict(asking, est) {
    const diff = asking / est.value - 1;
    let level;
    if (asking < est.low) level = "low";
    else if (asking <= est.value * 1.05) level = "fair";
    else if (asking <= est.high) level = "upper";
    else if (diff <= 0.25) level = "high";
    else level = "very_high";
    return { diff, level };
  }

  /* price index offset: multiply an old deal's price by this to bring it to today's level */
  function trendFactor(city, isoDate) {
    if (!city.trend || !city.trend.length) return 1;
    const d = new Date(isoDate);
    const key = d.getFullYear() + "Q" + (Math.floor(d.getMonth() / 3) + 1);
    const latest = city.trend[city.trend.length - 1][1];
    const row = city.trend.find((r) => r[0] === key);
    return row ? latest / row[1] : 1;
  }

  function similarDeals(city, a, limit) {
    const cols = city.deals_cols;
    const ix = Object.fromEntries(cols.map((c, i) => [c, i]));
    const rows = city.deals.filter((r) => r[ix.street] === a.street);
    const now = Date.now();
    const scored = rows.map((r) => {
      const rooms = r[ix.rooms];
      const roomDiff = rooms == null || a.rooms == null ? 0.5 : Math.abs(rooms - a.rooms);
      const areaDiff = Math.abs(r[ix.area] - a.area) / a.area;
      const ageYears = (now - new Date(r[ix.date]).getTime()) / 3.15e10;
      const factor = trendFactor(city, r[ix.date]);
      return {
        address: r[ix.address], date: r[ix.date], rooms, floor: r[ix.floor], area: r[ix.area],
        price: r[ix.price], ppsqm: r[ix.price] / r[ix.area], adjPrice: r[ix.price] * factor,
        score: roomDiff * 1.0 + areaDiff * 2.0 + ageYears * 0.15,
      };
    }).filter((d) => d.rooms == null || a.rooms == null || Math.abs(d.rooms - a.rooms) <= 1);
    scored.sort((p, q) => p.score - q.score);
    return scored.slice(0, limit || 8);
  }

  const api = { featureVector, estimate, verdict, similarDeals, trendFactor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Estimator = api;
})(typeof window !== "undefined" ? window : this);
