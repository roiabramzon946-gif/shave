/*
 * Street price map: draws a city's streets colored by price per m², optionally highlighting one street.
 * Used by the price check (index.html) and the city pages.
 *
 *   ShaveMap.draw(element, { geometryUrl, prices: {street: ppsqm}, highlight: "street name" })
 *
 * Map data © OpenStreetMap contributors (ODbL). Leaflet is loaded on first use.
 */
(function (root) {
  "use strict";
  var LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist/";
  var COLORS = ["#1a9850", "#91cf60", "#e6c84f", "#fc8d59", "#d73027"];   // cheap -> expensive
  var loading = null;
  var maps = new WeakMap();

  function loadLeaflet() {
    if (root.L) return Promise.resolve(root.L);
    if (loading) return loading;
    loading = new Promise(function (ok, bad) {
      var css = document.createElement("link");
      css.rel = "stylesheet"; css.href = LEAFLET + "leaflet.css";
      document.head.appendChild(css);
      var s = document.createElement("script");
      s.src = LEAFLET + "leaflet.js";
      s.onload = function () { ok(root.L); };
      s.onerror = bad;
      document.head.appendChild(s);
    });
    return loading;
  }

  var geoCache = {};
  function loadGeometry(url) {
    if (!geoCache[url]) {
      geoCache[url] = fetch(url).then(function (r) { if (!r.ok) throw new Error(url); return r.json(); });
    }
    return geoCache[url];
  }

  function quantiles(values, n) {
    var v = values.slice().sort(function (a, b) { return a - b; });
    var out = [];
    for (var i = 1; i < n; i++) out.push(v[Math.floor((i / n) * (v.length - 1))]);
    return out;
  }
  function bin(x, cuts) { var i = 0; while (i < cuts.length && x > cuts[i]) i++; return i; }
  var nf = new Intl.NumberFormat("he-IL", { maximumFractionDigits: 0 });
  function money(v) { return "₪" + nf.format(Math.round(v / 100) * 100); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function draw(el, opts) {
    return Promise.all([loadLeaflet(), loadGeometry(opts.geometryUrl)]).then(function (res) {
      var L = res[0], geo = res[1];
      var old = maps.get(el);
      if (old) old.remove();
      el.setAttribute("dir", "ltr");
      var map = L.map(el, { scrollWheelZoom: false, zoomControl: true, attributionControl: true });
      maps.set(el, map);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
      }).addTo(map);

      var prices = opts.prices || {};
      var names = Object.keys(geo.streets).filter(function (s) { return prices[s] > 0; });
      var cuts = names.length >= 5 ? quantiles(names.map(function (s) { return prices[s]; }), 5) : [];

      names.forEach(function (s) {
        var color = cuts.length ? COLORS[bin(prices[s], cuts)] : COLORS[2];
        geo.streets[s].forEach(function (line) {
          L.polyline(line, { color: color, weight: 4, opacity: 0.85 })
            .bindTooltip("<b>" + esc(s) + "</b><br>" + money(prices[s]) + ' למ"ר', { sticky: true, direction: "top" })
            .addTo(map);
        });
      });

      var hl = opts.highlight && geo.streets[opts.highlight];
      if (hl) {
        var group = [];
        var label = "<b>" + esc(opts.highlight) + "</b>" + (prices[opts.highlight] ? "<br>" + money(prices[opts.highlight]) + ' למ"ר' : "");
        hl.forEach(function (line) {
          group.push(L.polyline(line, { color: "#17212b", weight: 10, opacity: 0.9 }).addTo(map));
          group.push(L.polyline(line, { color: "#ffffff", weight: 4, opacity: 1 }).addTo(map)
            .bindTooltip(label, { sticky: true, direction: "top" }));
        });
        // one label for the whole street (a street is made of many short pieces on the map),
        // placed in the middle of its longest piece
        var longest = hl.reduce(function (best, line) {
          var len = 0;
          for (var i = 1; i < line.length; i++) len += Math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]);
          return len > best.len ? { len: len, line: line } : best;
        }, { len: -1, line: hl[0] }).line;
        L.tooltip({ permanent: true, direction: "top", className: "shave-hl", offset: [0, -6] })
          .setLatLng(longest[Math.floor(longest.length / 2)])
          .setContent(label)
          .addTo(map);
        map.fitBounds(L.featureGroup(group).getBounds(), { padding: [40, 40], maxZoom: 16 });
      } else {
        map.fitBounds(geo.bounds, { padding: [10, 10] });
      }

      if (cuts.length) {
        var legend = L.control({ position: "bottomleft" });
        legend.onAdd = function () {
          var d = L.DomUtil.create("div", "shave-legend");
          d.setAttribute("dir", "rtl");
          var lo = Math.min.apply(null, names.map(function (s) { return prices[s]; }));
          var hi = Math.max.apply(null, names.map(function (s) { return prices[s]; }));
          var edges = [lo].concat(cuts).concat([hi]);
          var rows = COLORS.map(function (c, i) {
            return '<div><i style="background:' + c + '"></i><span dir="ltr">' + money(edges[i]) + " – " + money(edges[i + 1]) + "</span></div>";
          }).reverse().join("");
          d.innerHTML = '<b>מחיר למ"ר</b>' + rows;
          return d;
        };
        legend.addTo(map);
      }
      return { map: map, matched: names.length, highlighted: !!hl };
    });
  }

  // small style block for the legend and the highlighted label
  var st = document.createElement("style");
  st.textContent =
    ".shave-legend{background:rgba(255,255,255,.92);color:#17212b;padding:8px 10px;border-radius:6px;font:12px/1.5 'IBM Plex Sans Hebrew',Arial,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.2)}" +
    ".shave-legend b{display:block;margin-bottom:2px}.shave-legend i{display:inline-block;width:14px;height:10px;margin-left:6px;border-radius:2px;vertical-align:middle}" +
    ".shave-legend div{direction:rtl;font-variant-numeric:tabular-nums}" +
    ".leaflet-tooltip{font-family:'IBM Plex Sans Hebrew',Arial,sans-serif;direction:rtl;text-align:right}" +
    ".shave-hl{font-weight:600}";
  document.head.appendChild(st);

  root.ShaveMap = { draw: draw };
})(window);
