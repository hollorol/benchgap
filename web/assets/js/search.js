/* The top bar's search: models and benchmarks, filtered by type. */
import { html, render } from "./vendor/lit-html.js";
import { D, ix, $, store, capLabel, benchHref, modelHref, go } from "./core.js";
import { dot } from "./ui.js";

const fold = (t) => t.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
const SEARCH_TYPES = [["all", "All"], ["model", "Models"], ["bench", "Benchmarks"]];
const SEARCH_GROUPS = Object.fromEntries(SEARCH_TYPES.slice(1));
const BENCH_ICON = html`<svg class="srch-ic" viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="7" width="3" height="7" rx=".6"/><rect x="6.5" y="3" width="3" height="11" rx=".6"/><rect x="11" y="9" width="3" height="5" rx=".6"/></svg>`;

function initSearch(openSearch) {
  const input = $("#site-search"), pop = $("#search-pop"), list = $("#search-results"), types = $(".search-types");
  const items = [
    ...ix.listedModels.map((m) => ({ type: "model", label: m.name, text: fold(m.name + " " + m.slug), href: modelHref(m),
      meta: `${D.meta.providers[m.provider] || "Other"} · ${m.n_measured} measured`, weight: m.n_measured, icon: dot(m) })),
    // the listed benchmarks, the original ones first
    ...ix.listed.map((b) => ({ type: "bench", label: b.label, text: fold(b.label + " " + b.key), href: benchHref(b),
      meta: `${capLabel(b.capability)} · ${b.n_measured} measured`, weight: b.n_measured + (b.featured ? 1e4 : 0), icon: BENCH_ICON })),
  ];
  let type = store.get("search-type", "all");
  if (!SEARCH_TYPES.some(([t]) => t === type)) type = "all";
  let shown = [], active = -1;

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
    const out = [];
    for (let i = 0; i < label.length; ) {
      let j = i; while (j < label.length && on[j] === on[i]) j++;
      out.push(on[i] ? html`<mark>${label.slice(i, j)}</mark>` : label.slice(i, j));
      i = j;
    }
    return out;
  }

  function draw() {
    const q = input.value.trim();
    // nothing typed: the most measured models and the original benchmarks
    const pool = q ? matches(q) : items.slice().sort((a, b) => b.weight - a.weight);
    const n = { all: pool.length, model: 0, bench: 0 };
    pool.forEach((it) => n[it.type]++);
    // the type buttons keep their elements: replacing them under a click would make it look like a click outside
    render(SEARCH_TYPES.map(([t, label]) => html`<button type="button" data-type="${t}" aria-pressed="${t === type}">${label}<span class="cnt">${q ? n[t] : ""}</span></button>`), types);
    const per = type === "all" ? (q ? 6 : 4) : 50;
    shown = [];
    const rows = [];
    for (const t of type === "all" ? ["model", "bench"] : [type]) {
      const group = pool.filter((it) => it.type === t);
      if (!group.length) continue;
      const some = group.slice(0, per);
      rows.push(html`<li class="srch-group" role="presentation">${q ? SEARCH_GROUPS[t] : `Popular ${SEARCH_GROUPS[t].toLowerCase()}`}${q ? html`<span>${some.length < group.length ? `${some.length} of ${group.length}` : group.length}</span>` : ""}</li>`);
      for (const it of some) {
        const i = shown.push(it) - 1;
        rows.push(html`<li role="option" id="srch-${i}" aria-selected="false"><a href="${it.href}" tabindex="-1" data-i="${i}">${it.icon}<span class="srch-name">${q ? mark(it.label, q) : it.label}</span><span class="srch-meta">${it.meta}</span></a></li>`);
      }
    }
    if (!shown.length) rows.push(html`<li class="srch-empty" role="presentation">No ${type === "model" ? "model" : type === "bench" ? "benchmark" : "model or benchmark"} matches “${q}”.</li>`);
    render(rows, list);
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
    draw();
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
  input.addEventListener("input", () => { open(); draw(); });
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
    draw();
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

// the top bar: on phones and tablets the search button opens the box as a row under the bar
export function initChrome() {
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
