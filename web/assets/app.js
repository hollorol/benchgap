/* benchgap.net front-end: renders the site data that serve.php computes.
 *
 * Every page loads data/site.json (benchmarks, models, counts) and its own
 * slice of the scores (data/..., src/Site.php), never the whole database.
 * No framework (CI only minifies it): each page has its own path (serve.php
 * serves it with a plain-HTML summary in <main>, which this replaces); old #/
 * links are forwarded.
 *   /                       leaderboard (default benchmark)
 *   /b/<benchmark/version>  leaderboard for one benchmark
 *   /matrix                 models x benchmarks score matrix
 *   /model/<slug>           one model across all benchmarks
 *   /calibration            predictability matrix + list of mappings
 *   /calibration/<id>       one fitted mapping (scatter + curve)
 *   /multivariate           each benchmark from several others (fit + predicted vs measured)
 *   /method                 methodology
 *   /api                    public API documentation (api/v1/)
 */
(function () {
  "use strict";

  const REPO_URL = "https://github.com/hollorol/benchgap";
  // the ledes below are also on the pages serve.php renders (src/Pages.php): keep the two in step
  const ABOUT = "benchgap is an LLM benchmark leaderboard that fills in the missing scores. Most models are only "
    + "ever run on a handful of benchmarks, so benchgap calibrates benchmarks against each other on the models "
    + "measured on both, then estimates each missing score with its cross-validated error and a confidence level. "
    + "Measured and estimated scores are always marked apart.";
  // --- state ------------------------------------------------------------------
  const store = {
    get(k, d) { try { const v = localStorage.getItem("bg-" + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("bg-" + k, JSON.stringify(v)); } catch (e) {} },
  };
  const prefs = {
    show: store.get("show", "all"),         // "measured" | "reliable" | "all"
    dense: store.get("dense", true),
    sortCol: null,
  };

  let D = null;           // what every page uses (data/site.json): metadata, capabilities, benchmarks, models
  const ix = {};          // indexes

  const $ = (sel, el) => (el || document).querySelector(sel);
  const main = $("#main");
  const tip = $("#tip");

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v, d = 1) => (v * 100).toFixed(d);

  // --- data loading & indexing ----------------------------------------------
  // the documents loaded so far by URL, each a promise of its JSON; a failed one is dropped, to retry
  const docs = new Map();
  function load(url) {
    if (!docs.has(url)) {
      const doc = fetch(url).then((r) => {
        if (!r.ok) throw Object.assign(new Error(`${r.status} ${r.statusText}`), { status: r.status });
        return r.json();
      });
      doc.catch(() => docs.delete(url));
      docs.set(url, doc);
    }
    return docs.get(url);
  }
  // each page's data (src/Site.php)
  const boardUrl = (key) => `/data/b/${key.split("/").map(encodeURIComponent).join("/")}.json`;
  const modelUrl = (slug) => `/data/model/${encodeURIComponent(slug)}.json`;
  const scoreUrl = (s) => `/data/score/${s.m}/${s.b}.json`;

  // list -> Map of key -> [items]
  function groupBy(list, key) {
    const out = new Map();
    for (const x of list) {
      const k = key(x);
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(x);
    }
    return out;
  }

  function buildIndex() {
    ix.bench = new Map(D.benchmarks.map((b) => [b.id, b]));
    ix.benchByKey = new Map(D.benchmarks.map((b) => [b.key, b]));
    ix.model = new Map(D.models.map((m) => [m.id, m]));
    ix.modelBySlug = new Map(D.models.map((m) => [m.slug, m]));
    ix.cap = new Map(D.capabilities.map((c) => [c.id, c]));
    ix.cell = new Map();   // the scores loaded so far, by "model:benchmark", for the tooltips
    // the site shows only what the backend lists (Snapshot::LISTED); counts come listed already
    ix.listed = D.benchmarks.filter((b) => b.listed);
    ix.home = D.meta.home;   // the home page's benchmark (Snapshot::home)
    ix.byCap = groupBy(ix.listed, (b) => b.capability);
    ix.benchesByCap = D.capabilities.filter((c) => ix.byCap.has(c.id)).map((c) => ({ cap: c, benches: ix.byCap.get(c.id) }));
  }
  // keeps loaded scores for the tooltips; a full score is never replaced by a matrix cell's partial one
  function remember(scores) {
    for (const s of scores) {
      const key = s.m + ":" + s.b, old = ix.cell.get(key);
      if (!old || old.partial || !s.partial) ix.cell.set(key, s);
    }
    return scores;
  }

  const methodLabel = (k) => D.meta.methods[k] || k;
  const capLabel = (id) => (ix.cap.get(id) || {}).label || id;
  const capOf = (id) => ix.bench.get(id).capability;   // a benchmark's capability
  const benchLabel = (id) => (ix.bench.get(id) || {}).label || "?";

  const visible = (s) =>
    s.s === "m" || (prefs.show === "all") || (prefs.show === "reliable" && s.tier !== "low");

  // --- tooltip ----------------------------------------------------------------
  // "Benchmark = x%" for each measured input of an estimate
  const sourceList = (s, link) =>
    s.via.from
      .map((f) => {
        const label = esc(benchLabel(f.b));
        const fb = ix.bench.get(f.b);
        return `${link && fb ? `<a href="${benchHref(fb)}">${label}</a>` : label} = ${f.v == null ? "?" : pct(f.v) + "%"}`;
      })
      .join(", ");

  // the model's name, heading the sheet (the hover tooltip sits next to it)
  const modelLine = (s) => `<div class="h3">${esc(ix.model.get(s.m).name)}</div>`;
  // the calibration an estimate came from, if it came from a single one
  const fitHref = (s) => (s.s === "e" && s.via && s.via.kind === "uni" ? mappingHref(s.via.mapping) : "");

  function describeEstimate(s, link) {
    const b = ix.bench.get(s.b);
    const lines = [];
    lines.push(`<div class="t-h">${esc(b.label)}<span class="t-tier ${s.tier}">${s.tier} confidence</span></div>`);
    if (link) lines.push(modelLine(s));
    if (s.partial) {   // a matrix cell: the details are loading (details())
      lines.push(`<div class="t-v"><i>≈ ${pct(s.v)}%</i></div><div class="t-note">Loading where it came from…</div>`);
      return lines.join("");
    }
    lines.push(`<div class="t-v"><i>≈ ${pct(s.v)}%</i> <span class="t-note">± ${pct(s.sd)} pp</span></div>`);
    lines.push(`<div>Estimated from ${sourceList(s, link)} via ${esc(methodLabel(s.method))}, fitted on ${s.via.n} models measured on both.</div>`);
    if (s.why && s.why.length) lines.push(`<ul>${s.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`);
    else lines.push(`<div class="t-note">Low cross-validated error, inside the fitted range.</div>`);
    lines.push(`<div class="t-note">Not a measured score.</div>`);
    return lines.join("");
  }
  const HARNESSES = { "artificial-analysis": "Artificial Analysis", "vals-ai": "Vals AI" };
  const harnessNote = (b) => HARNESSES[b.harness] ? `Run by ${HARNESSES[b.harness]}.` : "A published result.";
  const host = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };
  function describeMeasured(s, link) {
    const b = ix.bench.get(s.b);
    return `<div class="t-h">${esc(b.label)} · measured</div>${link ? modelLine(s) : ""}`
      + `<div class="t-v">${pct(s.v)}%</div><div class="t-note">${esc(harnessNote(b))}</div>`;
  }
  let tipEl = null;        // element the tooltip currently describes
  let tipSize = null;      // its measured size, so moves don't re-measure
  let tipAt = [0, 0];      // where it points
  function showTip(el, html, x, y) {
    if (el !== tipEl) {
      tip.innerHTML = html;
      tip.hidden = false;
      const r = tip.getBoundingClientRect();
      tipSize = [r.width, r.height];
      tipEl = el;
    }
    moveTip(x, y);
  }
  function moveTip(x, y) {
    tipAt = [x, y];
    const [w, h] = tipSize;
    let left = x + 14, top = y + 14;
    if (left + w > window.innerWidth - 8) left = Math.max(8, x - w - 14);
    if (top + h > window.innerHeight - 8) top = Math.max(8, y - h - 14);
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  let scrim = null;        // the dimmed backdrop behind a sheet
  function hideTip() {
    tip.hidden = true;
    tipEl = null;
    tip.classList.remove("sheet");
    tip.setAttribute("role", "tooltip");
    if (scrim) scrim.hidden = true;
  }
  const tipTarget = (e) => e.target.closest("[data-tip],[data-tiptext]");
  // the details for el; link: with links (in a sheet, which can be tapped)
  function tipFor(el, link) {
    const key = el.getAttribute("data-tip");
    if (key) {
      const s = ix.cell.get(key);
      if (s && s.partial) details(s, el);
      if (s) return s.s === "e" ? describeEstimate(s, link) : describeMeasured(s, link);
    }
    const raw = el.getAttribute("data-tiptext");
    return raw ? esc(raw) : null;
  }
  // a matrix cell's estimate loads where it came from when its tooltip opens, then redraws the tooltip
  function details(s, el) {
    load(scoreUrl(s)).then((full) => {
      remember([full]);
      if (tipEl !== el || tip.hidden) return;
      if (tip.classList.contains("sheet")) openSheet(el);
      else { tipEl = null; showTip(el, tipFor(el, false), ...tipAt); }
    }, () => {});
  }
  // touch screens cannot hover: a tap opens the details as a sheet instead
  const touch = matchMedia("(pointer: coarse)");   // as the CSS
  function openSheet(el) {
    const html = tipFor(el, true);
    if (!html) return;
    const s = ix.cell.get(el.getAttribute("data-tip"));
    const fit = s && fitHref(s) ? `<a href="${fitHref(s)}">See the calibration</a>` : "";
    if (!scrim) {
      scrim = document.createElement("div");
      scrim.className = "tip-scrim";
      scrim.addEventListener("click", hideTip);
      document.body.append(scrim);
    }
    scrim.hidden = false;
    tip.innerHTML = `${html}<div class="tip-actions">${fit}<button type="button" class="btn">Close</button></div>`;
    tip.querySelector(".tip-actions button").addEventListener("click", hideTip);
    tip.classList.add("sheet");
    tip.setAttribute("role", "dialog");
    tip.style.left = tip.style.top = "";
    tip.hidden = false;
    tipEl = el;
  }
  document.addEventListener("click", (e) => {
    if (!touch.matches || tip.contains(e.target)) return;
    const el = tipTarget(e);
    if (el && !el.closest("a")) openSheet(el);   // a link inside a target (calibration cells) just navigates
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !tip.hidden) hideTip(); });
  document.addEventListener("mouseover", (e) => {
    if (touch.matches) return;   // the tap's emulated mouse events; the click opens a sheet
    const el = tipTarget(e);
    const html = el && (el === tipEl || tipFor(el));
    if (!html) return hideTip();
    showTip(el, html, e.clientX, e.clientY);
  });
  document.addEventListener("mousemove", (e) => {
    if (tipEl && tipTarget(e) === tipEl) moveTip(e.clientX, e.clientY);
  });
  document.addEventListener("focusin", (e) => {
    if (touch.matches || tip.classList.contains("sheet")) return;
    const el = tipTarget(e);
    const html = el && tipFor(el);
    if (!html) return hideTip();
    const r = el.getBoundingClientRect();
    showTip(el, html, r.left + r.width / 2, r.bottom);
  });
  document.addEventListener("focusout", () => { if (!tip.classList.contains("sheet")) hideTip(); });
  window.addEventListener("scroll", hideTip, { passive: true });

  // --- shared fragments -------------------------------------------------------
  const dot = (m) => `<span class="dot" data-p="${m.provider}" aria-hidden="true"></span>`;
  const modelLink = (m) => `<a href="${modelHref(m)}">${esc(m.name)}</a>`;
  const benchHref = (b) => `/b/${b.key}`;
  const modelHref = (m) => `/model/${encodeURIComponent(m.slug)}`;
  const mappingHref = (id) => `/calibration/${id}`;
  const confidenceFlag = (tier) => `<span class="flag ${tier}">${tier} confidence</span>`;
  const COPY_ICON = `<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 3.5v-.5A1.5 1.5 0 0 0 9 1.5H3A1.5 1.5 0 0 0 1.5 3v6A1.5 1.5 0 0 0 3 10.5h.5"/></svg>`
    + `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7"/></svg>`;
  // a page's header: eyebrow, h1 and lede, all HTML (as Pages::header)
  const pageHead = (eyebrow, h1, lede) => `<header class="page-head reveal"><div class="eyebrow">${eyebrow}</div>`
    + `<h1 class="h2">${h1}</h1>${lede ? `<p class="lede">${lede}</p>` : ""}</header>`;

  // <pre> with a copy button pinned to its top-right corner (it stays put while the block scrolls)
  const codeBox = (id, cls, inner) => `<div class="code-box"><pre class="code ${cls}" id="${id}">${inner}</pre>`
    + `<button type="button" class="code-copy" data-copy="${id}" aria-label="Copy" title="Copy">${COPY_ICON}</button></div>`;

  // copy text; on success flag the button with .done for a moment, otherwise select `fallback` for a manual copy
  async function copyText(text, button, fallback) {
    try {
      await navigator.clipboard.writeText(text);
      button.classList.add("done");
      setTimeout(() => button.classList.remove("done"), 1500);
    } catch (err) {
      getSelection().selectAllChildren(fallback);
    }
  }

  function legendHTML() {
    return `<div class="legend" aria-label="Legend">
      <span><span class="sw measured"></span>Measured score</span>
      <span><span class="sw high"></span><i>High confidence</i></span>
      <span><span class="sw medium"></span><i>Medium confidence</i></span>
      <span><span class="sw low"></span><i>Low confidence</i>: treat with caution</span>
      <span><span class="whisk"></span>± cross-validated error</span>
    </div>`;
  }

  // segmented control with a sliding pill; options are [value, label] pairs
  const segHTML = (ariaLabel, options, current) =>
    `<span class="seg instant" role="group" aria-label="${ariaLabel}"><span class="seg-thumb" aria-hidden="true"></span>${options
      .map(([k, l]) => `<button type="button" data-seg="${k}" aria-pressed="${k === current}">${l}</button>`)
      .join("")}</span>`;
  // updates itself in place (the pill slides); onPick(value) redraws only the data
  function bindSeg(seg, onPick) {
    const btns = seg.querySelectorAll("[data-seg]");
    const place = () => {
      const on = seg.querySelector('[aria-pressed="true"]');
      seg.style.setProperty("--x", on.offsetLeft + "px");
      seg.style.setProperty("--w", on.offsetWidth + "px");
    };
    new ResizeObserver(place).observe(seg);   // first callback places it; re-measures when webfonts load
    requestAnimationFrame(() => requestAnimationFrame(() => seg.classList.remove("instant")));
    btns.forEach((btn) =>
      btn.addEventListener("click", () => {
        if (btn.getAttribute("aria-pressed") === "true") return;
        btns.forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
        place();
        onPick(btn.dataset.seg);
      })
    );
  }
  // option labels: long, and short for phones
  const SHOW_OPTIONS = [["measured", "Measured only", "Measured"], ["reliable", "+ reliable estimates", "+ Reliable est."], ["all", "+ all estimates", "+ All est."]]
    .map(([k, long, short]) => [k, `<span class="lg">${long}</span><span class="sh">${short}</span>`]);
  const showSeg = () => `<span class="show-ctl"><span class="ctl-label">Show</span>${segHTML("Which scores to show", SHOW_OPTIONS, prefs.show)}</span>`;
  const bindShowSeg = (root, rerender) =>
    bindSeg(root.querySelector(".seg"), (value) => {
      prefs.show = value;
      store.set("show", prefs.show);
      rerender();
    });

  const tierFlag = (s) => (s.s === "e" && s.tier === "low" ? `<span class="flag low" title="Low-confidence estimate"><span class="lg">⚠ low conf.</span><span class="sh">⚠ low</span></span>` : "");

  // bar track: grid lines, the bar, and a ±sd whisker for estimates; X maps a fraction to %
  function trackHTML(s, X, ticks) {
    const e = s.s === "e";
    const whisker = e
      ? `<span class="whisker" style="left:${X(Math.max(0, s.v - s.sd))}%;width:${X(Math.min(1, s.v + s.sd)) - X(Math.max(0, s.v - s.sd))}%"></span>`
      : "";
    return `${ticks.map((t) => `<span class="grid" style="left:${X(t)}%"></span>`).join("")}<span class="bar ${e ? "e " + s.tier : "m"}" style="width:${X(s.v)}%"></span>${whisker}`;
  }

  // --- page: leaderboard --------------------------------------------------------
  // phones: the benchmarks of b's capability as a swipeable row of chips (none if b is alone in it)
  // a benchmark's chip, current if it is b
  // a benchmark's model counts: measured, plus estimated if any
  const benchCount = (x) => `${x.n_measured}${x.n_estimated ? "+" + x.n_estimated : ""}`;
  const benchChip = (x, b) => `<a class="chip" href="${benchHref(x)}" aria-current="${x.id === b.id}">${esc(x.label)}<span class="cnt">${benchCount(x)}</span></a>`;
  const byMeasured = (p, q) => q.n_measured - p.n_measured || p.label.localeCompare(q.label);
  const MAX_CHIPS = 8;  // chips per capability on the leaderboard picker and the phones' rail
  // a capability's chips: up to MAX_CHIPS, the original benchmarks first, then the most measured,
  // and the open benchmark b if it belongs to the capability but is not among them
  function capChips(cap, benches, b) {
    const chips = [...benches.filter((x) => x.featured), ...benches.filter((x) => !x.featured).sort(byMeasured)].slice(0, MAX_CHIPS);
    if (b.capability === cap && !chips.includes(b)) chips.push(b);
    return chips;
  }
  let pickerExtra = null;   // the open benchmark, if the picker drew a chip just for it
  // each capability's chips; the rest folds into one "+N more" list
  function pickerHTML(b) {
    pickerExtra = null;
    return `<nav class="picker" aria-label="Benchmarks">${ix.benchesByCap.map(({ cap, benches }) => {
      const chips = capChips(cap.id, benches, b);
      if (chips.length > MAX_CHIPS) pickerExtra = b;
      const rest = benches.filter((x) => !chips.includes(x)).sort(byMeasured);
      const more = rest.length ? `<button type="button" class="chip more-btn" aria-expanded="false" aria-controls="more-${esc(cap.id)}">+${rest.length} more</button>
        <div class="more-list" id="more-${esc(cap.id)}" hidden><p class="more-note">By models measured · faint ones have fewer than 5</p>${rest
          .map((x) => `<a class="more-item${x.n_measured < 5 ? " few" : ""}" href="${benchHref(x)}" aria-current="${x.id === b.id}"><span>${esc(x.label)}</span><span class="cnt">${benchCount(x)}</span></a>`)
          .join("")}</div>` : "";
      return `<div class="picker-row"><div class="cap">${esc(cap.label)}</div><div class="chips">${chips.map((x) => benchChip(x, b)).join("")}${more}</div></div>`;
    }).join("")}</nav>`;
  }
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const EASE = "cubic-bezier(.2, .7, .2, 1)";
  // animates el between keyframes, clipped (.animating) meanwhile so a changing height hides overflow
  function ease(el, frames, ms, done) {
    el.classList.add("animating");
    const a = el.animate(frames, { duration: ms, easing: EASE });
    a.onfinish = () => { el.classList.remove("animating"); if (done) done(); };
    return a;
  }
  // a "+N more" button opens its list and closes any other; Escape closes it.
  // The list grows from (or shrinks to) nothing, so the rows below slide instead of jumping.
  const BOX = ["height", "paddingTop", "paddingBottom", "marginTop", "marginBottom"];
  function toggleList(list, open) {
    if (list.anim) list.anim.finish();  // a click mid-animation: settle the last one first
    if (open === !list.hidden) return;
    if (reduceMotion.matches) { list.hidden = !open; return; }
    list.hidden = false;
    const cs = getComputedStyle(list);
    const full = { opacity: 1, transform: "none" };
    BOX.forEach((k) => { full[k] = cs[k]; });
    const none = { opacity: 0, transform: "translateY(-4px)" };
    BOX.forEach((k) => { none[k] = "0px"; });
    list.anim = ease(list, open ? [none, full] : [full, none], open ? 240 : 180, () => {
      list.anim = null;
      if (!open) list.hidden = true;
    });
  }
  // replaces el's content: the old fades out, then the new fades in while el eases to its new height
  function swapContent(el, html) {
    const id = (el.swapId = (el.swapId || 0) + 1);  // a newer swap supersedes this one
    if (reduceMotion.matches) { el.innerHTML = html; return; }
    el.getAnimations().forEach((a) => a.cancel());
    const from = el.offsetHeight;
    const out = el.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(4px)" }],
      { duration: 130, easing: "ease-in", fill: "forwards" });
    out.onfinish = () => {
      if (id !== el.swapId) return;
      el.innerHTML = html;
      out.cancel();
      const to = el.offsetHeight;
      ease(el, [{ opacity: 0, transform: "translateY(6px)", height: from + "px" }, { opacity: 1, transform: "none", height: to + "px" }], 280);
    };
  }
  const setMore = (btn, open) => {
    btn.setAttribute("aria-expanded", String(open));
    toggleList(document.getElementById(btn.getAttribute("aria-controls")), open);
  };
  const closeMore = (except) => main.querySelectorAll('.more-btn[aria-expanded="true"]').forEach((btn) => {
    if (btn !== except) setMore(btn, false);
  });
  document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest(".more-btn");
    if (!btn) return;
    const open = btn.getAttribute("aria-expanded") !== "true";
    closeMore(btn);
    setMore(btn, open);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMore(); });
  // phones: the open benchmark's capability as a swipeable row of the same chips as the picker
  function railHTML(b) {
    const benches = capChips(b.capability, ix.byCap.get(b.capability) || [], b);
    if (benches.length < 2) return "";
    return `<p class="rail-cap">Also in <b>${esc(capLabel(b.capability))}</b></p><nav class="rail chip-row" aria-label="${esc(capLabel(b.capability))} benchmarks">${benches
      .map((x) => benchChip(x, b))
      .join("")}</nav>`;
  }
  // scrolls a row of pills (phones only) so its current one is in the middle
  const phone = matchMedia("(max-width: 760px)");
  function centerIn(row, selector) {
    const chip = phone.matches && row && row.querySelector(selector);
    if (chip) row.scrollLeft += chip.getBoundingClientRect().left - row.getBoundingClientRect().left - (row.clientWidth - chip.offsetWidth) / 2;
  }
  const centerRail = () => centerIn($("#bench-rail .rail"), '[aria-current="true"]');

  // A leaderboard; data: its scores (data/b/...). Returns true when only the chart was swapped (already on the leaderboard).
  function renderBoard(key, data) {
    const b = ix.benchByKey.get(key);
    if (!b || !data) return renderNotFound(`No benchmark “${esc(key)}”.`);
    if (!docs.has(boardUrl(key))) docs.set(boardUrl(key), Promise.resolve(data));   // the home page's, under its own name too
    const scores = remember(data.scores);
    if (key === ix.home) setMeta("LLM Benchmark Leaderboard with Estimated Scores",
      "LLM benchmark scores: measured where available, estimated where missing, with every estimate's error and confidence.", "/");
    else setMeta(`${b.label} leaderboard`, `${b.label} leaderboard: ${b.n_measured} measured and ${b.n_estimated} estimated LLM scores, each estimate with its error and confidence.`);
    if ($("#board-sec")) {
      // the picker is redrawn only when the open benchmark needs a chip of its own or leaves one
      if (pickerExtra || !main.querySelector(`.picker .chip[href="${benchHref(b)}"]`)) $(".picker").outerHTML = pickerHTML(b);
      else {
        main.querySelectorAll('.picker [aria-current="true"]').forEach((a) => a.setAttribute("aria-current", "false"));
        main.querySelectorAll(`.picker [href="${benchHref(b)}"]`).forEach((a) => a.setAttribute("aria-current", "true"));
        closeMore();
      }
      $("#bench-select").value = b.key;
      $("#bench-rail").innerHTML = railHTML(b);
      renderBoardBody(b, scores);
      centerRail();
      return true;
    }
    const c = D.meta.counts;
    const t = c.confidence;
    const tot = Math.max(1, t.high + t.medium + t.low);
    const hero = `
      <section class="hero reveal">
        <div>
          <div class="eyebrow">LLM benchmark leaderboard · gaps filled</div>
          <h1 class="display" style="margin-top:.9rem">Mind the <em>gap</em>.</h1>
          <p class="lede">${esc(ABOUT)} Estimates are hatched and italic; low-confidence ones are flagged.</p>
        </div>
        <div class="ledger">
          <div><div class="n">${c.models}</div><div class="k">models</div></div>
          <div><div class="n">${c.benchmarks}</div><div class="k">benchmark versions</div></div>
          <div><div class="n">${c.measured}</div><div class="k">measured scores</div></div>
          <div><div class="n est">${c.estimated}</div><div class="k">estimated scores</div>
            <div class="tierbar" aria-hidden="true">
              <span class="t-high" style="width:${(t.high / tot) * 100}%"></span>
              <span class="t-medium" style="width:${(t.medium / tot) * 100}%"></span>
              <span class="t-low" style="width:${(t.low / tot) * 100}%"></span>
            </div>
            <div class="tierkey"><span><b>${t.high}</b> high</span><span><b>${t.medium}</b> medium</span><span><b>${t.low}</b> low confidence</span></div>
          </div>
        </div>
      </section>`;

    const picker = pickerHTML(b);

    const pickerMobile = `<div class="picker-mobile"><label><span class="ctl-label">Benchmark · ${D.meta.counts.benchmarks} to pick from</span><select class="select" id="bench-select">${b.listed ? "" : `<option value="${esc(b.key)}" selected>${esc(b.label)}</option>`}${ix.benchesByCap
      .map(({ cap, benches }) => `<optgroup label="${esc(cap.label)}">${benches
        .map((x) => `<option value="${esc(x.key)}" ${x.id === b.id ? "selected" : ""}>${esc(x.label)} (${x.n_measured}${x.n_estimated ? " + " + x.n_estimated + " est." : ""})</option>`)
        .join("")}</optgroup>`)
      .join("")}</select></label><div id="bench-rail">${railHTML(b)}</div></div>`;

    main.innerHTML = `<div class="page">${hero}${picker}${pickerMobile}<section id="board-sec"></section></div>`;
    $("#bench-select").addEventListener("change", (e) => go(benchHref({ key: e.target.value })));
    renderBoardBody(b, scores);
    centerRail();
    return false;
  }

  // "As of …, the highest measured score on X is …": the leaderboard's lede (Pages::benchmarkLead)
  function benchLead(b, scores) {
    const top = scores.filter((s) => s.s === "m").sort((p, q) => q.v - p.v)[0];
    const n = b.n_estimated;
    return [
      top ? `As of ${D.meta.retrieved_at}, the highest measured score on ${b.label} is ${pct(top.v)}% by ${ix.model.get(top.m).name}.` : "",
      n ? `${n === 1 ? "1 more model has an estimated score" : `${n} more models have estimated scores`}, calibrated from the benchmarks they were measured on.` : "",
    ].filter(Boolean).join(" ");
  }

  // the model page's lede (Pages::modelLead)
  function modelLead(m) {
    return `As of ${D.meta.retrieved_at}, ${m.name} (${D.meta.providers[m.provider] || m.provider}) has measured scores on ${m.n_measured} benchmark${m.n_measured === 1 ? "" : "s"}`
      + (m.n_estimated ? ` and estimated scores on ${m.n_estimated} more` : "") + ".";
  }

  // header, controls and legend for one benchmark and its scores; the rows live in #board-rows
  function renderBoardBody(b, all) {
    const sec = $("#board-sec");
    const nEst = b.n_estimated;
    const nLow = all.filter((s) => s.s === "e" && s.tier === "low").length;
    sec.innerHTML = `
      <div class="board-head">
        <div>
          <div class="eyebrow">${esc(capLabel(b.capability))}</div>
          <h2 class="h2" style="margin-top:.4rem">${esc(b.label)}</h2>
        </div>
        <div class="src">${b.n_measured} measured · <i>${nEst} estimated</i>${nLow ? ` (${nLow} low confidence)` : ""}
          ${b.source_url ? ` · source: <a href="${esc(b.source_url)}" rel="noopener" target="_blank">${esc(host(b.source_url))}</a>` : ""}</div>
      </div>
      <p class="lede board-lead">${esc(benchLead(b, all))}</p>
      <div class="controls">${showSeg()}</div>
      ${legendHTML()}
      <div id="board-rows"></div>
      ${nEst === 0 ? `<p class="muted" style="margin-top:1rem">No estimates for this benchmark: no same-capability benchmark calibrates it well enough (see <a href="/calibration">Calibration</a>).</p>` : ""}`;
    bindShowSeg(sec, () => renderBoardRows(b, all));
    renderBoardRows(b, all);
  }

  // a 0..max axis for values up to hi, max rounded up to a tenth, with its ticks
  function axis(hi, lo = 0) {
    const min = Math.max(0, Math.floor(lo * 10) / 10), max = Math.min(1, Math.ceil(hi * 10) / 10);
    const step = max - min > 0.5 ? 0.1 : max - min > 0.2 ? 0.05 : 0.02, ticks = [];
    for (let v = min; v <= max + 1e-9; v += step) ticks.push(v);
    return { min, max, ticks };
  }

  // a leaderboard longer than TAIL.min_rows folds where the scores drop below TAIL.below (never
  // before row TAIL.min_rows), if more than TAIL.more_below rows score below it; one longer than
  // TAIL.long_rows shows at most its top TAIL.long_share. The first hidden rows fade out above a
  // "Show N more" button
  const TAIL = { min_rows: 25, below: 0.15, more_below: 5, long_rows: 120, long_share: 1 / 3 };
  let tailOpen = null;   // the benchmark whose folded tail is open
  // [index of the first folded row, whether the TAIL.below score set it], or null: no fold
  function tailCut(rows) {
    if (rows.length <= TAIL.min_rows) return null;
    const low = rows.findIndex((s) => s.v < TAIL.below);
    const byLow = low >= 0 && rows.length - low > TAIL.more_below;   // rows are sorted: all from low on are below
    let cut = byLow ? Math.max(low, TAIL.min_rows) : rows.length, byScore = byLow;
    if (rows.length > TAIL.long_rows && Math.ceil(rows.length * TAIL.long_share) < cut) {
      cut = Math.ceil(rows.length * TAIL.long_share);
      byScore = false;
    }
    return cut < rows.length ? [cut, byScore] : null;
  }

  function renderBoardRows(b, scores) {
    const rows = scores.filter(visible).sort((p, q) => q.v - p.v);
    const hi = Math.max(0.1, ...rows.map((s) => s.v + (s.s === "e" ? s.sd : 0)));
    const { max: axisMax, ticks } = axis(hi);
    const X = (v) => Math.max(0, Math.min(100, (v / axisMax) * 100));

    const rowHTML = (s, i) => {
        const m = ix.model.get(s.m);
        const est = s.s === "e";
        const rank = est ? `≈${i + 1}` : String(i + 1);
        return `<div class="row ${est ? "e " + s.tier : "m"}" data-p="${m.provider}">
          <div class="rank ${est ? "est" : ""}">${rank}</div>
          <div class="who">${dot(m)}${modelLink(m)}${tierFlag(s)}</div>
          <div class="track" data-tip="${s.m}:${s.b}" tabindex="0" aria-label="${esc(m.name)}: ${est ? "estimated " : ""}${pct(s.v)} percent">${trackHTML(s, X, ticks)}</div>
          <div class="val">${est ? "≈" : ""}${pct(s.v)}%${est ? `<span class="pm">±${pct(s.sd)}</span>` : ""}</div>
        </div>`;
    };
    const tail = tailCut(rows);
    const open = tailOpen === b.id;
    let body;
    if (!tail) body = rows.map(rowHTML).join("");
    else {
      const [cut, byScore] = tail;
      const more = `Show ${rows.length - cut} more${byScore ? ` · scoring below ${Math.round(TAIL.below * 100)}%` : ""}`;
      body = rows.slice(0, cut).map(rowHTML).join("")
        + `<div class="board-tail${open ? "" : " folded"}" id="board-tail">${rows.slice(cut).map((s, i) => rowHTML(s, cut + i)).join("")}</div>
          <div class="tail-ctl"><button type="button" class="btn tail-btn" aria-controls="board-tail" aria-expanded="${open}"
            data-more="${more}">${open ? "Show fewer" : more}</button></div>`;
    }

    hideTip();
    $("#board-rows").innerHTML = `
      <div class="board" role="list">
        <div class="axis" aria-hidden="true"><span></span><span></span>
          <div class="ticks">${ticks.map((t) => `<span style="left:${X(t)}%">${Math.round(t * 100)}</span>`).join("")}</div><span></span></div>
        ${body || `<p class="empty">No scores to show with the current filter.</p>`}
      </div>`;
    const btn = $("#board-rows .tail-btn");
    if (btn) btn.addEventListener("click", () => toggleTail(b, btn));
  }
  // unfolds (or folds back) the tail, easing its height between the faded peek and all its rows
  function toggleTail(b, btn) {
    const tail = $("#board-tail");
    const open = btn.getAttribute("aria-expanded") !== "true";
    tailOpen = open ? b.id : null;
    btn.setAttribute("aria-expanded", String(open));
    btn.textContent = open ? "Show fewer" : btn.dataset.more;
    const from = tail.offsetHeight;
    tail.classList.toggle("folded", !open);
    const to = tail.offsetHeight;
    if (!open) btn.scrollIntoView({ block: "nearest" });
    if (!reduceMotion.matches) ease(tail, [{ height: from + "px" }, { height: to + "px" }], open ? 420 : 300);
  }

  // --- page: matrix -----------------------------------------------------------

  // a matrix cell's kind (src/Site.php CELL_KINDS): measured, or an estimate's confidence
  const CELL_TIERS = [null, "high", "medium", "low"];
  let mxd = null;   // the matrix's cells by model, and each benchmark's range of measured scores (for the tint)

  // data: every listed benchmark's cells (data/matrix.json), as [model, benchmark, value, kind]; an
  // estimate's cell is partial: where it came from loads when its tooltip opens (details())
  function renderMatrix(_, data) {
    setMeta("LLM benchmark score matrix", "Every model on every benchmark: measured LLM scores and calibrated estimates for the missing ones, side by side.");
    const cells = remember(data.cells.map(([m, b, v, k]) => (k ? { m, b, v, s: "e", tier: CELL_TIERS[k], partial: true } : { m, b, v, s: "m" })));
    const range = new Map();
    for (const c of cells) {
      if (c.s !== "m") continue;
      const r = range.get(c.b);
      if (!r) range.set(c.b, [c.v, c.v]);
      else { r[0] = Math.min(r[0], c.v); r[1] = Math.max(r[1], c.v); }
    }
    mxd = { byModel: groupBy(cells, (c) => c.m), range };
    mx = null;
    main.innerHTML = `<div class="page">
      ${pageHead("Score matrix", "Every model × every benchmark", `Measured cells are tinted by score within each column. Hatched italic cells are estimates;
        a red corner marks a <b>low-confidence</b> estimate. Dots are gaps that stay gaps: no calibrated source to estimate from.
        Click a column header to rank by that benchmark.`)}
      <div class="controls">
        ${showSeg()}
        <label class="check"><input type="checkbox" id="dense" ${prefs.dense ? "checked" : ""}> Dense core only
          <span class="muted" data-tiptext="Hides benchmarks with fewer than ${D.meta.dense.min_models} measured models and models measured on fewer than ${D.meta.dense.min_benchmarks} benchmarks, peeled repeatedly until both hold. Display only.">ⓘ</span></label>
        <span class="muted mono" style="font-size:.78rem" id="mx-counts"></span>
      </div>
      ${legendHTML()}
      <p class="mx-hint"><span>Model names stay put; the scores scroll.</span><b>Swipe →</b></p>
      <div class="matrix-wrap" id="mx-wrap" style="margin-top:1rem"></div></div>`;

    bindShowSeg(main, renderMatrixTable);
    $("#dense").addEventListener("change", (e) => { prefs.dense = e.target.checked; store.set("dense", prefs.dense); renderMatrixTable(); });
    // column headers are re-created on every redraw, so sorting is delegated
    const wrap = $("#mx-wrap");
    // new rows as the box scrolls, at most once a frame
    let ticking = false;
    wrap.addEventListener("scroll", () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => { ticking = false; paintMatrix(false); });
    }, { passive: true });
    const sortBy = (el) => { const id = Number(el.dataset.sort); prefs.sortCol = prefs.sortCol === id ? null : id; renderMatrixTable(); };
    wrap.addEventListener("click", (e) => { const el = e.target.closest("[data-sort]"); if (el) sortBy(el); });
    wrap.addEventListener("keydown", (e) => {
      const el = e.target.closest("[data-sort]");
      if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortBy(el); }
    });
    renderMatrixTable();
  }

  // The matrix draws only the cells in view (plus a margin) inside its scroll box: tens of
  // thousands of cells at once make every toggle, sort and scroll stall. Spacer rows and cells
  // keep the scroll size; rows and columns each have one fixed size, so position = index × size.
  // It redraws only when the view nears the edge of what is drawn, not on every scrolled frame.
  const MX_ROWS = 24, MX_COLS = 10;   // rows and columns drawn beyond each edge of the view
  const MX_EDGE = 6;                  // redraw when the view comes this close to the drawn edge
  let mx = null;   // the table's models, benchmarks, cell sizes and the drawn window

  function renderMatrixTable() {
    const benches = ix.listed.filter((b) => !prefs.dense || b.dense);
    let models = D.models.filter((m) => !prefs.dense || m.dense);

    const cellOf = (m, b) => {
      const s = ix.cell.get(m.id + ":" + b.id);
      return s && visible(s) ? s : null;
    };
    // one pass over each model's own scores: the counts (over every cell, drawn or not), and
    // which models have something to show here (with this "Show" setting): the others get no row
    const drawn = new Set(benches.map((b) => b.id));
    let shown = 0, est = 0, low = 0;
    models = models.filter((m) => {
      let any = false;
      for (const s of mxd.byModel.get(m.id) || []) {
        if (!drawn.has(s.b) || !visible(s)) continue;
        any = true; shown++;
        if (s.s === "e") { est++; if (s.tier === "low") low++; }
      }
      return any;
    });
    if (prefs.sortCol && benches.some((b) => b.id === prefs.sortCol)) {
      const b = ix.bench.get(prefs.sortCol);
      models = models.slice().sort((p, q) => {
        const a = cellOf(p, b), c = cellOf(q, b);
        return (c ? c.v : -1) - (a ? a.v : -1) || p.name.localeCompare(q.name);
      });
    } else {
      models = models.slice().sort((p, q) => q.n_measured - p.n_measured || p.name.localeCompare(q.name));
    }

    const gaps = models.length * benches.length - shown;
    hideTip();
    $("#mx-counts").innerHTML = `${models.length} models · ${benches.length} benchmarks · ${shown - est} measured · <i>${est} estimated</i> (${low} low confidence) · ${gaps} gaps`;

    mx = { models, benches, cellOf, rowH: mx ? mx.rowH : 0, colW: mx ? mx.colW : 0, nameW: 0, win: null };
    const wrap = $("#mx-wrap");
    wrap.innerHTML = `<table class="mx"><thead></thead><tbody></tbody></table>`;
    paintMatrix(true);
  }

  // a capability's first column gets a heavier left border
  const mxBrk = (benches, j) => (j > 0 && benches[j].capability !== benches[j - 1].capability ? " brk" : "");
  // spacer cells for columns not drawn
  const mxPadCells = (n, colW, tag) => (n > 0 ? `<${tag} class="mx-padc" colspan="${n}" style="width:${n * colW}px;min-width:${n * colW}px"></${tag}>` : "");

  function matrixHead(c0, c1) {
    const { benches, colW } = mx;
    // the capability spans of the drawn columns
    const spans = [];
    for (let j = c0; j < c1; j++) {
      const last = spans[spans.length - 1];
      if (last && last.cap === benches[j].capability) last.n++;
      else spans.push({ cap: benches[j].capability, n: 1, j });
    }
    return `<tr class="caps"><th class="corner" rowspan="2">Model</th>${mxPadCells(c0, colW, "th")}${spans
        .map((sp) => `<th colspan="${sp.n}" class="${mxBrk(benches, sp.j).trim()}" title="${esc(capLabel(sp.cap))}"><span class="cap-l">${esc(capLabel(sp.cap))}</span></th>`)
        .join("")}${mxPadCells(benches.length - c1, colW, "th")}</tr>
      <tr class="cols">${mxPadCells(c0, colW, "th")}${benches.slice(c0, c1)
        .map((b, k) => `<th class="${prefs.sortCol === b.id ? "sorted" : ""}${mxBrk(benches, c0 + k)}"><span class="colh" data-sort="${b.id}" role="button" tabindex="0" title="Sort by ${esc(b.label)}">${esc(b.label)}</span></th>`)
        .join("")}${mxPadCells(benches.length - c1, colW, "th")}</tr>`;
  }

  function matrixRow(m, c0, c1) {
    const { benches, cellOf, colW } = mx;
    let tds = "";
    for (let j = c0; j < c1; j++) {
      const b = benches[j], s = cellOf(m, b), brk = mxBrk(benches, j);
      if (!s) tds += `<td class="gap${brk}">·</td>`;
      else if (s.s === "e") tds += `<td class="e ${s.tier}${brk}" data-tip="${m.id}:${b.id}" tabindex="0">${pct(s.v, 0)}</td>`;
      else {
        const [lo, hi] = mxd.range.get(b.id);
        const h = hi > lo ? (s.v - lo) / (hi - lo) : 0.5;
        tds += `<td class="m${brk}" style="--h:${(0.15 + h * 0.85).toFixed(2)}" data-tip="${m.id}:${b.id}">${pct(s.v, 0)}</td>`;
      }
    }
    return `<tr><th scope="row"><a href="${modelHref(m)}" title="${esc(m.name)}">${dot(m)}<span>${esc(m.name)}</span></a></th>${mxPadCells(c0, colW, "td")}${tds}${mxPadCells(benches.length - c1, colW, "td")}</tr>`;
  }

  // draws the cells around the view, if the view has come near the edge of what is drawn (or force)
  function paintMatrix(force) {
    const wrap = $("#mx-wrap"), table = wrap && wrap.querySelector("table");
    if (!mx || !table) return;
    const nR = mx.models.length, nC = mx.benches.length;
    // first guesses; the first drawing measures them
    const rowH = mx.rowH || (phone.matches ? 41 : 31), colW = mx.colW || (phone.matches ? 44 : 46);
    const head = table.tHead, nameW = mx.nameW || (phone.matches ? 136 : 230);
    const top = Math.max(0, wrap.scrollTop - head.offsetHeight);
    const left = Math.max(0, wrap.scrollLeft);
    // the rows and columns in view
    const r0 = Math.floor(top / rowH), r1 = Math.ceil((top + wrap.clientHeight) / rowH);
    const v0 = Math.floor(left / colW), v1 = Math.ceil((left + wrap.clientWidth - nameW) / colW);
    const w = mx.win;
    const inside = w && r0 >= w.r0 + (w.r0 > 0 ? MX_EDGE : 0) && r1 <= w.r1 - (w.r1 < nR ? MX_EDGE : 0)
      && v0 >= w.c0 + (w.c0 > 0 ? MX_EDGE / 2 : 0) && v1 <= w.c1 - (w.c1 < nC ? MX_EDGE / 2 : 0);
    if (!force && inside) return;
    const win = {
      r0: Math.max(0, r0 - MX_ROWS), r1: Math.min(nR, r1 + MX_ROWS),
      c0: Math.max(0, v0 - MX_COLS), c1: Math.min(nC, v1 + MX_COLS),
    };
    const sameCols = w && w.c0 === win.c0 && w.c1 === win.c1;
    mx.win = win;
    if (force || !sameCols) head.innerHTML = matrixHead(win.c0, win.c1);
    const pad = (rows) => (rows > 0 ? `<tr class="mx-pad" aria-hidden="true"><td colspan="${nC + 1}" style="height:${rows * rowH}px"></td></tr>` : "");
    table.tBodies[0].innerHTML = pad(win.r0) + mx.models.slice(win.r0, win.r1).map((m) => matrixRow(m, win.c0, win.c1)).join("") + pad(nR - win.r1);
    if (tipEl && !tipEl.isConnected) hideTip();
    if (!force) return;
    // a full drawing measures the real sizes (scrolling never changes them); draw again if the guess was off
    const row = table.tBodies[0].querySelector("tr:not(.mx-pad)"), cell = row && row.querySelector("td:not(.mx-padc)");
    mx.nameW = head.querySelector(".corner").offsetWidth;
    // the capability labels stop at the model column's edge while the columns scroll under it
    wrap.style.setProperty("--name-w", mx.nameW + "px");
    const off = row && (Math.abs(row.offsetHeight - rowH) > 0.5 || Math.abs(cell.offsetWidth - colW) > 0.5);
    mx.rowH = row ? row.offsetHeight : rowH;
    mx.colW = cell ? cell.offsetWidth : colW;
    if (off) paintMatrix(true);
  }

  // --- page: model ------------------------------------------------------------
  // data: the model's scores (data/model/...)
  function renderModel(slug, data) {
    const m = ix.modelBySlug.get(slug);
    if (!m || !data) return renderNotFound(`No model “${esc(slug)}”.`);
    setMeta(`${m.name} benchmark scores`, `${m.name} benchmark scores: measured on ${m.n_measured} benchmark${m.n_measured === 1 ? "" : "s"}`
      + (m.n_estimated ? `, estimated on ${m.n_estimated} more, with the error and confidence of each estimate.` : "."));
    const scores = remember(data.scores);   // on the listed benchmarks
    const byB = new Map(scores.map((s) => [s.b, s]));
    const est = scores.filter((s) => s.s === "e");
    const nTier = (t) => est.filter((s) => s.tier === t).length;
    const blocks = ix.benchesByCap
      .map(({ cap, benches }) => {
        const rows = benches
          .map((b) => {
            const s = byB.get(b.id);
            if (!s) {
              return `<div class="mrow gap"><div class="bn"><a href="${benchHref(b)}">${esc(b.label)}</a></div><div class="track">not measured · no calibrated source to estimate from</div><div class="val">—</div></div>`;
            }
            const e = s.s === "e";
            const fit = fitHref(s) ? ` · <a href="${fitHref(s)}">see the fit</a>` : "";
            const why = e
              ? `<div class="why">${confidenceFlag(s.tier)} estimated from ${sourceList(s, true)} via ${esc(methodLabel(s.method))}, ± ${pct(s.sd)} pp${fit}${
                  s.why.length ? `<span>· ${s.why.map(esc).join("; ")}</span>` : ""
                }</div>`
              : "";
            return `<div class="mrow ${e ? "e" : "m"}" data-p="${m.provider}">
              <div class="bn"><a href="${benchHref(b)}">${esc(b.label)}</a></div>
              <div class="track" data-tip="${m.id}:${b.id}" tabindex="0">${trackHTML(s, (v) => v * 100, [0.25, 0.5, 0.75])}</div>
              <div class="val">${e ? "≈" : ""}${pct(s.v)}%</div>${why}</div>`;
          })
          .join("");
        const nHere = benches.filter((b) => byB.has(b.id)).length;
        return `<section class="cap-block"><h3 class="h3">${esc(cap.label)} <span class="muted mono" style="font-weight:400;font-size:.78rem">${nHere}/${benches.length}</span></h3>${rows}</section>`;
      })
      .join("");

    main.innerHTML = `<div class="page">
      <header class="page-head reveal">
        <div class="eyebrow">${esc(D.meta.providers[m.provider] || m.provider)} · model</div>
        <div class="model-head">
          <h1 class="h2 who" data-p="${m.provider}">${dot(m)}${esc(m.name)}</h1>
          <dl class="kv">
            <dt>measured</dt><dd>${m.n_measured} benchmarks</dd>
            <dt>estimated</dt><dd><i>${est.length}</i> (${nTier("high")} high, ${nTier("medium")} medium, ${nTier("low")} low confidence)</dd>
          </dl>
        </div>
        <p class="lede">${esc(modelLead(m))}</p>
      </header>
      ${legendHTML()}
      ${blocks}
    </div>`;
  }

  // --- page: calibration --------------------------------------------------------
  // green -> amber -> red over 0..gate pp of LOO error (same stops as the legend ramp)
  const LOSS_STOPS = [[0, [16, 185, 129]], [0.5, [245, 158, 11]], [1, [239, 68, 68]]];
  const LOSS_RAMP = `linear-gradient(90deg, ${LOSS_STOPS.map(([t, c]) => `rgb(${c.join(",")}) ${t * 100}%`).join(", ")})`;
  function lossColor(lossPP, gate) {
    const t = Math.max(0, Math.min(lossPP / gate, 1));
    const i = Math.max(1, LOSS_STOPS.findIndex(([t1]) => t <= t1));
    const [t0, c0] = LOSS_STOPS[i - 1], [t1, c1] = LOSS_STOPS[i];
    const u = (t - t0) / (t1 - t0);
    return `rgb(${c0.map((a, k) => Math.round(a + (c1[k] - a) * u)).join(",")})`;
  }

  // data: the calibrations between listed benchmarks (data/calibration.json)
  // a row of capability chips over items, All first: byCap (items by capability id, and
  // "all"), caps ({ id, label, n }), html(current) and bind(onPick), which presses the clicked chip
  function capFilter(items, capOfItem) {
    const byCap = groupBy(items, capOfItem);
    byCap.set("all", items);
    const caps = [{ id: "all", label: "All" }, ...D.capabilities.filter((c) => byCap.has(c.id))].map((c) => ({ ...c, n: byCap.get(c.id).length }));
    const html = (current) => `<div class="pm-caps chip-row" role="group" aria-label="Capability">${caps
      .map((c) => `<button type="button" class="chip" data-cap="${c.id}" aria-pressed="${c.id === current}">${esc(c.label)}<span class="cnt">${c.n}</span></button>`).join("")}</div>`;
    const bind = (onPick) => main.querySelectorAll(".pm-caps .chip").forEach((c) => c.addEventListener("click", () => {
      main.querySelectorAll(".pm-caps .chip").forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
      hideTip();
      onPick(c.dataset.cap);
    }));
    return { byCap, caps, html, bind };
  }

  function renderCalibration(_, data) {
    setMeta("LLM benchmark calibrations", "Which LLM benchmarks predict which: the fitted cross-benchmark calibrations behind every estimate, with their errors.");
    const gate = D.meta.quality_gate.max_loo_pp;
    // one capability at a time (the one with the most mappings first)
    const maps = data.mappings;
    const byPair = new Map(maps.map((m) => [m.from + ":" + m.to, m]));
    const chips = capFilter(maps, (m) => capOf(m.from)), nByCap = chips.byCap, caps = chips.caps;
    const capOrder = new Map(D.capabilities.map((c, i) => [c.id, i]));
    const capName = (cap) => (cap === "all" ? "All capabilities" : capLabel(cap));
    // the cross-domain fits (data/cross.json), by "from:to"; loaded after the page
    let cross = null;
    const LIST_ROWS = 20;  // mappings listed before "Show all"

    // a source x target table of rows ({ id, label }) with cell(src, dst, pair) for each cell
    const pmTable = (rows, cell) => `<table class="pm"><thead><tr><th style="text-align:right;vertical-align:bottom" class="muted">source ↓ · target →</th>${rows
      .map((r) => `<th><span class="colh">${esc(r.label)}</span></th>`).join("")}</tr></thead><tbody>${rows
      .map((src) => `<tr><th scope="row">${esc(src.label)}</th>${rows.map((dst) => cell(src, dst, `${esc(src.label)} → ${esc(dst.label)}`)).join("")}</tr>`)
      .join("")}</tbody></table>`;

    function pairMap(cap) {
      const involved = new Set();
      nByCap.get(cap).forEach((m) => { involved.add(m.from); involved.add(m.to); });
      // grouped by capability (stable: within one, the site's order)
      const vs = D.benchmarks.filter((b) => involved.has(b.id)).sort((a, b) => capOrder.get(a.capability) - capOrder.get(b.capability));
      return pmTable(vs, (src, dst, pair) => {
        if (src === dst) return `<td class="diag"></td>`;
        if (src.capability !== dst.capability) {
          const x = cross?.get(src.id + ":" + dst.id);
          if (x?.passes && x.loo != null) return `<td class="cell cross" style="background:${lossColor(x.loo * 100, gate)}" data-tiptext="${pair}: cross-domain, ${esc(methodLabel(x.method))}, n=${x.n}, R²=${x.r2.toFixed(2)}, LOO error ${pct(x.loo)} pp (shown to compare, never used for estimates)">${pct(x.loo)}</td>`;
          return `<td data-tiptext="${pair}: ${x ? "the cross-domain fit fails the quality gate" : "different capabilities, too few shared models"}"></td>`;
        }
        const m = byPair.get(src.id + ":" + dst.id);
        if (!m) return `<td class="none" data-tiptext="${pair}: no usable mapping (too few shared models, or the best fit failed the quality gate)"></td>`;
        return `<td class="cell" style="background:${lossColor(m.loo * 100, gate)}"><a href="${mappingHref(m.id)}" data-tiptext="${pair}: ${esc(methodLabel(m.method))}, n=${m.n}, R²=${m.r2.toFixed(2)}, LOO error ${pct(m.loo)} pp, used for ${m.n_used} estimates">${pct(m.loo)}</a></td>`;
      });
    }
    function mapList(cap, all) {
      const rows = nByCap.get(cap).slice().sort((a, b) => a.loo - b.loo);
      const html = (all ? rows : rows.slice(0, LIST_ROWS))
        .map((m) => {
          const f = ix.bench.get(m.from), t = ix.bench.get(m.to);
          return `<tr><td><a href="${mappingHref(m.id)}">${esc(f.label)} → ${esc(t.label)}</a></td><td>${esc(methodLabel(m.method))}</td>
            <td class="num">${m.n}</td><td class="num">${m.r2.toFixed(3)}</td><td class="num err"><span class="scale-dot" style="background:${lossColor(m.loo * 100, gate)}"></span>${pct(m.loo)}</td><td class="num">${m.n_used}</td></tr>`;
        })
        .join("");
      return `<table class="list maps"><thead><tr><th>Mapping</th><th>Selected curve</th><th style="text-align:right">n</th><th style="text-align:right">R²</th><th style="text-align:right">LOO error (pp)</th><th style="text-align:right">Estimates</th></tr></thead><tbody>${html}</tbody></table>`
        + (!all && rows.length > LIST_ROWS ? `<button type="button" class="btn more-maps">Show all ${rows.length} mappings</button>` : "");
    }

    // capability x capability: the median LOO error of the cross-domain fits that pass the gate
    function crossTable() {
      const byCaps = groupBy([...cross.values()], (x) => capOf(x.from) + ":" + capOf(x.to));
      return pmTable(caps.slice(1), (src, dst, pair) => {
        if (src === dst) return `<td class="diag" data-tiptext="${esc(src.label)}: the same capability, calibrated within it (its chip above)"></td>`;
        const all = byCaps.get(src.id + ":" + dst.id) || [];
        const ok = all.filter((x) => x.passes && x.loo != null).sort((a, b) => a.loo - b.loo);
        if (!ok.length) return `<td class="none" data-tiptext="${pair}: ${all.length ? `none of ${all.length} benchmark pairs passes the quality gate` : "no benchmark pairs with enough shared models"}"></td>`;
        const med = ok[ok.length >> 1].loo, best = ok[0];
        return `<td class="cell cross" style="background:${lossColor(med * 100, gate)}" data-tiptext="${pair}: median LOO error ${pct(med)} pp; ${ok.length} of ${all.length} benchmark pairs pass the quality gate; best ${esc(ix.bench.get(best.from).label)} → ${esc(ix.bench.get(best.to).label)} (${pct(best.loo)} pp)">${pct(med)}</td>`;
      });
    }

    let cap = caps.slice(1).reduce((a, c) => (c.n > a.n ? c : a)).id;
    main.innerHTML = `<div class="page">
      ${pageHead("Calibration", "Which benchmarks predict which", `For each ordered pair of same-capability benchmarks with at least ${D.meta.quality_gate.min_pairs} shared models,
        several monotone curves are fitted and the one with the lowest leave-one-out error is kept, if it passes the quality gate
        (R² ≥ ${D.meta.quality_gate.min_r2}, error ≤ ${gate} pp). Cells show that error in percentage points: rows are the source, columns the target.
        Estimates come only from calibrations within a capability; pick one below, or All, which also shows the fits across capabilities (see Cross-domain predictability).`)}
      <section class="section">
        ${chips.html(cap)}
        <div class="pm-wrap" id="pm-wrap">${pairMap(cap)}</div>
        <div class="scale"><span>0 pp</span><span class="ramp" style="background:${LOSS_RAMP}"></span><span>${gate} pp (gate)</span>
          <span style="margin-left:1rem"><span class="sq none"></span>no usable mapping</span>
          <span><span class="sq diag"></span>same benchmark</span>
          <span><span class="sq cross"></span>cross-domain (All only)</span></div>
      </section>
      <section class="section">
        <h2 class="h2">Cross-domain predictability</h2>
        <p class="lede">How well one capability's benchmarks predict another's: each cell is the median leave-one-out error (pp)
          of the fits between their benchmarks that pass the quality gate, from source capability (row) to target (column).
          Fits across capabilities are made only for this comparison and the All view; no estimate comes from them.</p>
        <div class="pm-wrap" id="cross-sum"><p class="muted">Loading the cross-domain fits…</p></div>
      </section>
      <section class="section">
        <h2 class="h2">Fitted mappings · <span id="maps-cap">${esc(capName(cap))}</span></h2>
        <div class="list-wrap" id="maps">${mapList(cap, false)}</div>
      </section></div>`;

    // a chip redraws the pair map and the list for its capability
    chips.bind((id) => {
      cap = id;
      swapContent($("#pm-wrap"), pairMap(cap));
      swapContent($("#maps-cap"), esc(capName(cap)));
      swapContent($("#maps"), mapList(cap, false));
    });
    $("#maps").addEventListener("click", (e) => {
      if (e.target.closest(".more-maps")) swapContent($("#maps"), mapList(cap, true));
    });
    const sum = $("#cross-sum");
    load("/data/cross.json").then((d) => {
      if (!sum.isConnected) return;   // another page was opened meanwhile
      cross = new Map(d.cross.map(([from, to, method, n, r2, loo, passes]) => [from + ":" + to, { from, to, method, n, r2, loo, passes }]));
      if (!d.cross.length) { sum.innerHTML = `<p class="muted">No cross-domain fits in this build yet.</p>`; return; }
      swapContent(sum, crossTable());
      if (cap === "all") swapContent($("#pm-wrap"), pairMap(cap));
    }).catch(() => { if (sum.isConnected) sum.innerHTML = `<p class="muted">The cross-domain fits could not be loaded.</p>`; });
  }

  // data: the calibration with its points and curve, the estimates it made and its reverse (data/calibration/{id}.json)
  function renderMapping(id, data) {
    if (!data) return renderNotFound("No such mapping.");
    const m = data.mapping, reverse = data.reverse;
    const f = ix.bench.get(m.from), t = ix.bench.get(m.to);
    setMeta(`${f.label} → ${t.label} calibration`, `How ${f.label} scores predict ${t.label}: the fitted ${methodLabel(m.method)} curve, the models it was fitted on and its cross-validated error.`);
    const ests = remember(data.estimates);

    // plot geometry
    const W = 720, H = 460, L = 56, R = 18, T = 18, B = 50;
    const xs = m.points.map((p) => p[1]).concat(ests.map((s) => s.via.from[0].v));
    const ys = m.points.map((p) => p[2]).concat(ests.map((s) => s.v));
    const xAxis = axis(Math.max(...xs) + 0.02), yAxis = axis(Math.max(...ys) + 0.02);
    const xMax = xAxis.max, yMax = yAxis.max;
    const px = (x) => L + (x / xMax) * (W - L - R);
    const py = (y) => H - B - (y / yMax) * (H - T - B);
    let grid = "";
    for (const v of xAxis.ticks) grid += `<line class="gl" x1="${px(v)}" x2="${px(v)}" y1="${T}" y2="${H - B}"/><text x="${px(v)}" y="${H - B + 18}" text-anchor="middle">${Math.round(v * 100)}</text>`;
    for (const v of yAxis.ticks) grid += `<line class="gl" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/><text x="${L - 8}" y="${py(v) + 4}" text-anchor="end">${Math.round(v * 100)}</text>`;
    const [r0, r1] = m.range || [0, xMax];
    // polyline through the exported curve samples with a <= x <= b
    const path = (a, b) => {
      const pts = m.curve
        .filter(([x]) => x >= a && x <= b && x <= xMax)
        .map(([x, y]) => `${px(x).toFixed(1)},${py(Math.max(0, Math.min(yMax, y))).toFixed(1)}`);
      return pts.length > 1 ? "M" + pts.join("L") : "";
    };
    const pts = m.points
      .map((p) => {
        const mod = ix.model.get(p[0]);
        return `<circle class="pt" cx="${px(p[1])}" cy="${py(p[2])}" r="4.5"/><circle class="hit" cx="${px(p[1])}" cy="${py(p[2])}" r="9" data-tiptext="${esc(mod ? mod.name : "?")}: ${pct(p[1])}% → ${pct(p[2])}% (measured on both)"/>`;
      })
      .join("");
    const epts = ests
      .map((s) => {
        const x = s.via.from[0].v;
        return `<circle class="ept ${s.tier}" cx="${px(x)}" cy="${py(s.v)}" r="4.5"/><circle class="hit" cx="${px(x)}" cy="${py(s.v)}" r="9" data-tip="${s.m}:${s.b}"/>`;
      })
      .join("");
    const svg = `<svg class="plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="Scatter of ${esc(f.label)} against ${esc(t.label)} with the fitted ${esc(methodLabel(m.method))} curve">
      <rect class="band" x="${px(r0)}" y="${T}" width="${px(r1) - px(r0)}" height="${H - T - B}"/>
      ${grid}
      <line class="ax" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><line class="ax" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
      <path class="curve-out" d="${path(0, r0)}"/><path class="curve-out" d="${path(r1, xMax)}"/>
      <path class="curve" d="${path(r0, r1)}"/>
      ${pts}${epts}
      <text class="lbl" x="${(L + W - R) / 2}" y="${H - 10}" text-anchor="middle">${esc(f.label)} score (%)</text>
      <text class="lbl" transform="translate(16 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">${esc(t.label)} score (%)</text>
    </svg>`;

    const estRows = ests
      .slice()
      .sort((a, b) => b.v - a.v)
      .map((s) => {
        const mod = ix.model.get(s.m);
        return `<tr><td>${dot(mod)} ${modelLink(mod)}</td><td class="num">${pct(s.via.from[0].v)}%</td><td class="num"><i>≈${pct(s.v)}%</i></td><td>${confidenceFlag(s.tier)}${s.x ? ' <span class="flag x">extrapolated</span>' : ""}</td></tr>`;
      })
      .join("");

    main.innerHTML = `<div class="page">
      ${pageHead(`<a href="/calibration">Calibration</a> · ${esc(capLabel(f.capability))}`, `${esc(f.label)} <span class="muted">→</span> ${esc(t.label)}`,
        `Each dot is a model measured on both benchmarks. The curve is the selected
        ${esc(methodLabel(m.method))} fit; the shaded band is the range of ${esc(f.label)} scores it was fitted on,
        and the dashed parts are extrapolation. Hollow rings are the estimates it produced, coloured by confidence.`)}
      <div class="grid-2 section">
        <div class="card">${svg}
          <div class="legend" style="border:0;padding-bottom:0">
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="currentColor"/></svg> measured on both</span>
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-high)" stroke-width="2"/></svg> high</span>
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-medium)" stroke-width="2"/></svg> medium</span>
            <span class="ring-key"><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-low)" stroke-width="2" stroke-dasharray="2 1.5"/></svg> low confidence</span>
          </div>
        </div>
        <div>
          <div class="eqn">${esc(m.equation)}</div>
          <p class="muted" style="font-size:.8rem;margin:.4rem 0 0">x and y as fractions (0–1).</p>
          <dl class="kv" style="margin-top:1rem">
            <dt>paired models</dt><dd>${m.n}</dd>
            <dt>R²</dt><dd>${m.r2.toFixed(3)}</dd>
            <dt>in-sample RMSE</dt><dd>${pct(m.rmse)} pp</dd>
            <dt>LOO-CV error</dt><dd><b>${pct(m.loo)} pp</b></dd>
            <dt>fitted x-range</dt><dd>${m.range ? `${pct(m.range[0])}–${pct(m.range[1])}%` : "—"}</dd>
            <dt>estimates made</dt><dd>${ests.length}</dd>
          </dl>
          ${reverse ? `<p style="margin-top:1rem"><a class="btn-link" href="${mappingHref(reverse.id)}">Reverse direction: ${esc(t.label)} → ${esc(f.label)} (${pct(reverse.loo)} pp) →</a></p>` : ""}
          ${estRows ? `<div class="list-wrap" style="margin-top:1rem"><table class="list"><thead><tr><th>Estimated model</th><th style="text-align:right">${esc(f.label)}</th><th style="text-align:right">${esc(t.label)}</th><th>Confidence</th></tr></thead><tbody>${estRows}</tbody></table></div>` : ""}
        </div>
      </div></div>`;
  }

  // --- page: multivariate -----------------------------------------------------
  // data: the multivariate view's fits (data/multivariate.json, Snapshot's compact rows)
  function renderMultivariate(_, data) {   // its lede is also in src/Pages.php
    setMeta("Multivariate LLM benchmark predictions", "Each LLM benchmark predicted from several others together, of any capability: the fitted model, its cross-validated error and every prediction.");
    const gate = D.meta.quality_gate.max_loo_pp;
    const fits = data.multivariate;
    const chips = capFilter(fits, (f) => capOf(f.to));
    const gain = (f) => (f.alone == null ? -Infinity : f.alone - f.loo);
    // gain: over the best of its benchmarks alone
    const SORTS = { gain: ["biggest gain", (a, b) => gain(b) - gain(a)], loo: ["lowest error", (a, b) => a.loo - b.loo] };
    const LIST_ROWS = 20;  // fits shown before "Show all"
    let cap = "all", sort = "gain";

    const term = (id) => {
      const b = ix.bench.get(id);
      return `<span class="mv-term"><a href="${benchHref(b)}">${esc(b.label)}</a><small>${esc(capLabel(b.capability))}</small></span>`;
    };
    // measured (x) against leave-one-out predicted (y), on one scale, with the y = x line
    function scatter(f) {
      const W = 300, H = 300, L = 40, R = 10, T = 10, B = 38;
      const pts = f.points.filter((p) => p[2] != null);
      // zoomed to the scores, on round ticks
      const vals = pts.flatMap((p) => [p[1], p[2]]);
      const { min: lo, max: hi, ticks } = axis(Math.max(...vals) + 0.02, Math.min(...vals) - 0.02);
      const clamp = (v) => Math.min(hi, Math.max(lo, v));
      const px = (v) => L + ((clamp(v) - lo) / (hi - lo)) * (W - L - R), py = (v) => H - B - ((clamp(v) - lo) / (hi - lo)) * (H - T - B);
      let grid = "";
      for (const v of ticks) grid += `<line class="gl" x1="${px(v)}" x2="${px(v)}" y1="${T}" y2="${H - B}"/><text x="${px(v)}" y="${H - B + 16}" text-anchor="middle">${Math.round(v * 100)}</text>`
        + `<line class="gl" x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}"/><text x="${L - 6}" y="${py(v) + 4}" text-anchor="end">${Math.round(v * 100)}</text>`;
      const dots = pts.map(([m, obs, pred]) => {
        const mod = ix.model.get(m);
        return `<circle class="pt" cx="${px(obs).toFixed(1)}" cy="${py(pred).toFixed(1)}" data-tiptext="${esc(mod ? mod.name : "?")}: measured ${pct(obs)}%, predicted ${pct(pred)}% (${pred >= obs ? "+" : "−"}${pct(Math.abs(pred - obs))} pp)"/>`;
      }).join("");
      const t = ix.bench.get(f.to);
      return `<svg class="plot mv-plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="Measured against cross-validated predicted ${esc(t.label)} scores">
        ${grid}<line class="ax" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/><line class="ax" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
        <line class="ideal" x1="${px(lo)}" y1="${py(lo)}" x2="${px(hi)}" y2="${py(hi)}"/>${dots}
        <text class="lbl" x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle">measured (%)</text>
        <text class="lbl" transform="translate(12 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">predicted (%)</text></svg>`;
    }
    function card(f) {
      const d = gain(f);
      const vs = f.alone == null ? "" : `<dt>best one alone</dt><dd>${pct(f.alone)} pp <span class="${d > 0 ? "mv-better" : "muted"}">(${d > 0 ? "−" : "+"}${pct(Math.abs(d))} pp)</span></dd>`;
      return `<article class="card mv-fit">
        <div class="mv-info">
          <div class="mv-formula">${term(f.to)}${f.from.map((id, i) => `<span class="mv-next"><span class="mv-op">${i ? "+" : "~"}</span>${term(id)}</span>`).join("")}</div>
          <dl class="kv">
            <dt>fit</dt><dd>${esc(methodLabel(f.method))}</dd>
            <dt>models</dt><dd>${f.n}</dd>
            <dt>R²</dt><dd>${f.r2.toFixed(3)}</dd>
            <dt>LOO-CV error</dt><dd><span class="scale-dot" style="background:${lossColor(f.loo * 100, gate)}"></span><b>${pct(f.loo)} pp</b></dd>
            ${vs}
          </dl>
          <p class="mv-flags">${f.passes ? "" : '<span class="flag low">fails the quality gate</span> '}${f.used ? `<span class="flag x" data-tiptext="The estimates of ${esc(ix.bench.get(f.to).label)} come from a combination of benchmarks of its own capability">estimates use a combination</span>` : ""}</p>
        </div>
        ${scatter(f)}
      </article>`;
    }
    function list(all) {
      const rows = chips.byCap.get(cap).slice().sort(SORTS[sort][1]);
      return (all ? rows : rows.slice(0, LIST_ROWS)).map(card).join("")
        + (!all && rows.length > LIST_ROWS ? `<button type="button" class="btn more-fits">Show all ${rows.length}</button>` : "");
    }

    main.innerHTML = `<div class="page">
      ${pageHead("Multivariate", "Each benchmark from several others", `For each benchmark, candidates of any capability (its best single predictors and the benchmarks sharing the most models with it) feed two searches combined - one elastic net fit whose lasso part zeroes the useless ones, and greedy forward selection trying every candidate - always ending with at least two. On what they find, the linear fit and a multivariate Michaelis–Menten curve compete by cross-validated error.
        Each plot shows every model measured on all of them: its measured score against the prediction of the fit to the other models.
        Shown for analysis: the estimates come from the <a href="/calibration">calibrations</a>.`)}
      <section class="section">
        ${chips.html(cap)}
        <div class="mv-sort"><span class="ctl-label">Sort</span>${segHTML("Sort", Object.entries(SORTS).map(([k, [label]]) => [k, label]), sort)}</div>
        <div class="mv-list" id="mv-list">${fits.length ? list(false) : '<p class="muted">No multivariate fits in this build yet.</p>'}</div>
      </section></div>`;

    const redraw = () => swapContent($("#mv-list"), list(false));
    chips.bind((id) => { cap = id; redraw(); });
    bindSeg($(".mv-sort .seg"), (k) => { sort = k; hideTip(); redraw(); });
    $("#mv-list").addEventListener("click", (e) => {
      if (e.target.closest(".more-fits")) swapContent($("#mv-list"), list(true));
    });
  }

  // --- page: method -----------------------------------------------------------
  function renderMethod() {
    const r = D.meta.confidence_levels, g = D.meta.quality_gate, c = D.meta.counts.confidence;
    setMeta("How missing benchmark scores are estimated", "How benchgap estimates missing LLM benchmark scores: calibration curves, leave-one-out validation and confidence levels.");
    main.innerHTML = `<div class="page">
      ${pageHead("Method", "How the gaps are filled, and when not to trust it", esc(ABOUT))}
      <nav class="jump chip-row" aria-label="On this page">${[["data", "The data"], ["calibrating", "Calibrating"], ["filling", "Filling a gap"], ["confidence", "Confidence"], ["caveats", "Caveats"]]
        .map(([id, label]) => `<a class="chip" href="#${id}">${label}</a>`).join("")}</nav>
      <article class="prose">
        <h2 id="data">The data</h2>
        <p>Measured scores come from public evaluation leaderboards and model reports, compiled by
        <a href="https://benchlm.ai/data" rel="noopener" target="_blank">BenchLM.ai</a> (harness: ${esc(D.meta.harnesses.join(", "))}),
        retrieved ${esc(D.meta.retrieved_at || "")}. Every score is stored as a fraction and shown as a percentage. Each benchmark belongs to a
        <b>capability</b> group (agentic terminal, agentic tools, knowledge, vision, …). Benchmarks are only ever calibrated against
        benchmarks of the same capability: a model never run on a vision benchmark keeps that gap instead of inheriting a score from text benchmarks.</p>

        <h2 id="calibrating">Calibrating one benchmark against another</h2>
        <p>For every ordered pair of same-capability benchmarks with at least ${g.min_pairs} models measured on both, ${g.n_candidates} monotone curve families are fitted
        by least squares: linear, Michaelis–Menten (with and without an offset), the inverse Michaelis–Menten form, Hill and an offset logistic.
        The saturating forms capture the typical shape: gains on an easier benchmark flatten out while a harder one keeps discriminating.</p>
        <div class="formula">y = y₀ + V<sub>max</sub> · x / (K + x)</div>
        <p>The curve with the lowest <b>leave-one-out cross-validated error</b> is kept: each model is held out in turn, the curve is refitted without it,
        and the held-out score is predicted. That error, in percentage points, is the “±” shown next to every estimate. A pair keeps no mapping at all
        unless its best curve reaches R² ≥ ${g.min_r2} and an error of at most ${g.max_loo_pp} pp; poorly fitting pairs leave their gaps empty rather than filling them with noise.</p>

        <h2 id="filling">Filling a gap</h2>
        <p>For a model missing a score, every mapping into that benchmark from a benchmark the model <i>was</i> measured on is a candidate; the one
        with the lowest cross-validated error wins. Multivariate mappings (several source benchmarks combined) compete on the same footing when available.
        Estimates are never used to make further estimates: inputs are always measured scores.</p>

        <h2 id="confidence">Confidence levels</h2>
        <p>Every estimate gets a confidence level, so low-confidence fills are visibly different from the others on every page:</p>
        <table class="tier-table">
          <thead><tr><th>Level</th><th>Rule</th><th>Count</th></tr></thead>
          <tbody>
            <tr><td><span class="flag high">high</span></td><td>cross-validated error ≤ ${r.high_max_pp} pp and no warning below</td><td class="mono">${c.high}</td></tr>
            <tr><td><span class="flag medium">medium</span></td><td>error ≤ ${r.medium_max_pp} pp, or a high-confidence fit with one warning</td><td class="mono">${c.medium}</td></tr>
            <tr><td><span class="flag low">⚠ low</span></td><td>error above ${r.medium_max_pp} pp, or demoted by warnings</td><td class="mono">${c.low}</td></tr>
          </tbody>
        </table>
        <p>Each of these warnings demotes an estimate by one level:</p>
        <ul>
          <li><b>Extrapolated</b>: the model's source score lies outside the range the mapping was fitted on.</li>
          <li><b>Small sample</b>: the mapping was fitted on fewer than ${r.min_reliable_n} models, so its error estimate is itself noisy.</li>
          <li><b>Uninformative fit</b>: R² below ${r.min_informative_r2}; the curve explains little of how models differ on the target.</li>
        </ul>
        <p>Hover over, tap or focus any estimate to see exactly which benchmark it came from, the curve used, and which warnings applied.
        The leaderboard and matrix can hide low-confidence estimates (<i>+ reliable estimates</i>) or all of them (<i>Measured only</i>).</p>

        <h2 id="caveats">Caveats</h2>
        <ul>
          <li>Estimates are predictions, not measurements. A model can genuinely over- or under-perform what its other scores imply.</li>
          <li>Coefficients are harness-specific: these calibrations hold for the source leaderboard's evaluation setup, not for other harnesses.</li>
          <li>Saturating curves have a ceiling. Several models above a mapping's fitted range can receive the same estimate; those are flagged as extrapolated.</li>
          <li>The error bars are point estimates of typical error, not credible intervals. A probabilistic version is on the roadmap.</li>
        </ul>
      </article></div>`;
  }

  // --- page: API ----------------------------------------------------------------
  const API_BASE = new URL("/api/v1/", location.origin).href;
  const API_ENDPOINTS = [
    ["index.json", "Entry point: build metadata, endpoint templates, confidence thresholds, capabilities."],
    ["benchmarks.json", "All benchmark versions."],
    ["benchmarks/{name}/{version}.json", "One benchmark with all its scores, highest first."],
    ["models.json", "All models."],
    ["models/{slug}.json", "One model with all its scores."],
    ["scores.json", "Every score, measured and estimated."],
    ["scores.csv", "The same scores flattened to CSV, for spreadsheets."],
    ["mappings.json", "Every calibration (fitted mapping between two benchmarks)."],
    ["mappings/{id}.json", "One calibration with its training points, sampled curve and estimates."],
    ["openapi.json", "OpenAPI 3.1 description of this API."],
  ];
  const API_FIELDS = {
    Score: [
      ["model", "string", "Model slug, e.g. <code>gpt-6-astra</code>."],
      ["benchmark", "string", `Benchmark key <code>name/version</code>, e.g. <code>${ix.home}</code>.`],
      ["score", "number", "Fraction in [0, 1]. Multiply by 100 for percent."],
      ["source", '"measured" | "estimated"', "Measured scores come from a public leaderboard; estimates are predictions."],
      ["estimate", "Estimate | null", "Present only for estimated scores."],
    ],
    Estimate: [
      ["confidence", '"high" | "medium" | "low"', "Confidence level; see <a href=\"/method\">Method</a> for the rules."],
      ["error_pp", "number", "Cross-validated error of the calibration, in percentage points (the ± on the site)."],
      ["reasons", "string[]", "Why the confidence is not high; empty when it is."],
      ["extrapolated", "boolean", "The input score lies outside the range the calibration was fitted on."],
      ["method, method_name", "string", "Curve family, e.g. <code>mm_offset</code> / Michaelis–Menten + offset."],
      ["kind", '"univariate" | "multivariate"', "One source benchmark, or several combined."],
      ["mapping_id", "integer | null", "The calibration used; resolve with <code>mappings/{id}.json</code>."],
      ["inputs", "{benchmark, score}[]", "The same model's measured scores the estimate was computed from."],
    ],
    Benchmark: [
      ["key, name, version", "string", "Stable identifier <code>name/version</code> and its parts."],
      ["label", "string", "Display name, e.g. Terminal-Bench 4.0."],
      ["capability", "string", "Capability group; benchmarks are only calibrated within one."],
      ["harness, source_url", "string", "Evaluation harness and the leaderboard the measured scores come from."],
      ["n_measured, n_estimated", "integer", "Number of scores of each kind."],
      ["url, page", "string", "This resource in the API, and on the website."],
    ],
    Model: [
      ["slug, name", "string", "Stable identifier and display name."],
      ["provider, provider_name", "string", "e.g. <code>openai</code> / OpenAI."],
      ["n_measured, n_estimated", "integer", "Number of scores of each kind."],
      ["url, page", "string", "This resource in the API, and on the website."],
    ],
    Mapping: [
      ["id", "integer", "Calibration id (changes when the data is refitted)."],
      ["from, to", "string", "Source and target benchmark keys."],
      ["method, method_name, equation", "string", "Selected curve and its fitted equation (x, y as fractions)."],
      ["n_models, r2, rmse_pp, loo_rmse_pp", "number", "Training size and fit quality; <code>loo_rmse_pp</code> becomes the estimates' <code>error_pp</code>."],
      ["train_range", "[min, max]", "Source scores the curve was fitted on."],
      ["points, curve", "array", "Detail endpoint only: training points and the sampled curve."],
    ],
  };

  // Minimal syntax highlighter for the code samples (no external library):
  // one regex pass per language; each match becomes a <span class="tk-…">.
  const SAMPLE_LANG = { curl: "bash", Python: "python", JavaScript: "js", pandas: "python" };
  const HL_KEYWORDS = {
    bash: ["curl"],
    python: ["import", "as", "for", "in", "if", "and", "continue"],
    js: ["const", "await"],
  };
  function highlight(code, lang) {
    const kw = HL_KEYWORDS[lang] || [];
    const comment = lang === "js" ? String.raw`\/\/[^\n]*` : String.raw`#[^\n]*`;
    // group names are the token classes (tk-com, tk-str, ...)
    const re = new RegExp(
      [
        `(?<com>${comment})`,
        String.raw`(?<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|https?:\/\/[^\s"']+)`,
        String.raw`(?<num>\b\d+(?:\.\d+)?\b)`,
        String.raw`(?<kw>\b(?:${kw.join("|")})\b)`,
        String.raw`(?<fn>\b[A-Za-z_]\w*(?=\())`,
      ].join("|"),
      "g"
    );
    let out = "", last = 0;
    for (const m of code.matchAll(re)) {
      const cls = Object.keys(m.groups).find((k) => m.groups[k] !== undefined);
      out += esc(code.slice(last, m.index)) + `<span class="tk-${cls}">${esc(m[0])}</span>`;
      last = m.index + m[0].length;
    }
    return out + esc(code.slice(last));
  }

  function codeSamples(base) {
    return {
      curl: `curl ${base}benchmarks/${ix.home}.json`,
      Python: `import requests

data = requests.get("${base}benchmarks/${ix.home}.json").json()
for s in data["scores"]:
    est = s["estimate"]
    if est and est["confidence"] == "low":
        continue  # skip low-confidence estimates
    print(s["model"], round(s["score"] * 100, 1), s["source"])`,
      JavaScript: `const res = await fetch("${base}models/gpt-6-astra.json");
const { model, scores } = await res.json();
const measured = scores.filter((s) => s.source === "measured");
console.log(model.name, measured.length, "measured scores");`,
      pandas: `import pandas as pd

df = pd.read_csv("${base}scores.csv")
measured = df[df.source == "measured"]
table = measured.pivot(index="model", columns="benchmark", values="score")`,
    };
  }

  // data: the calibrations (data/calibration.json), for the "Try it" picker
  function renderApi(_, data) {
    setMeta("Public API", "Free JSON and CSV API for LLM benchmark scores, measured and estimated, with an OpenAPI 3.1 description.");
    const base = API_BASE;
    const samples = codeSamples(base);
    // every resource the API serves, grouped for the "Try it" picker
    const tryGroups = [
      ["Lists", API_ENDPOINTS.map(([p]) => p).filter((p) => !p.includes("{"))],
      ["Benchmarks", D.benchmarks.map((b) => `benchmarks/${b.key}.json`)],
      ["Models", D.models.map((m) => `models/${m.slug}.json`)],
      ["Calibrations", data.mappings.map((m) => m.id).sort((a, b) => a - b).map((id) => `mappings/${id}.json`)],
    ];
    const tryDefault = `benchmarks/${ix.home}.json`;
    const fieldTable = (name) => `<div class="card api-obj"><h3 class="h3">${name}</h3><table class="list"><tbody>${API_FIELDS[name]
      .map(([f, t, d]) => `<tr><td class="mono">${f}</td><td class="mono muted">${esc(t)}</td><td>${d}</td></tr>`)
      .join("")}</tbody></table></div>`;

    main.innerHTML = `<div class="page">
      ${pageHead("API · v1", "Public API", `Everything on this site is available as plain JSON (and CSV): every benchmark, model,
        score and calibration, with each estimate's confidence level and error. Free, no key, readable from any origin.`)}

      <div class="api-base-bar">
        <span class="verb">GET</span>
        <code class="mono" id="api-base">${esc(base)}</code>
        <button type="button" class="code-copy" data-copy="api-base" aria-label="Copy base URL" title="Copy">${COPY_ICON}</button>
        <a class="spec-link" href="${esc(base)}openapi.json" target="_blank" rel="noopener" title="Machine-readable spec: import into Postman, Insomnia or Swagger UI, or generate a client">OpenAPI 3.1 spec ↗</a>
      </div>

      <section class="section">
        <h2 class="h2">Quick start</h2>
        <div class="controls">${segHTML("Code sample language", Object.keys(samples).map((k) => [k, k]), "curl")}</div>
        ${codeBox("api-sample", "", highlight(samples.curl, SAMPLE_LANG.curl))}
      </section>

      <section class="section">
        <h2 class="h2">Endpoints</h2>
        <p class="muted">All paths are relative to the base URL. Every JSON document also carries
        <code>api_version</code>, <code>generated_at</code>, <code>data_retrieved_at</code> and <code>counts</code>.</p>
        <div class="list-wrap"><table class="list api-ep"><thead><tr><th></th><th>Path</th><th>Returns</th></tr></thead><tbody>${API_ENDPOINTS
          .map(([path, d]) => `<tr><td><span class="verb">GET</span></td><td class="mono">${path.includes("{") ? esc(path) : `<a href="${esc(base + path)}" target="_blank" rel="noopener">${esc(path)}</a>`}</td><td>${d}</td></tr>`)
          .join("")}</tbody></table></div>
      </section>

      <section class="section">
        <h2 class="h2">Try it</h2>
        <div class="api-try">
          <label class="sr" for="api-path">Endpoint</label>
          <span class="mono muted api-base">${esc(base)}</span>
          <select id="api-path" class="select mono">${tryGroups
            .map(([g, paths]) => `<optgroup label="${g}">${paths
              .map((p) => `<option value="${esc(p)}" ${p === tryDefault ? "selected" : ""}>${esc(p)}</option>`).join("")}</optgroup>`)
            .join("")}</select>
          <button type="button" class="btn" id="api-send">Send request</button>
        </div>
        <div class="api-status mono muted" id="api-status"></div>
        ${codeBox("api-out", "api-out", "Press “Send request” to fetch a live response.")}
      </section>

      <section class="section">
        <h2 class="h2">Objects</h2>
        <div class="grid-2">${["Score", "Estimate", "Benchmark", "Model", "Mapping"].map(fieldTable).join("")}</div>
      </section>

      <section class="section prose">
        <h2>Conventions</h2>
        <ul>
          <li><b>Scores are fractions</b> in [0, 1]; the site shows them as percentages.</li>
          <li><b>Estimates are never inputs.</b> Every estimate is computed from the same model's <i>measured</i> scores, listed in <code>estimate.inputs</code>.</li>
          <li><b>Identifiers:</b> benchmarks are addressed by <code>name/version</code>, models by slug. Both are stable across data updates; mapping ids are not.</li>
          <li><b>Freshness:</b> responses always reflect the current database; <code>generated_at</code> says when it was last rebuilt. Responses carry an ETag, so revalidating unchanged data is a cheap 304.</li>
          <li><b>Versioning:</b> <code>v1</code> only gains fields. Anything that would break a client goes to <code>/api/v2/</code>, with <code>v1</code> kept alongside it.</li>
          <li><b>Errors:</b> an unknown benchmark, model or mapping is a plain HTTP 404.</li>
        </ul>
        <h2>Using the data</h2>
        <p>The measured scores are data from <a href="https://benchlm.ai/data" rel="noopener" target="_blank">BenchLM.ai</a>,
        licensed under <a href="https://creativecommons.org/licenses/by-nc/4.0/" rel="noopener" target="_blank">CC BY-NC 4.0</a>
        (non-commercial use, with credit); the estimates are benchgap's additions. Check those terms before republishing scores.
        Estimates are model-based predictions: if you show them, show them as estimates, ideally with
        <code>error_pp</code> and <code>confidence</code>, and link back to benchgap.</p>
        <p>The benchgap code is <a href="${REPO_URL}/blob/main/LICENSE" target="_blank" rel="noopener">MIT-licensed</a>.
        A machine-readable description of the API is in <a href="${esc(base)}openapi.json" target="_blank" rel="noopener">openapi.json</a>.</p>
      </section>
    </div>`;

    bindSeg(main.querySelector(".seg"), (lang) => {
      $("#api-sample").innerHTML = highlight(samples[lang], SAMPLE_LANG[lang]);
    });

    const send = async () => {
      const path = $("#api-path").value;
      const out = $("#api-out"), status = $("#api-status");
      status.textContent = "loading…";
      const t0 = performance.now();
      try {
        const res = await fetch(API_BASE + path, { cache: "no-cache" });
        const text = await res.text();
        const ms = Math.round(performance.now() - t0);
        status.textContent = `${res.status} ${res.statusText || ""} · ${(text.length / 1024).toFixed(1)} KB · ${ms} ms`;
        let shown = text;
        if (res.ok && path.endsWith(".json")) shown = JSON.stringify(JSON.parse(text), null, 2);
        out.textContent = shown;
        out.scrollTop = 0;
      } catch (err) {
        status.textContent = "request failed";
        out.textContent = String(err);
      }
    };
    main.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", () => {
      const pre = document.getElementById(button.dataset.copy);
      copyText(pre.textContent, button, pre);
    }));
    $("#api-send").addEventListener("click", send);
  }

  // the 404 page (as Pages::notFound); msg says what was not found, in HTML
  function renderNotFound(msg) {
    setMeta("Not found", "", null);
    main.innerHTML = `<div class="page"><section class="nf reveal">
      <svg class="nf-mark" viewBox="0 0 120 64" aria-hidden="true"><defs><pattern id="nf-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="5"/></pattern></defs>
        <rect x="4" y="20" width="26" height="44" rx="2"/><rect class="nf-gap" x="47" y="4" width="26" height="60" rx="2" fill="url(#nf-hatch)"/><rect x="90" y="30" width="26" height="34" rx="2"/></svg>
      <div class="eyebrow">Not found · 404</div>
      <h1 class="display">This one is a gap we <em>can’t</em> fill.</h1>
      <p class="lede">${msg} It may have been renamed, or the link has a typo.</p>
      <a class="btn nf-home" href="/">Back to the leaderboard</a>
      <nav class="nf-links" aria-label="Elsewhere on benchgap"><div class="ctl-label">Or try</div>
        <a href="/matrix">Every model × every benchmark</a><a href="/calibration">Which benchmarks predict which</a><a href="/api">Every score in the public API</a></nav>
    </section></div>`;
  }

  // --- router -----------------------------------------------------------------
  // the site's pages (keep in step with serve.php): path, nav item, renderer of the path's
  // argument and the page's data, and the URL of that data (none: the page needs only D).
  // A renderer sets the page's meta and returns true if it updated the page in place; its data
  // is null if the server has none for the argument (404)
  const PAGES = [
    [/^\/$/, "board", (_, data) => renderBoard(ix.home, data), () => "/data/home.json"],
    [/^\/b\/(.+)$/, "board", renderBoard, boardUrl],
    [/^\/model\/(.+)$/, "", renderModel, modelUrl],
    [/^\/matrix$/, "matrix", renderMatrix, () => "/data/matrix.json"],
    [/^\/calibration$/, "calibration", renderCalibration, () => "/data/calibration.json"],
    [/^\/calibration\/(\d+)$/, "calibration", renderMapping, (id) => `/data/calibration/${id}.json`],
    [/^\/multivariate$/, "multivariate", renderMultivariate, () => "/data/multivariate.json"],
    [/^\/method$/, "method", renderMethod],
    [/^\/api$/, "api", renderApi, () => "/data/calibration.json"],
  ];
  const pageOf = (path) => PAGES.find(([re]) => re.test(path));
  const argOf = (page, path) => decodeURIComponent(path.match(page[0])[1] || "");

  function go(path) {
    if (path === location.pathname) return;
    history.pushState(null, "", path);
    route();
  }

  // title, description and canonical path of the current page, for search engines and tabs;
  // a null path marks a page not to index (not found)
  function setMeta(title, description, path) {
    document.title = `${title} · benchgap`;
    $('meta[name="description"]').content = description;
    $('link[rel="canonical"]').href = location.origin + (path ?? location.pathname);
    $('meta[name="robots"]').content = path === null ? "noindex" : "index, follow";
  }

  // the first page is rendered: <main> no longer hides (index.html, .booting)
  const booted = () => document.documentElement.classList.remove("booting");
  let shownPath = null;   // the path the page was last rendered (or is being loaded) for
  const scrollToHash = () => { const el = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1))); if (el) el.scrollIntoView(); return !!el; };
  async function route() {
    hideTip();
    const path = location.pathname;
    if (path === shownPath) return scrollToHash();   // only the #fragment changed: same page, no re-render
    shownPath = path;
    const page = pageOf(path);
    const arg = page ? argOf(page, path) : "";
    let data = null;
    // the current page stays (dimmed if it takes a moment) until the new one's data is in
    main.toggleAttribute("aria-busy", !!(page && page[3]));
    if (page && page[3]) {
      try {
        data = await load(page[3](arg));
      } catch (err) {
        if (err.status !== 404) {
          if (shownPath === path) { shownPath = null; renderError(err); }
          return;
        }
      } finally {
        if (shownPath === path || shownPath === null) main.removeAttribute("aria-busy");
      }
      if (shownPath !== path) return;   // another page was opened meanwhile
    }
    const nav = page ? page[1] : "";
    const inPlace = page ? page[2](arg, data) : renderNotFound("Page not found.");
    document.querySelectorAll("[data-nav]").forEach((a) => (a.dataset.nav === nav ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    centerIn($(".nav"), '[aria-current="page"]');
    booted();
    if (!inPlace && !scrollToHash()) window.scrollTo(0, 0);
  }

  // in-site links switch pages without a reload
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download") || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const url = new URL(a.href);
    if (url.origin !== location.origin || url.hash || !pageOf(url.pathname)) return;
    e.preventDefault();
    go(url.pathname);
  });

  // --- chrome -------------------------------------------------------------------
  // --- search: models and benchmarks, filtered by type ------------------------------
  const fold = (t) => t.toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const SEARCH_TYPES = [["all", "All"], ["model", "Models"], ["bench", "Benchmarks"]];
  const SEARCH_GROUPS = Object.fromEntries(SEARCH_TYPES.slice(1));
  const BENCH_ICON = '<svg class="srch-ic" viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="7" width="3" height="7" rx=".6"/><rect x="6.5" y="3" width="3" height="11" rx=".6"/><rect x="11" y="9" width="3" height="5" rx=".6"/></svg>';
  function initSearch(openSearch) {
    const input = $("#site-search"), pop = $("#search-pop"), list = $("#search-results"), types = $(".search-types");
    const items = [
      ...D.models.filter((m) => m.listed).map((m) => ({ type: "model", label: m.name, text: fold(m.name + " " + m.slug), href: modelHref(m),
        meta: `${D.meta.providers[m.provider] || "Other"} · ${m.n_measured} measured`, weight: m.n_measured, icon: dot(m) })),
      // the listed benchmarks, the original ones first
      ...ix.listed.map((b) => ({ type: "bench", label: b.label, text: fold(b.label + " " + b.key), href: benchHref(b),
        meta: `${capLabel(b.capability)} · ${b.n_measured} measured`, weight: b.n_measured + (b.featured ? 1e4 : 0), icon: BENCH_ICON })),
    ];
    let type = store.get("search-type", "all");
    if (!SEARCH_TYPES.some(([t]) => t === type)) type = "all";
    let shown = [], active = -1;
    types.innerHTML = SEARCH_TYPES.map(([t, label]) => `<button type="button" data-type="${t}">${label}<span class="cnt"></span></button>`).join("");

    // every word of the query must appear; a match at the start of a word ranks above one inside a word
    const where = (it, word) => { const i = it.text.indexOf(word); return i === 0 || /[^a-z0-9]/.test(it.text[i - 1]) ? 0 : 1; };
    function matches(q) {
      const words = fold(q).split(/\s+/).filter(Boolean);
      return items
        .filter((it) => words.every((w) => it.text.includes(w)))
        .map((it) => [it, where(it, words[0])])
        .sort((a, b) => a[1] - b[1] || b[0].weight - a[0].weight || a[0].label.localeCompare(b[0].label))
        .map(([it]) => it);
    }
    // the query's words marked in a result's name
    function mark(label, q) {
      const words = fold(q).split(/\s+/).filter(Boolean), low = fold(label), on = new Array(label.length).fill(false);
      words.forEach((w) => { for (let i = low.indexOf(w); i >= 0; i = low.indexOf(w, i + 1)) on.fill(true, i, i + w.length); });
      let out = "";
      for (let i = 0; i < label.length; ) {
        let j = i; while (j < label.length && on[j] === on[i]) j++;
        out += on[i] ? `<mark>${esc(label.slice(i, j))}</mark>` : esc(label.slice(i, j));
        i = j;
      }
      return out;
    }

    function render() {
      const q = input.value.trim();
      // nothing typed: the most measured models and the original benchmarks
      const pool = q ? matches(q) : items.slice().sort((a, b) => b.weight - a.weight);
      const n = { all: pool.length, model: 0, bench: 0 };
      pool.forEach((it) => n[it.type]++);
      // the type buttons are updated in place: replacing them under a click would make it look like a click outside
      types.querySelectorAll("button").forEach((btn) => {
        btn.setAttribute("aria-pressed", String(btn.dataset.type === type));
        btn.querySelector(".cnt").textContent = q ? n[btn.dataset.type] : "";
      });
      const per = type === "all" ? (q ? 6 : 4) : 50;
      shown = [];
      let html = "";
      for (const t of type === "all" ? ["model", "bench"] : [type]) {
        const group = pool.filter((it) => it.type === t);
        if (!group.length) continue;
        const some = group.slice(0, per);
        html += `<li class="srch-group" role="presentation">${q ? SEARCH_GROUPS[t] : `Popular ${SEARCH_GROUPS[t].toLowerCase()}`}${q ? `<span>${some.length < group.length ? `${some.length} of ${group.length}` : group.length}</span>` : ""}</li>`;
        html += some.map((it) => {
          const i = shown.push(it) - 1;
          return `<li role="option" id="srch-${i}" aria-selected="false"><a href="${it.href}" tabindex="-1" data-i="${i}">${it.icon}<span class="srch-name">${q ? mark(it.label, q) : esc(it.label)}</span><span class="srch-meta">${esc(it.meta)}</span></a></li>`;
        }).join("");
      }
      if (!shown.length) html = `<li class="srch-empty" role="presentation">No ${type === "model" ? "model" : type === "bench" ? "benchmark" : "model or benchmark"} matches “${esc(q)}”.</li>`;
      list.innerHTML = html;
      setActive(q && shown.length ? 0 : -1);
    }
    function setActive(i) {
      const old = list.querySelector('[aria-selected="true"]');
      if (old) old.setAttribute("aria-selected", "false");
      active = i;
      const li = i >= 0 && $("#srch-" + i);
      if (li) { li.setAttribute("aria-selected", "true"); li.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", li.id); }
      else input.removeAttribute("aria-activedescendant");
    }
    const isOpen = () => !pop.hidden;
    function open() {
      if (isOpen()) return;
      pop.hidden = false;
      input.setAttribute("aria-expanded", "true");
      render();
    }
    function close() {
      pop.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }
    function pick(it) {
      close();
      input.value = "";
      input.blur();
      openSearch(false);
      go(it.href);
    }

    input.addEventListener("focus", open);
    input.addEventListener("input", () => { open(); render(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen()) return open();
        if (shown.length) setActive((active + (e.key === "ArrowDown" ? 1 : shown.length - 1 + (active < 0 ? 1 : 0))) % shown.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const it = shown[active >= 0 ? active : 0];
        if (it && isOpen()) pick(it);
      } else if (e.key === "Escape") {
        if (isOpen()) { e.stopPropagation(); close(); }
      }
    });
    // keep the focus in the box while using the panel
    pop.addEventListener("mousedown", (e) => e.preventDefault());
    types.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-type]");
      if (!btn) return;
      type = btn.dataset.type;
      store.set("search-type", type);
      render();
    });
    list.addEventListener("click", (e) => {
      const a = e.target.closest("a[data-i]");
      if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
      e.preventDefault();
      pick(shown[Number(a.dataset.i)]);
    });
    list.addEventListener("mousemove", (e) => {
      const a = e.target.closest("a[data-i]");
      if (a && Number(a.dataset.i) !== active) setActive(Number(a.dataset.i));
    });
    // the click's path as it was dispatched: still right if the clicked element has since been redrawn
    const box = $(".search");
    document.addEventListener("click", (e) => { if (isOpen() && !e.composedPath().includes(box)) close(); });
    input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) close(); }, 0));
  }

  function initChrome() {
    // phones and tablets: the search button opens the box as a row under the bar
    const topbar = $(".topbar"), searchBtn = $(".search-btn"), search = $("#site-search");
    const openSearch = (open) => {
      topbar.classList.toggle("searching", open);
      searchBtn.setAttribute("aria-expanded", String(open));
      if (open) search.focus();
    };
    searchBtn.addEventListener("click", () => openSearch(!topbar.classList.contains("searching")));
    document.addEventListener("click", (e) => { if (topbar.classList.contains("searching") && !e.composedPath().includes(topbar)) openSearch(false); });
    search.addEventListener("keydown", (e) => { if (e.key === "Escape" && topbar.classList.contains("searching")) { openSearch(false); searchBtn.focus(); } });
    initSearch(openSearch);
  }

  function renderError(err) {
    main.innerHTML = `<div class="page">${pageHead("Error", "The score database could not be loaded.", `${esc(err.message)}. Please try again in a moment.`)}</div>`;
    booted();
  }

  // links from before pages had their own paths: #/model/x -> /model/x
  if (location.hash.startsWith("#/")) history.replaceState(null, "", "/" + location.hash.slice(2));
  // the first page's data loads alongside the site's (route() then finds it loading)
  const first = pageOf(location.pathname);
  if (first && first[3]) load(first[3](argOf(first, location.pathname))).catch(() => {});
  load("/data/site.json")
    .then((data) => {
      D = data;
      buildIndex();
      initChrome();
      window.addEventListener("popstate", route);
      route();
    })
    .catch(renderError);
})();
