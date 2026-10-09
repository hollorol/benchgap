/* What every page shares: the site's data and its indexes, the documents loaded so far, the
 * user's preferences, the page's meta, and showing a page in <main>. */
import { render } from "./vendor/lit-html.js";

export const REPO_URL = "https://github.com/hollorol/benchgap";
// the ledes below are also on the pages serve.php renders (src/Pages.php): keep the two in step
export const ABOUT = "benchgap is an LLM benchmark leaderboard that fills in the missing scores. Most models are only "
  + "ever run on a handful of benchmarks, so benchgap calibrates benchmarks against each other on the models "
  + "measured on both, then estimates each missing score with its cross-validated error and a confidence level. "
  + "Measured and estimated scores are always marked apart.";

// --- state ------------------------------------------------------------------
export const store = {
  get(k, d) { try { const v = localStorage.getItem("bg-" + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("bg-" + k, JSON.stringify(v)); } catch (e) {} },
};
export const prefs = {
  show: store.get("show", "all"),         // "measured" | "reliable" | "all"
  dense: store.get("dense", true),
  sortCol: null,
};

export let D = null;    // what every page uses (data/site.json): metadata, capabilities, benchmarks, models
export const ix = {};   // indexes

export const $ = (sel, el) => (el || document).querySelector(sel);
export const main = $("#main");

export const pct = (v, d = 1) => (v * 100).toFixed(d);

// --- data loading & indexing ----------------------------------------------
// the documents loaded so far by URL, each a promise of its JSON; a failed one is dropped, to retry
export const docs = new Map();
export function load(url) {
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
export const boardUrl = (key) => `/data/b/${key.split("/").map(encodeURIComponent).join("/")}.json`;
export const modelUrl = (slug) => `/data/model/${encodeURIComponent(slug)}.json`;
export const scoreUrl = (s) => `/data/score/${s.m}/${s.b}.json`;

// list -> Map of key -> [items]
export function groupBy(list, key) {
  const out = new Map();
  for (const x of list) {
    const k = key(x);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(x);
  }
  return out;
}

// items grouped by capability, as [capability id, items], in the site's capability order
// (a capability the site does not know, last)
export function byCapability(items, capOfItem) {
  const groups = groupBy(items, capOfItem);
  const known = D.capabilities.filter((c) => groups.has(c.id)).map((c) => c.id);
  return [...known, ...[...groups.keys()].filter((c) => !known.includes(c))].map((c) => [c, groups.get(c)]);
}

// the site's data (data/site.json) and its indexes
export function setSite(data) {
  D = data;
  ix.bench = new Map(D.benchmarks.map((b) => [b.id, b]));
  ix.benchByKey = new Map(D.benchmarks.map((b) => [b.key, b]));
  ix.model = new Map(D.models.map((m) => [m.id, m]));
  ix.modelBySlug = new Map(D.models.map((m) => [m.slug, m]));
  ix.cap = new Map(D.capabilities.map((c) => [c.id, c]));
  ix.cell = new Map();   // the scores loaded so far, by "model:benchmark", for the tooltips
  // the site shows only what the backend lists (Snapshot::LISTED); counts come listed already
  ix.listed = D.benchmarks.filter((b) => b.listed);
  ix.home = D.meta.home;   // the home page's benchmark (Snapshot::home)
  ix.listedModels = D.models.filter((m) => m.listed);
  ix.benchesByCap = byCapability(ix.listed, (b) => b.capability).map(([id, benches]) => ({ cap: ix.cap.get(id), benches }));
}
// keeps loaded scores for the tooltips; a full score is never replaced by a matrix cell's partial one
export function remember(scores) {
  for (const s of scores) {
    const key = s.m + ":" + s.b, old = ix.cell.get(key);
    if (!old || old.partial || !s.partial) ix.cell.set(key, s);
  }
  return scores;
}

export const methodLabel = (k) => D.meta.methods[k] || k;
export const capLabel = (id) => (ix.cap.get(id) || {}).label || id;
export const capOf = (id) => ix.bench.get(id).capability;   // a benchmark's capability
export const benchLabel = (id) => (ix.bench.get(id) || {}).label || "?";

export const visible = (s) =>
  s.s === "m" || (prefs.show === "all") || (prefs.show === "reliable" && s.tier !== "low");

// the site's paths
export const benchHref = (b) => `/b/${b.key}`;
export const modelHref = (m) => `/model/${encodeURIComponent(m.slug)}`;
export const mappingHref = (id) => `/calibration/${id}`;

// --- pages ------------------------------------------------------------------
// title, description and canonical path of the current page, for search engines and tabs;
// a null path marks a page not to index (not found)
export function setMeta(title, description, path) {
  document.title = `${title} · benchgap`;
  $('meta[name="description"]').content = description;
  $('link[rel="canonical"]').href = location.origin + (path ?? location.pathname);
  $('meta[name="robots"]').content = path === null ? "noindex" : "index, follow";
}

// a page's template into <main>; the first one replaces the plain-HTML summary serve.php sent,
// and <main> no longer hides (index.html, .booting). The same page again (another model, say)
// updates what changed instead of redrawing it all
export let drawn = false;
export function show(template) {
  if (!drawn) {
    main.replaceChildren();
    document.documentElement.classList.remove("booting");
    drawn = true;
  }
  render(template, main);
}

// opens a page of the site without a reload (app.js routes it on popstate)
export function go(path) {
  if (path === location.pathname) return;
  history.pushState(null, "", path);
  dispatchEvent(new PopStateEvent("popstate"));
}
