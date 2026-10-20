/*
 * Background: a faint land-registry map (blocks and parcels, like a גוש/חלקה survey sheet).
 * Draws into any <div class="cadastre" aria-hidden="true"></div>. Same drawing on every visit.
 */
(function () {
  "use strict";
  var hosts = document.querySelectorAll(".cadastre");
  if (!hosts.length) return;

  var W = 1600, H = 640, COLS = 8, ROWS = 4;
  var seed = 6108;                                   // fixed seed: the map never "jumps" between visits
  function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
  function j(v) { return (rnd() - 0.5) * v; }
  function lerp(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]; }
  function pt(p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }

  // street grid with slightly crooked corners
  var cw = W / COLS, ch = H / ROWS, P = [];
  for (var r = 0; r <= ROWS; r++) {
    P.push([]);
    for (var c = 0; c <= COLS; c++) P[r].push([c * cw + j(cw * 0.35), r * ch + j(ch * 0.3)]);
  }

  var GAP = 0.06;                                    // streets between blocks
  var out = [];
  var parcelNo = 1;
  for (r = 0; r < ROWS; r++) {
    for (c = 0; c < COLS; c++) {
      var a = P[r][c], b = P[r][c + 1], d = P[r + 1][c], e = P[r + 1][c + 1];
      // shrink the block a little so streets show between blocks
      var A = lerp(a, e, GAP), B = lerp(b, d, GAP), E = lerp(e, a, GAP), D = lerp(d, b, GAP);
      out.push('<polygon class="blk" points="' + [A, B, E, D].map(pt).join(" ") + '"/>');
      // parcels: strips across the block's long side, with a back line through the middle
      var wide = Math.hypot(B[0] - A[0], B[1] - A[1]) > Math.hypot(D[0] - A[0], D[1] - A[1]);
      var n = 3 + Math.floor(rnd() * 4);
      var mid1 = wide ? lerp(A, D, 0.5) : lerp(A, B, 0.5), mid2 = wide ? lerp(B, E, 0.5) : lerp(D, E, 0.5);
      if (rnd() > 0.35) out.push('<line class="prc" x1="' + mid1[0].toFixed(1) + '" y1="' + mid1[1].toFixed(1) + '" x2="' + mid2[0].toFixed(1) + '" y2="' + mid2[1].toFixed(1) + '"/>');
      for (var k = 1; k < n; k++) {
        var t = k / n + j(0.08);
        var p1 = wide ? lerp(A, B, t) : lerp(A, D, t), p2 = wide ? lerp(D, E, t) : lerp(B, E, t);
        out.push('<line class="prc" x1="' + p1[0].toFixed(1) + '" y1="' + p1[1].toFixed(1) + '" x2="' + p2[0].toFixed(1) + '" y2="' + p2[1].toFixed(1) + '"/>');
      }
      // parcel numbers in a few strips, block number in the middle
      for (k = 0; k < n; k++) {
        if (rnd() < 0.45) { parcelNo += 1 + Math.floor(rnd() * 3); continue; }
        var tt = (k + 0.5) / n, q1 = wide ? lerp(A, B, tt) : lerp(A, D, tt), q2 = wide ? lerp(D, E, tt) : lerp(B, E, tt);
        var m = lerp(q1, q2, rnd() > 0.5 ? 0.28 : 0.72);
        out.push('<text class="no" x="' + m[0].toFixed(1) + '" y="' + (m[1] + 4).toFixed(1) + '">' + (parcelNo++) + "</text>");
      }
      if (rnd() > 0.55) {
        var ctr = lerp(lerp(A, E, 0.5), lerp(B, D, 0.5), 0.5);
        out.push('<text class="blkno" x="' + ctr[0].toFixed(1) + '" y="' + (ctr[1] + 6).toFixed(1) + '">' + (6100 + r * COLS + c) + "</text>");
      }
    }
  }
  var svg = '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMin slice" focusable="false">' + out.join("") + "</svg>";
  for (var i = 0; i < hosts.length; i++) hosts[i].innerHTML = svg;
})();
