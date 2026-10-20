/*
 * "שווה?" embed loader.
 * Put this where the price check should appear on your site:
 *
 *   <div class="shave-widget"
 *        data-brand="כהן נדל״ן"
 *        data-color="#0a7d55"
 *        data-contact="https://your-site.co.il/contact"
 *        data-city="רעננה"></div>
 *   <script src="https://roiabramzon946-gif.github.io/shave/embed.js" async></script>
 *
 * All data-* settings are optional. data-theme="dark" for dark sites.
 */
(function () {
  "use strict";
  var me = document.currentScript;
  var base = me && me.src ? me.src.replace(/embed\.js(\?.*)?$/, "") : "https://roiabramzon946-gif.github.io/shave/";
  var KEYS = ["brand", "color", "contact", "city", "theme", "title"];
  var frames = [];

  function mount(el) {
    if (el.getAttribute("data-shave-mounted")) return;
    el.setAttribute("data-shave-mounted", "1");
    var q = [];
    KEYS.forEach(function (k) {
      var v = el.getAttribute("data-" + k);
      if (v) q.push(k + "=" + encodeURIComponent(v));
    });
    var f = document.createElement("iframe");
    f.src = base + "widget.html" + (q.length ? "?" + q.join("&") : "");
    f.title = "בדיקת מחיר דירה";
    f.loading = "lazy";
    f.style.cssText = "width:100%;max-width:720px;border:0;display:block;height:520px;color-scheme:normal;";
    el.appendChild(f);
    frames.push(f);
  }

  window.addEventListener("message", function (ev) {
    if (!ev.data || ev.data.type !== "shave-height") return;
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === ev.source) {
        var h = Math.max(200, Math.min(3000, Number(ev.data.height) || 0));
        frames[i].style.height = h + "px";
      }
    }
  });

  function start() {
    var els = document.querySelectorAll(".shave-widget");
    for (var i = 0; i < els.length; i++) mount(els[i]);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
