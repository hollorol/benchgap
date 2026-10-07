/* benchgap.net front-end: renders data/benchgap.json (computed by serve.php).
 *
 * No framework (CI only minifies it): each page has its own path (serve.php
 * serves it with a plain-HTML summary in <main>, which this replaces); old #/
 * links are forwarded.
 *   /                       leaderboard (default benchmark)
 *   /b/<benchmark/version>  leaderboard for one benchmark
 *   /matrix                 models x benchmarks score matrix
 *   /model/<slug>           one model across all benchmarks
 *   /calibration            predictability matrix + list of mappings
 *   /calibration/<id>       one fitted mapping (scatter + curve)
 *   /method                 methodology
 *   /api                    public API documentation (api/v1/)
 */
(function () {
  "use strict";

  const DATA_URL = "/data/benchgap.json";
  const REPO_URL = "https://github.com/hollorol/benchgap";
  const DEFAULT_BENCH = "terminal-bench/4.0";
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

  let D = null;           // raw data
  const ix = {};          // indexes

  const $ = (sel, el) => (el || document).querySelector(sel);
  const main = $("#main");
  const tip = $("#tip");

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v, d = 1) => (v * 100).toFixed(d);

  // --- data loading & indexing ----------------------------------------------
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
    ix.mapping = new Map(D.mappings.map((m) => [m.id, m]));
    ix.cap = new Map(D.capabilities.map((c) => [c.id, c]));
    ix.cell = new Map(D.scores.map((s) => [s.m + ":" + s.b, s]));
    ix.byBench = groupBy(D.scores, (s) => s.b);
    ix.byModel = groupBy(D.scores, (s) => s.m);
    ix.benchesByCap = D.capabilities.map((c) => ({ cap: c, benches: D.benchmarks.filter((b) => b.capability === c.id) }));
    ix.estByMapping = groupBy(D.scores.filter((s) => s.s === "e" && s.via.kind === "uni"), (s) => s.via.mapping);
    ix.mappingByPair = new Map(D.mappings.map((m) => [m.from + ":" + m.to, m]));
    // per-benchmark range of measured scores, for the matrix tint
    ix.measuredRange = new Map(D.benchmarks.map((b) => {
      const vals = (ix.byBench.get(b.id) || []).filter((s) => s.s === "m").map((s) => s.v);
      return [b.id, [Math.min(...vals), Math.max(...vals)]];
    }));
  }

  const methodLabel = (k) => D.meta.methods[k] || k;
  const capLabel = (id) => (ix.cap.get(id) || {}).label || id;
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
  const fitHref = (s) => (s.s === "e" && s.via.kind === "uni" ? mappingHref(s.via.mapping) : "");

  function describeEstimate(s, link) {
    const b = ix.bench.get(s.b);
    const lines = [];
    lines.push(`<div class="t-h">${esc(b.label)}<span class="t-tier ${s.tier}">${s.tier} confidence</span></div>`);
    if (link) lines.push(modelLine(s));
    lines.push(`<div class="t-v"><i>≈ ${pct(s.v)}%</i> <span class="t-note">± ${pct(s.sd)} pp</span></div>`);
    lines.push(`<div>Estimated from ${sourceList(s, link)} via ${esc(methodLabel(s.method))}, fitted on ${s.via.n} models measured on both.</div>`);
    if (s.why && s.why.length) lines.push(`<ul>${s.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`);
    else lines.push(`<div class="t-note">Low cross-validated error, inside the fitted range.</div>`);
    lines.push(`<div class="t-note">Not a measured score.</div>`);
    return lines.join("");
  }
  function describeMeasured(s, link) {
    const b = ix.bench.get(s.b);
    return `<div class="t-h">${esc(b.label)} · measured</div>${link ? modelLine(s) : ""}`
      + `<div class="t-v">${pct(s.v)}%</div><div class="t-note">Reported by the ${esc(b.harness)} harness.</div>`;
  }
  let tipEl = null;        // element the tooltip currently describes
  let tipSize = null;      // its measured size, so moves don't re-measure
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
      if (s) return s.s === "e" ? describeEstimate(s, link) : describeMeasured(s, link);
    }
    const raw = el.getAttribute("data-tiptext");
    return raw ? esc(raw) : null;
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
  const benchChip = (x, b) => `<a class="chip" href="${benchHref(x)}" aria-current="${x.id === b.id}">${esc(x.label)}<span class="cnt">${x.n_measured}${x.n_estimated ? "+" + x.n_estimated : ""}</span></a>`;
  function railHTML(b) {
    const benches = ix.benchesByCap.find(({ cap }) => cap.id === b.capability).benches;
    if (benches.length < 2) return "";
    return `<p class="rail-cap">Also in <b>${esc(capLabel(b.capability))}</b></p><nav class="rail chip-row" aria-label="${esc(capLabel(b.capability))} benchmarks">${benches
      .map((x) => benchChip(x, b))
      .join("")}</nav>`;
  }
  // scrolls the rail (phones only) so the current benchmark's chip is in the middle
  const phone = matchMedia("(max-width: 760px)");
  function centerRail() {
    if (!phone.matches) return;
    const rail = $("#bench-rail .rail"), chip = rail && rail.querySelector('[aria-current="true"]');
    if (chip) rail.scrollLeft += chip.getBoundingClientRect().left - rail.getBoundingClientRect().left - (rail.clientWidth - chip.offsetWidth) / 2;
  }

  // Returns true when only the chart was swapped (already on the leaderboard).
  function renderBoard(key) {
    const b = ix.benchByKey.get(key);
    if (!b) return renderNotFound(`No benchmark “${esc(key)}”.`);
    if (key === DEFAULT_BENCH) setMeta("LLM Benchmark Leaderboard with Estimated Scores",
      "LLM benchmark scores: measured where available, estimated where missing, with every estimate's error and confidence.", "/");
    else setMeta(`${b.label} leaderboard`, `${b.label} leaderboard: ${b.n_measured} measured and ${b.n_estimated} estimated LLM scores, each estimate with its error and confidence.`);
    if ($("#board-sec")) {
      main.querySelectorAll(".picker .chip").forEach((a) => a.setAttribute("aria-current", String(a.getAttribute("href") === benchHref(b))));
      $("#bench-select").value = b.key;
      $("#bench-rail").innerHTML = railHTML(b);
      renderBoardBody(b);
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

    const picker = `<nav class="picker" aria-label="Benchmarks">${ix.benchesByCap
      .map(
        ({ cap, benches }) => `<div class="picker-row"><div class="cap">${esc(cap.label)}</div><div class="chips">${benches
          .map(
            (x) => benchChip(x, b)
          )
          .join("")}</div></div>`
      )
      .join("")}</nav>`;

    const pickerMobile = `<div class="picker-mobile"><label><span class="ctl-label">Benchmark · ${D.benchmarks.length} to pick from</span><select class="select" id="bench-select">${ix.benchesByCap
      .map(({ cap, benches }) => `<optgroup label="${esc(cap.label)}">${benches
        .map((x) => `<option value="${esc(x.key)}" ${x.id === b.id ? "selected" : ""}>${esc(x.label)} (${x.n_measured}${x.n_estimated ? " + " + x.n_estimated + " est." : ""})</option>`)
        .join("")}</optgroup>`)
      .join("")}</select></label><div id="bench-rail">${railHTML(b)}</div></div>`;

    main.innerHTML = `<div class="page">${hero}${picker}${pickerMobile}<section id="board-sec"></section></div>`;
    $("#bench-select").addEventListener("change", (e) => go(benchHref({ key: e.target.value })));
    renderBoardBody(b);
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

  // header, controls and legend for one benchmark; the rows live in #board-rows
  function renderBoardBody(b) {
    const sec = $("#board-sec");
    const all = ix.byBench.get(b.id) || [];
    const nEst = b.n_estimated;
    const nLow = all.filter((s) => s.s === "e" && s.tier === "low").length;
    sec.innerHTML = `
      <div class="board-head">
        <div>
          <div class="eyebrow">${esc(capLabel(b.capability))}</div>
          <h2 class="h2" style="margin-top:.4rem">${esc(b.label)}</h2>
        </div>
        <div class="src">${b.n_measured} measured · <i>${nEst} estimated</i>${nLow ? ` (${nLow} low confidence)` : ""}
          ${b.source_url ? ` · source: <a href="${esc(b.source_url)}" rel="noopener" target="_blank">${esc(b.harness)}</a>` : ""}</div>
      </div>
      <p class="lede board-lead">${esc(benchLead(b, all))}</p>
      <div class="controls">${showSeg()}</div>
      ${legendHTML()}
      <div id="board-rows"></div>
      ${nEst === 0 ? `<p class="muted" style="margin-top:1rem">No estimates for this benchmark: no same-capability benchmark calibrates it well enough (see <a href="/calibration">Calibration</a>).</p>` : ""}`;
    bindShowSeg(sec, () => renderBoardRows(b));
    renderBoardRows(b);
  }

  // a 0..max axis for values up to hi, max rounded up to a tenth, with its ticks
  function axis(hi) {
    const max = Math.min(1, Math.ceil(hi * 10) / 10), step = max > 0.5 ? 0.1 : 0.05, ticks = [];
    for (let v = 0; v <= max + 1e-9; v += step) ticks.push(v);
    return { max, ticks };
  }

  function renderBoardRows(b) {
    const rows = (ix.byBench.get(b.id) || []).filter(visible).sort((p, q) => q.v - p.v);
    const hi = Math.max(0.1, ...rows.map((s) => s.v + (s.s === "e" ? s.sd : 0)));
    const { max: axisMax, ticks } = axis(hi);
    const X = (v) => Math.max(0, Math.min(100, (v / axisMax) * 100));

    const body = rows
      .map((s, i) => {
        const m = ix.model.get(s.m);
        const est = s.s === "e";
        const rank = est ? `≈${i + 1}` : String(i + 1);
        return `<div class="row ${est ? "e " + s.tier : "m"}" data-p="${m.provider}" style="--d:${Math.min(i, 30) * 14}ms">
          <div class="rank ${est ? "est" : ""}">${rank}</div>
          <div class="who">${dot(m)}${modelLink(m)}${tierFlag(s)}</div>
          <div class="track" data-tip="${s.m}:${s.b}" tabindex="0" aria-label="${esc(m.name)}: ${est ? "estimated " : ""}${pct(s.v)} percent">${trackHTML(s, X, ticks)}</div>
          <div class="val">${est ? "≈" : ""}${pct(s.v)}%${est ? `<span class="pm">±${pct(s.sd)}</span>` : ""}</div>
        </div>`;
      })
      .join("");

    hideTip();
    $("#board-rows").innerHTML = `
      <div class="board" role="list">
        <div class="axis" aria-hidden="true"><span></span><span></span>
          <div class="ticks">${ticks.map((t) => `<span style="left:${X(t)}%">${Math.round(t * 100)}</span>`).join("")}</div><span></span></div>
        ${body || `<p class="empty">No scores to show with the current filter.</p>`}
      </div>`;
  }

  // --- page: matrix -----------------------------------------------------------
  let matrixCells = null;   // "m:b" -> cell class at the last redraw (null: first draw)

  function renderMatrix() {
    setMeta("LLM benchmark score matrix", "Every model on every benchmark: measured LLM scores and calibrated estimates for the missing ones, side by side.");
    matrixCells = null;
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
    const sortBy = (el) => { const id = Number(el.dataset.sort); prefs.sortCol = prefs.sortCol === id ? null : id; renderMatrixTable(); };
    wrap.addEventListener("click", (e) => { const el = e.target.closest("[data-sort]"); if (el) sortBy(el); });
    wrap.addEventListener("keydown", (e) => {
      const el = e.target.closest("[data-sort]");
      if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortBy(el); }
    });
    renderMatrixTable();
  }

  function renderMatrixTable() {
    const benches = D.benchmarks.filter((b) => !prefs.dense || b.dense);
    let models = D.models.filter((m) => !prefs.dense || m.dense);

    const cellOf = (m, b) => {
      const s = ix.cell.get(m.id + ":" + b.id);
      return s && visible(s) ? s : null;
    };
    if (prefs.sortCol && benches.some((b) => b.id === prefs.sortCol)) {
      const b = ix.bench.get(prefs.sortCol);
      models = models.slice().sort((p, q) => {
        const a = cellOf(p, b), c = cellOf(q, b);
        return (c ? c.v : -1) - (a ? a.v : -1) || p.name.localeCompare(q.name);
      });
    } else {
      models = models.slice().sort((p, q) => q.n_measured - p.n_measured || p.name.localeCompare(q.name));
    }

    const spans = [];
    benches.forEach((b) => {
      const last = spans[spans.length - 1];
      if (last && last.cap === b.capability) last.n++;
      else spans.push({ cap: b.capability, n: 1 });
    });
    const brk = (j) => (j > 0 && benches[j].capability !== benches[j - 1].capability ? " brk" : "");

    let shown = 0, est = 0, low = 0, gaps = 0;
    const prev = matrixCells;
    const cells = new Map();
    // "changed" marks a cell whose content differs from the previous redraw
    const mark = (m, b, kind) => {
      const key = m.id + ":" + b.id;
      cells.set(key, kind);
      return prev && prev.get(key) !== kind ? " changed" : "";
    };
    const body = models
      .map((m) => {
        const tds = benches
          .map((b, j) => {
            const s = cellOf(m, b);
            if (!s) { gaps++; return `<td class="gap${brk(j)}${mark(m, b, "gap")}">·</td>`; }
            shown++;
            if (s.s === "e") {
              est++; if (s.tier === "low") low++;
              return `<td class="e ${s.tier}${brk(j)}${mark(m, b, "e")}" data-tip="${m.id}:${b.id}" tabindex="0">${pct(s.v, 0)}</td>`;
            }
            const [lo, hi] = ix.measuredRange.get(b.id);
            const h = hi > lo ? (s.v - lo) / (hi - lo) : 0.5;
            return `<td class="m${brk(j)}${mark(m, b, "m")}" style="--h:${(0.15 + h * 0.85).toFixed(2)}" data-tip="${m.id}:${b.id}">${pct(s.v, 0)}</td>`;
          })
          .join("");
        return `<tr><th scope="row"><a href="${modelHref(m)}" title="${esc(m.name)}">${dot(m)}<span>${esc(m.name)}</span></a></th>${tds}</tr>`;
      })
      .join("");

    matrixCells = cells;
    hideTip();
    $("#mx-counts").innerHTML = `${models.length} models · ${benches.length} benchmarks · ${shown - est} measured · <i>${est} estimated</i> (${low} low confidence) · ${gaps} gaps`;
    const wrap = $("#mx-wrap");
    wrap.innerHTML = `
        <table class="mx">
          <thead>
            <tr class="caps"><th class="corner" rowspan="2">Model</th>${spans
              .map((sp, k) => `<th colspan="${sp.n}" class="${k > 0 ? "brk" : ""}" title="${esc(capLabel(sp.cap))}">${esc(capLabel(sp.cap))}</th>`)
              .join("")}</tr>
            <tr class="cols">${benches
              .map((b, j) => `<th class="${prefs.sortCol === b.id ? "sorted" : ""}${brk(j)}"><span class="colh" data-sort="${b.id}" role="button" tabindex="0" title="Sort by ${esc(b.label)}">${esc(b.label)}</span></th>`)
              .join("")}</tr>
          </thead>
          <tbody>${body}</tbody>
        </table>`;
  }

  // --- page: model ------------------------------------------------------------
  function renderModel(slug) {
    const m = ix.modelBySlug.get(slug);
    if (!m) return renderNotFound(`No model “${esc(slug)}”.`);
    setMeta(`${m.name} benchmark scores`, `${m.name} benchmark scores: measured on ${m.n_measured} benchmark${m.n_measured === 1 ? "" : "s"}`
      + (m.n_estimated ? `, estimated on ${m.n_estimated} more, with the error and confidence of each estimate.` : "."));
    const scores = ix.byModel.get(m.id) || [];
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

  function renderCalibration() {
    setMeta("LLM benchmark calibrations", "Which LLM benchmarks predict which: the fitted cross-benchmark calibrations behind every estimate, with their errors.");
    const gate = D.meta.quality_gate.max_loo_pp;
    const involved = new Set();
    D.mappings.forEach((m) => { involved.add(m.from); involved.add(m.to); });
    const vs = D.benchmarks.filter((b) => involved.has(b.id));
    // phones show the pair map one capability at a time (.pm-off hides the others), the one with the most mappings first
    const nByCap = groupBy(D.mappings, (m) => ix.bench.get(m.from).capability);
    const caps = D.capabilities.filter((c) => nByCap.has(c.id)).map((c) => ({ id: c.id, label: c.label, n: nByCap.get(c.id).length }));
    const cap = caps.reduce((a, c) => (c.n > a.n ? c : a)).id;
    const off = (b) => (b.capability === cap ? "" : " pm-off");
    const head = vs.map((b) => `<th class="${off(b)}"><span class="colh">${esc(b.label)}</span></th>`).join("");
    const body = vs
      .map((src, i) => {
        const cells = vs
          .map((dst, j) => {
            if (i === j) return `<td class="diag${off(dst)}"></td>`;
            if (src.capability !== dst.capability) return `<td class="na${off(dst)}"></td>`;
            const m = ix.mappingByPair.get(src.id + ":" + dst.id);
            if (!m) return `<td class="none${off(dst)}" data-tiptext="${esc(src.label)} → ${esc(dst.label)}: no usable mapping (too few shared models, or the best fit failed the quality gate)"></td>`;
            return `<td class="cell${off(dst)}" style="background:${lossColor(m.loo * 100, gate)}"><a href="${mappingHref(m.id)}" data-tiptext="${esc(src.label)} → ${esc(dst.label)}: ${esc(methodLabel(m.method))}, n=${m.n}, R²=${m.r2.toFixed(2)}, LOO error ${pct(m.loo)} pp, used for ${m.n_used} estimates">${pct(m.loo)}</a></td>`;
          })
          .join("");
        return `<tr class="${off(src)}"><th scope="row">${esc(src.label)}</th>${cells}</tr>`;
      })
      .join("");

    const list = D.mappings
      .slice()
      .sort((a, b) => a.loo - b.loo)
      .map((m) => {
        const f = ix.bench.get(m.from), t = ix.bench.get(m.to);
        return `<tr><td><a href="${mappingHref(m.id)}">${esc(f.label)} → ${esc(t.label)}</a></td><td>${esc(methodLabel(m.method))}</td>
          <td class="num">${m.n}</td><td class="num">${m.r2.toFixed(3)}</td><td class="num err"><span class="scale-dot" style="background:${lossColor(m.loo * 100, gate)}"></span>${pct(m.loo)}</td><td class="num">${m.n_used}</td></tr>`;
      })
      .join("");

    main.innerHTML = `<div class="page">
      ${pageHead("Calibration", "Which benchmarks predict which", `For each ordered pair of same-capability benchmarks with at least ${D.meta.quality_gate.min_pairs} shared models,
        several monotone curves are fitted and the one with the lowest leave-one-out error is kept, if it passes the quality gate
        (R² ≥ ${D.meta.quality_gate.min_r2}, error ≤ ${gate} pp). Cells show that error in percentage points: rows are the source, columns the target.`)}
      <section class="section">
        <div class="pm-caps chip-row" role="group" aria-label="Capability">${caps
          .map((c) => `<button type="button" class="chip" data-cap="${c.id}" aria-pressed="${c.id === cap}">${esc(c.label)}<span class="cnt">${c.n}</span></button>`)
          .join("")}</div>
        <div class="pm-wrap"><table class="pm"><thead><tr><th style="text-align:right;vertical-align:bottom" class="muted">source ↓ · target →</th>${head}</tr></thead><tbody>${body}</tbody></table></div>
        <div class="scale"><span>0 pp</span><span class="ramp" style="background:${LOSS_RAMP}"></span><span>${gate} pp (gate)</span>
          <span style="margin-left:1rem"><span class="sq none"></span>no usable mapping</span>
          <span><span class="sq diag"></span>same benchmark</span>
          <span>blank: different capability, never fitted</span></div>
      </section>
      <section class="section">
        <h2 class="h2">All fitted mappings</h2>
        <div class="list-wrap"><table class="list maps"><thead><tr><th>Mapping</th><th>Selected curve</th><th style="text-align:right">n</th><th style="text-align:right">R²</th><th style="text-align:right">LOO error (pp)</th><th style="text-align:right">Estimates</th></tr></thead><tbody>${list}</tbody></table></div>
      </section></div>`;

    // a chip re-marks the rows and columns to hide
    const showCap = (id) => {
      main.querySelectorAll(".pm-caps .chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.cap === id)));
      main.querySelectorAll(".pm tr").forEach((tr, i) => {
        tr.classList.toggle("pm-off", i > 0 && vs[i - 1].capability !== id);
        [...tr.children].forEach((cell, j) => cell.classList.toggle("pm-off", j > 0 && vs[j - 1].capability !== id));
      });
    };
    main.querySelectorAll(".pm-caps .chip").forEach((c) => c.addEventListener("click", () => showCap(c.dataset.cap)));
  }

  function renderMapping(id) {
    const m = ix.mapping.get(Number(id));
    if (!m) return renderNotFound("No such mapping.");
    const f = ix.bench.get(m.from), t = ix.bench.get(m.to);
    setMeta(`${f.label} → ${t.label} calibration`, `How ${f.label} scores predict ${t.label}: the fitted ${methodLabel(m.method)} curve, the models it was fitted on and its cross-validated error.`);
    const ests = ix.estByMapping.get(m.id) || [];
    const reverse = ix.mappingByPair.get(m.to + ":" + m.from);

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
            <span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="currentColor"/></svg> measured on both</span>
            <span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-high)" stroke-width="2"/></svg> high</span>
            <span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-medium)" stroke-width="2"/></svg> medium</span>
            <span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="var(--conf-low)" stroke-width="2" stroke-dasharray="2 1.5"/></svg> low confidence</span>
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
        <p>Measured scores come from public evaluation leaderboards (harness: ${esc(D.meta.harnesses.join(", "))}),
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
      ["model", "string", "Model slug, e.g. <code>gpt-6-astra-high</code>."],
      ["benchmark", "string", "Benchmark key <code>name/version</code>, e.g. <code>terminal-bench/4.0</code>."],
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
      curl: `curl ${base}benchmarks/terminal-bench/4.0.json`,
      Python: `import requests

data = requests.get("${base}benchmarks/terminal-bench/4.0.json").json()
for s in data["scores"]:
    est = s["estimate"]
    if est and est["confidence"] == "low":
        continue  # skip low-confidence estimates
    print(s["model"], round(s["score"] * 100, 1), s["source"])`,
      JavaScript: `const res = await fetch("${base}models/gpt-6-astra-high.json");
const { model, scores } = await res.json();
const measured = scores.filter((s) => s.source === "measured");
console.log(model.name, measured.length, "measured scores");`,
      pandas: `import pandas as pd

df = pd.read_csv("${base}scores.csv")
measured = df[df.source == "measured"]
table = measured.pivot(index="model", columns="benchmark", values="score")`,
    };
  }

  function renderApi() {
    setMeta("Public API", "Free JSON and CSV API for LLM benchmark scores, measured and estimated, with an OpenAPI 3.1 description.");
    const base = API_BASE;
    const samples = codeSamples(base);
    // every resource the API serves, grouped for the "Try it" picker
    const tryGroups = [
      ["Lists", API_ENDPOINTS.map(([p]) => p).filter((p) => !p.includes("{"))],
      ["Benchmarks", D.benchmarks.map((b) => `benchmarks/${b.key}.json`)],
      ["Models", D.models.map((m) => `models/${m.slug}.json`)],
      ["Calibrations", D.mappings.slice().sort((a, b) => a.id - b.id).map((m) => `mappings/${m.id}.json`)],
    ];
    const tryDefault = `benchmarks/${DEFAULT_BENCH}.json`;
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
        <p>The benchmark data is collected from public leaderboards (each benchmark's <code>source_url</code>,
        mainly Artificial Analysis) and remains under its sources' terms; check them before republishing scores.
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
  // argument; a renderer sets the page's meta and returns true if it updated the page in place
  const PAGES = [
    [/^\/$/, "board", () => renderBoard(DEFAULT_BENCH)],
    [/^\/b\/(.+)$/, "board", renderBoard],
    [/^\/model\/(.+)$/, "", renderModel],
    [/^\/matrix$/, "matrix", renderMatrix],
    [/^\/calibration$/, "calibration", renderCalibration],
    [/^\/calibration\/(\d+)$/, "calibration", renderMapping],
    [/^\/method$/, "method", renderMethod],
    [/^\/api$/, "api", renderApi],
  ];
  const pageOf = (path) => PAGES.find(([re]) => re.test(path));

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

  let shownPath = null;   // the path the page was last rendered for
  const scrollToHash = () => { const el = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1))); if (el) el.scrollIntoView(); return !!el; };
  function route() {
    hideTip();
    const path = location.pathname;
    if (path === shownPath) return scrollToHash();   // only the #fragment changed: same page, no re-render
    shownPath = path;
    const page = pageOf(path);
    const nav = page ? page[1] : "";
    const inPlace = page ? page[2](decodeURIComponent(path.match(page[0])[1] || "")) : renderNotFound("Page not found.");
    document.querySelectorAll("[data-nav]").forEach((a) => (a.dataset.nav === nav ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
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
  function initChrome() {
    $("#model-list").innerHTML = D.models.map((m) => `<option value="${esc(m.name)}"></option>`).join("");
    const search = $("#model-search");
    const names = D.models.map((m) => [m.name.toLowerCase(), m]);
    // exact: only a full model name (a picked suggestion, or leaving the box); otherwise
    // the first name containing the query (Enter)
    // phones and tablets: the search button opens the box as a row under the bar
    const topbar = $(".topbar"), searchBtn = $(".search-btn");
    const openSearch = (open) => {
      topbar.classList.toggle("searching", open);
      searchBtn.setAttribute("aria-expanded", String(open));
      if (open) search.focus();
    };
    searchBtn.addEventListener("click", () => openSearch(!topbar.classList.contains("searching")));
    document.addEventListener("click", (e) => { if (topbar.classList.contains("searching") && !topbar.contains(e.target)) openSearch(false); });
    search.addEventListener("keydown", (e) => { if (e.key === "Escape") { openSearch(false); searchBtn.focus(); } });
    const find = (exact) => {
      const q = search.value.trim().toLowerCase();
      if (!q) return;
      const hit = names.find(([n]) => n === q) || (!exact && names.find(([n]) => n.includes(q)));
      if (hit) { go(modelHref(hit[1])); search.value = ""; search.blur(); openSearch(false); }
    };
    // picking a datalist suggestion fires "input" (not always "change") with no typing inputType
    search.addEventListener("input", (e) => { if (!e.inputType || e.inputType === "insertReplacementText") find(true); });
    search.addEventListener("change", () => find(true));
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") find(false); });
  }

  // links from before pages had their own paths: #/model/x -> /model/x
  if (location.hash.startsWith("#/")) history.replaceState(null, "", "/" + location.hash.slice(2));
  fetch(DATA_URL, { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(r.status + " " + r.statusText); return r.json(); })
    .then((data) => {
      D = data;
      buildIndex();
      initChrome();
      window.addEventListener("popstate", route);
      route();
    })
    .catch((err) => {
      main.innerHTML = `<div class="page">${pageHead("Error", "The score database could not be loaded.", `${esc(err.message)}. Please try again in a moment.`)}</div>`;
    });
})();
