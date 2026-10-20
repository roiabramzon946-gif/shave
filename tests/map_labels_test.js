/*
 * Checks docs/streetmap.js: highlighting any street must create exactly ONE fixed label,
 * however many pieces the street is made of on the map.
 *
 * Usage: node tests/map_labels_test.js docs/data/streets/<city>.json [more files...]
 * Runs without a browser: Leaflet and the page are replaced by small fakes that count labels.
 */
const fs = require("fs");
const path = require("path");

const files = process.argv.slice(2);
if (!files.length) { console.error("give one or more docs/data/streets/*.json files"); process.exit(2); }

let labels = 0, hoverOnly = 0;
const layer = () => ({ addTo() { return this; }, bindTooltip(t, o) { if (o && o.permanent) labels++; else hoverOnly++; return this; } });
global.L = {
  map: () => ({ remove() {}, fitBounds() {} }),
  tileLayer: () => layer(),
  polyline: () => layer(),
  featureGroup: () => ({ getBounds: () => [[0, 0], [1, 1]] }),
  tooltip: (o) => { if (o && o.permanent) labels++; const t = { setLatLng() { return t; }, setContent() { return t; }, addTo() { return t; } }; return t; },
  control: () => ({ addTo() { return this; } }),
  DomUtil: { create: () => ({ setAttribute() {} }) },
};
global.window = global;
global.document = { createElement: () => ({}), head: { appendChild() {} } };
let geo = null;
global.fetch = async () => ({ ok: true, json: async () => geo });
eval(fs.readFileSync(path.join(__dirname, "..", "docs", "streetmap.js"), "utf8"));

(async () => {
  let bad = 0, total = 0, multiPiece = 0;
  for (const f of files) {
    geo = JSON.parse(fs.readFileSync(f, "utf8"));
    if (!geo || !geo.streets) continue;          // e.g. index.json, the list of cities
    const names = Object.keys(geo.streets);
    const prices = Object.fromEntries(names.map((s, i) => [s, 20000 + i]));
    for (const s of names) {
      labels = 0;
      await ShaveMap.draw({ setAttribute() {} }, { geometryUrl: f + "?" + s, prices, highlight: s });
      total++;
      if (geo.streets[s].length > 1) multiPiece++;
      if (labels !== 1) { bad++; if (bad <= 5) console.log(`  ${geo.city} / ${s}: ${labels} labels (${geo.streets[s].length} pieces)`); }
    }
    console.log(`${geo.city}: ${names.length} streets checked`);
  }
  console.log(`${total} streets (${multiPiece} made of several pieces): ${bad ? bad + " with the wrong number of labels" : "every one has exactly 1 label"}`);
  process.exit(bad ? 1 : 0);
})();
