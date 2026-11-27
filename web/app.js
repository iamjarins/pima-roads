/* Road Work History Map. One screen: a map, and a history panel that opens when a road is clicked. */
(function () {
  "use strict";
  const CFG = window.ROADWORK_CONFIG;
  const $ = (id) => document.getElementById(id);
  const api = (path) => fetch(CFG.apiBase + path).then((r) => {
    if (!r.ok) throw new Error(r.status === 404 ? "not-found" : "HTTP " + r.status);
    return r.json();
  });

  // Small DOM builder. All text goes through textContent, so source strings are never parsed as HTML.
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v; else n.setAttribute(k, v);
    }
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) n.append(kid);
    return n;
  }
  const link = (href, text) => el("a", { href, target: "_blank", rel: "noopener" }, text);
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }) : null);

  // ---------------------------------------------------------------- colours

  // Newer work is darker. Bins are two fiscal years wide and end at the newest year in the data.
  const RAMP = ["#f2c14e", "#f08a24", "#e2543a", "#c2256f", "#7b2a9e", "#3b1a6b"];
  const NO_WORK = "#9aa3ad";
  const UPCOMING = "#0b7a75";
  let bins = [];

  function makeBins(hi) {
    hi = hi || new Date().getFullYear();
    bins = [];
    for (let i = 0; i < RAMP.length; i++) {
      const top = hi - 2 * (RAMP.length - 1 - i);
      bins.push({ from: i === 0 ? null : top - 1, to: top, color: RAMP[i],
                  label: i === 0 ? `${top} or earlier` : `${top - 1} to ${top}` });
    }
  }

  function colorExpression() {
    const step = ["step", ["get", "yr"], bins[0].color];
    for (let i = 1; i < bins.length; i++) step.push(bins[i].from, bins[i].color);
    return ["case", ["has", "yr"], step, NO_WORK];
  }

  function drawLegend() {
    const ul = $("legend-items");
    ul.replaceChildren(
      ...bins.slice().reverse().map((b) => el("li", {}, el("i", { style: `border-color:${b.color}` }), b.label)),
      el("li", {}, el("i", { class: "thin", style: `border-color:${NO_WORK}` }), "No work on record"),
      el("li", {}, el("i", { class: "dots", style: `border-color:${UPCOMING}` }), "Work planned or under way"),
      el("li", {}, el("i", { class: "dash", style: `border-color:${NO_WORK}` }), "Not a county or city road (records not loaded)")
    );
  }

  // ---------------------------------------------------------------- map

  const BASE_STYLE = { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef0f2" } }] };

  // The background map (street labels, parks, water) is loaded after our own roads are on screen, then slotted
  // underneath them. If it cannot be loaded, the roads stay on the plain background.
  async function addBasemap() {
    if (!CFG.basemapStyle) return;
    let style;
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 8000);
      const r = await fetch(CFG.basemapStyle, { signal: ctl.signal });
      clearTimeout(timer);
      if (!r.ok) throw new Error(r.status);
      style = await r.json();
    } catch (e) {
      console.warn("Background map unavailable, drawing roads on a plain background.", e);
      return;
    }
    try {
      if (style.glyphs) map.setGlyphs(style.glyphs);
      if (style.sprite) map.setSprite(style.sprite);
      for (const [id, src] of Object.entries(style.sources || {})) if (!map.getSource(id)) map.addSource(id, src);
      for (const layer of style.layers || []) {
        try {
          if (layer.type === "background") {
            if (layer.paint && layer.paint["background-color"]) map.setPaintProperty("bg", "background-color", layer.paint["background-color"]);
          } else if (!map.getLayer(layer.id)) {
            // labels go above our roads, everything else below them
            map.addLayer(layer, layer.type === "symbol" ? "roads-hit" : "roads-other");
          }
        } catch (e) { console.warn("Skipped background layer", layer.id, e); }
      }
    } catch (e) {
      console.warn("Background map could not be added.", e);
    }
  }

  let map;
  const publicOwner = ["in", ["get", "owner"], ["literal", ["county", "city"]]];

  function addRoadLayers() {
    const tiles = new URL(CFG.tiles, location.href).href.replace(/%7B/g, "{").replace(/%7D/g, "}");
    map.addSource("roads", { type: "vector", tiles: [tiles], minzoom: 7, maxzoom: CFG.tileMaxZoom,
      attribution: "Road data: Pima County GIS, Pima County DOT, City of Tucson DTM" });
    map.addSource("selected", { type: "geojson", data: { type: "FeatureCollection", features: [] } });

    // zoom must be the outermost expression, so a per-feature factor is multiplied inside each stop
    const width = (k) => ["interpolate", ["linear"], ["zoom"], 8, ["*", 0.6, k], 12, ["*", 1.4, k], 15, ["*", 3.2, k], 18, ["*", 8, k]];
    const major = ["==", ["get", "mj"], 1];

    map.addLayer({ id: "roads-other", type: "line", source: "roads", "source-layer": "roads",
      filter: ["all", ["!", publicOwner], ["!", ["has", "yr"]]],
      layout: { "line-cap": "butt" },
      paint: { "line-color": NO_WORK, "line-width": width(0.7), "line-dasharray": [3, 2], "line-opacity": 0.8 } });
    map.addLayer({ id: "roads-none", type: "line", source: "roads", "source-layer": "roads",
      filter: ["all", publicOwner, ["!", ["has", "yr"]]],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": NO_WORK, "line-width": width(["case", major, 1.1, 0.7]) } });
    map.addLayer({ id: "roads-worked", type: "line", source: "roads", "source-layer": "roads",
      filter: ["has", "yr"],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": colorExpression(), "line-width": width(["case", major, 1.7, 1.25]) } });
    map.addLayer({ id: "roads-upcoming", type: "line", source: "roads", "source-layer": "roads", minzoom: 11,
      filter: ["==", ["get", "up"], 1],
      paint: { "line-color": UPCOMING, "line-width": width(0.9), "line-dasharray": [0.2, 2.2] },
      layout: { "line-cap": "round" } });
    map.addLayer({ id: "selected-casing", type: "line", source: "selected",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": width(4.2) } });
    map.addLayer({ id: "selected-line", type: "line", source: "selected",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#111827", "line-width": width(2.2) } });
    // Invisible wide line: the click and tap target.
    map.addLayer({ id: "roads-hit", type: "line", source: "roads", "source-layer": "roads",
      paint: { "line-color": "#000", "line-opacity": 0, "line-width": 18 } });
  }

  // Distance in pixels from a point to a rendered feature, so the nearest road wins a tap between two.
  function pixelDistance(point, feature) {
    const lines = feature.geometry.type === "LineString" ? [feature.geometry.coordinates] : feature.geometry.coordinates;
    let best = Infinity;
    for (const line of lines) {
      let a = map.project(line[0]);
      for (let i = 1; i < line.length; i++) {
        const b = map.project(line[i]);
        const dx = b.x - a.x, dy = b.y - a.y;
        const len2 = dx * dx + dy * dy;
        const t = len2 ? Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / len2)) : 0;
        best = Math.min(best, Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy)));
        a = b;
      }
    }
    return best;
  }

  function onMapClick(e) {
    const touch = e.originalEvent && (e.originalEvent.pointerType === "touch" || "ontouchstart" in window);
    const r = touch ? 16 : 9;
    const box = [[e.point.x - r, e.point.y - r], [e.point.x + r, e.point.y + r]];
    const hits = map.queryRenderedFeatures(box, { layers: ["roads-hit"] });
    if (!hits.length) return;
    let best = null, bestD = Infinity;
    for (const f of hits) {
      const d = pixelDistance(e.point, f);
      if (d < bestD) { bestD = d; best = f; }
    }
    openSegment(best.id, { fit: false });
  }

  // ---------------------------------------------------------------- history panel

  let current = null;
  let selected = null;   // geometry of the open segment
  const fetchSegment = (id) => Promise.all([api(`/api/segments/${id}`), api(`/api/segments/${id}/events`)]);

  function showSelected() {
    const src = map && map.getSource("selected");
    if (src) src.setData(selected ? { type: "Feature", geometry: selected, properties: {} } : { type: "FeatureCollection", features: [] });
  }

  function fitOptions() {
    const phone = window.matchMedia("(max-width: 720px)").matches;
    return { maxZoom: 16, padding: phone ? { top: 80, bottom: Math.round(window.innerHeight * 0.62) + 30, left: 40, right: 40 }
                                         : { top: 80, bottom: 60, left: 60, right: 460 } };
  }

  function closePanel() {
    current = null;
    selected = null;
    $("panel").hidden = true;
    document.body.classList.remove("panel-open");
    showSelected();
    history.replaceState(null, "", location.pathname + location.search);
  }

  function stretchText(seg) {
    if (seg.from_desc && seg.to_desc) return `${seg.from_desc} to ${seg.to_desc}`;
    if (seg.from_desc || seg.to_desc) return `At ${seg.from_desc || seg.to_desc}`;
    return "";
  }

  function fact(label, value, emptyText) {
    return [el("dt", {}, label), el("dd", { class: value ? null : "none" }, value || emptyText)];
  }

  function projectLine(p) {
    if (!p.name) return null;
    const out = [safeUrl(p.link) ? link(p.link, p.name) : p.name];
    const c = p.code;
    if (c) {
      const parts = [`fiscal ${c.fiscal_year}`, c.delivery && c.delivery.toLowerCase(), c.program && c.program.toLowerCase(),
                     c.job_number && `job ${c.job_number}`].filter(Boolean);
      out.push(el("span", { class: "basis" }, `Code read as: ${parts.join(", ")}. These meanings are inferred, not confirmed by the county.`));
    }
    return out;
  }

  function eventCard(ev) {
    const t = ev.treatment;
    const src = ev.source;
    const funding = ev.funding && [ev.funding.program, ev.funding.detail && ev.funding.detail !== ev.funding.program ? `(${ev.funding.detail})` : null]
      .filter(Boolean).join(" ");
    const contractor = ev.contractor.name &&
      [ev.contractor.name, ev.contractor.vendor && ev.contractor.vendor !== ev.contractor.name
        ? el("span", { class: "basis" }, `Also written as ${ev.contractor.vendor}`) : null];
    return el("article", { class: "event" + (ev.status === "completed" ? "" : " upcoming") },
      el("p", { class: "when" }, ev.when, ev.status !== "completed" && el("span", { class: "badge " + ev.status }, ev.status_label)),
      el("h3", {}, t.label),
      el("p", { class: "explain" }, t.explanation, t.description && t.description.toLowerCase() !== t.label.toLowerCase()
        ? el("span", { class: "basis" }, `Source description: ${t.description}`) : null),
      el("dl", { class: "facts" },
        fact("Contractor", contractor, "Not recorded"),
        fact("Project", projectLine(ev.project), "Not recorded"),
        fact("Funding", funding, src.key === "county_work" ? "Not in the county record" : "Not recorded"),
        fact("Cost", ev.cost.status === "not_linked" ? null
          : [ev.cost.text, el("span", { class: "basis" }, `Confidence: ${ev.cost.confidence}`),
             safeUrl(ev.cost.document_link) && link(ev.cost.document_link, "Contract document")], ev.cost.text),
        fact("Record covers", [ev.stretch, ev.length_mi ? ` (${ev.length_mi} mi)` : null].filter(Boolean), "Not described")),
      ev.match.note && el("p", { class: "note" }, ev.match.note),
      el("p", { class: "source" },
        "Source: ", link(src.record_url, `${src.title}, record ${src.record_url.split("/").pop().split("?")[0]}`),
        `. Pulled ${day(src.pulled_at)}.`,
        src.record_edited_at ? ` Agency last edited this record ${day(src.record_edited_at)}.` : ""));
  }

  function renderHistory(seg, hist) {
    const body = $("p-body");
    const ownerKnown = seg.owner !== "unknown";
    const kids = [
      el("p", { class: "owner" }, el("strong", {}, ownerKnown ? "Maintained by: " : "Maintained by: not confirmed"),
        ownerKnown ? seg.owner_name : null, el("small", {}, seg.owner_basis + (seg.owner_basis.endsWith(".") ? "" : "."))),
      el("div", { class: "actions" }, el("button", { class: "btn", type: "button", id: "share" }, "Copy link to this road")),
    ];
    if (hist.upcoming.length) {
      kids.push(el("h2", { class: "section" }, "Planned and in progress"), ...hist.upcoming.map(eventCard));
    }
    kids.push(el("h2", { class: "section" }, `Completed work${hist.completed.length ? ` (${hist.completed.length})` : ""}`));
    if (hist.completed.length) {
      kids.push(...hist.completed.map(eventCard));
      kids.push(el("p", { class: "muted" }, "Older work may exist but not be on record. ", hist.coverage_notes.join(" ")));
    } else {
      kids.push(el("div", { class: "empty" }, el("strong", {}, hist.empty_message), hist.coverage_notes.join(" "),
        el("span", { class: "basis" }, "Checked: ",
          hist.sources_checked.map((s, i) => [i ? "; " : "", link(s.url, s.title), ` (pulled ${day(s.pulled_at)})`]), ".")));
    }
    body.replaceChildren(...kids);
    $("share").addEventListener("click", async (e) => {
      try { await navigator.clipboard.writeText(location.href); e.target.textContent = "Link copied"; }
      catch { prompt("Copy this link", location.href); }
    });
  }

  async function openSegment(id, opts) {
    id = Number(id);
    if (!Number.isInteger(id)) return;
    current = id;
    $("hint").classList.add("gone");
    $("panel").hidden = false;
    document.body.classList.add("panel-open");
    $("p-body").replaceChildren(el("p", { class: "loading" }, "Loading history…"));
    history.replaceState(null, "", `#s=${id}`);
    try {
      const [seg, hist] = await ((opts && opts.preloaded) || fetchSegment(id));
      if (current !== id) return;
      $("p-name").textContent = seg.name;
      $("p-stretch").textContent = stretchText(seg);
      document.title = `${seg.name} · Road Work History Map`;
      selected = seg.geometry;
      showSelected();
      if (opts && opts.fit && map) {
        map.fitBounds([[seg.bbox[0], seg.bbox[1]], [seg.bbox[2], seg.bbox[3]]], { ...fitOptions(), duration: 600 });
      }
      renderHistory(seg, hist);
      $("p-body").scrollTop = 0;
      $("p-name").focus({ preventScroll: true });
    } catch (err) {
      if (current !== id) return;
      $("p-name").textContent = err.message === "not-found" ? "Road not found" : "Could not load this road";
      $("p-stretch").textContent = "";
      $("p-body").replaceChildren(el("p", { class: "muted" }, err.message === "not-found"
        ? "This link points to a road segment that is no longer in the street network."
        : "The history could not be loaded. Check your connection and try again."));
    }
  }

  // ---------------------------------------------------------------- search

  let searchSeq = 0, searchTimer = null, results = [], active = -1;

  function showResults(list, query) {
    results = list; active = -1;
    const ul = $("results");
    if (!query) { ul.hidden = true; $("q").setAttribute("aria-expanded", "false"); return; }
    ul.replaceChildren(...(list.length
      ? list.map((r, i) => el("li", { role: "option", id: `r${i}`, "data-i": i, "aria-selected": "false" },
          el("span", {}, r.label), el("small", {}, r.type === "address" ? "Address" : `${r.blocks} block${r.blocks === 1 ? "" : "s"}`)))
      : [el("li", { class: "none" }, "No street or address found in Pima County")]));
    ul.hidden = false;
    $("q").setAttribute("aria-expanded", "true");
  }

  function choose(r) {
    if (!r) return;
    $("results").hidden = true;
    $("q").value = r.label;
    $("q").blur();
    if (r.segment_id) return openSegment(r.segment_id, { fit: true });
    $("hint").classList.add("gone");
    map.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], { padding: 70, maxZoom: 16, duration: 700 });
  }

  function setActive(i) {
    active = i;
    document.querySelectorAll("#results li[role=option]").forEach((li, j) => li.setAttribute("aria-selected", String(j === i)));
    if (i >= 0) $("q").setAttribute("aria-activedescendant", `r${i}`); else $("q").removeAttribute("aria-activedescendant");
  }

  function wireSearch() {
    const input = $("q");
    input.addEventListener("input", () => {
      clearTimeout(searchTimer);
      const query = input.value.trim();
      if (query.length < 2) return showResults([], "");
      searchTimer = setTimeout(async () => {
        const seq = ++searchSeq;
        try {
          const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
          if (seq === searchSeq) showResults(data.results, query);
        } catch { /* keep the last results */ }
      }, 180);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(active + 1, results.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(active - 1, 0)); }
      else if (e.key === "Escape") showResults([], "");
    });
    $("search").addEventListener("submit", (e) => { e.preventDefault(); choose(results[Math.max(active, 0)]); });
    $("results").addEventListener("click", (e) => {
      const li = e.target.closest("li[role=option]");
      if (li) choose(results[Number(li.dataset.i)]);
    });
    document.addEventListener("click", (e) => { if (!e.target.closest("#search")) $("results").hidden = true; });
  }

  // ---------------------------------------------------------------- about

  function fillAbout(meta) {
    $("about-sources").replaceChildren(...meta.sources.map((s) => el("li", {},
      link(s.url, s.title), `, ${s.owner}. `,
      s.pulled_at ? `Last pulled ${day(s.pulled_at)}, ${s.row_count.toLocaleString()} rows. ` : "Not pulled yet. ",
      s.carries_work_history ? "Work history." : "Used for the street network and who maintains each road.")));
    $("about-treatments").replaceChildren(...meta.treatments.flatMap((t) => [el("dt", {}, t.name), el("dd", {}, t.explanation)]));
  }

  // ---------------------------------------------------------------- start

  async function start() {
    wireSearch();
    $("panel-close").addEventListener("click", closePanel);
    $("about-open").addEventListener("click", () => $("about").showModal());
    $("legend-toggle").addEventListener("click", () => {
      const closed = $("legend").classList.toggle("closed");
      $("legend-toggle").setAttribute("aria-expanded", String(!closed));
    });
    if (window.matchMedia("(max-width: 720px)").matches) $("legend-toggle").click();
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("panel").hidden && !$("about").open) closePanel(); });

    // A shared link opens straight on its road: fetch the history at once and start the map there.
    const linked = /^#s=(\d+)$/.exec(location.hash);
    const preloaded = linked ? fetchSegment(linked[1]) : null;
    if (linked) openSegment(linked[1], { preloaded });
    const [meta, first] = await Promise.all([api("/api/meta").catch(() => null), preloaded ? preloaded.catch(() => null) : null]);
    makeBins(meta && meta.years && meta.years.hi);
    drawLegend();
    if (meta) fillAbout(meta);

    const view = first
      ? { bounds: [[first[0].bbox[0], first[0].bbox[1]], [first[0].bbox[2], first[0].bbox[3]]], fitBoundsOptions: fitOptions() }
      : { center: CFG.center, zoom: CFG.zoom };
    map = new maplibregl.Map({ container: "map", style: BASE_STYLE, ...view, minZoom: 7, maxZoom: 19,
      maxBounds: CFG.bounds, dragRotate: false, pitchWithRotate: false, attributionControl: { compact: true } });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.GeolocateControl({ fitBoundsOptions: { maxZoom: 15 } }), "top-right");
    window.roadworkMap = map; // handy for debugging and tests

    map.on("load", () => {
      addRoadLayers();
      showSelected();
      map.on("click", onMapClick);
      map.on("mousemove", "roads-hit", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "roads-hit", () => { map.getCanvas().style.cursor = ""; });
      addBasemap();
    });
    window.addEventListener("hashchange", () => {
      const m = /^#s=(\d+)$/.exec(location.hash);
      if (m && Number(m[1]) !== current) openSegment(m[1], { fit: true });
    });
    setTimeout(() => $("hint").classList.add("gone"), 9000);
  }

  start();
})();
