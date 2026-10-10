/* benchgap.net front-end: renders the site data that serve.php computes.
 *
 * Every page loads data/site.json (benchmarks, models, counts) and its own
 * slice of the scores (data/..., src/Site.php), never the whole database, and
 * its own code: this file is the router, each page a module of js/pages/ loaded
 * when it is first opened. Pages are lit-html templates (js/vendor/lit-html.js),
 * so a redraw updates only what changed. In the browser the modules load as
 * they are; CI bundles them (esbuild: this file and a chunk per page, deploy.yml).
 * Each page has its own path (serve.php serves it with a plain-HTML summary in
 * <main>, which this replaces); old #/ links are forwarded.
 *   /                       leaderboard (default benchmark)
 *   /b/<benchmark/version>  leaderboard for one benchmark
 *   /matrix                 models x benchmarks score matrix
 *   /compare                two models side by side, or one and the frontier model closest to it
 *   /model/<slug>           one model across all benchmarks
 *   /calibration            predictability matrix + list of mappings
 *   /calibration/<id>       one fitted mapping (scatter + curve)
 *   /multivariate           each benchmark from several others (fit + predicted vs measured)
 *   /harness-tax            how harnesses disagree about the same models (measured scores only)
 *   /method                 methodology
 *   /publications           the papers behind the method, and how benchgap relates
 *   /api                    public API documentation (api/v1/)
 */
import { $, main, load, boardUrl, modelUrl, setSite, drawn, go } from "./js/core.js";
import { hideTip } from "./js/tip.js";
import { centerIn, pillNav, renderNotFound, renderError } from "./js/ui.js";
import { initChrome } from "./js/search.js";

// --- router -----------------------------------------------------------------
// each page's module, loaded when the page is first opened. Its promise is kept: a new import()
// of a loaded module resolves only a task later, after the browser restored the scroll position
// of a page opened with Back, which the page drawn then would undo
const lazy = (importer) => {
  let loading = null;
  return () => (loading ??= importer().catch((err) => { loading = null; throw err; }));
};
const board = lazy(() => import("./js/pages/board.js"));
const calibration = lazy(() => import("./js/pages/calibration.js"));
// the site's pages (keep in step with serve.php): path, nav item, module (code), its renderer
// (render) and the URL of the page's data (none: the page needs only site.json). A renderer gets
// the data and the path's argument, sets the page's meta and returns true if it updated the page
// in place; its data is null if the server has none for the argument (404)
const PAGES = [
  { re: /^\/$/, nav: "board", code: board, render: "renderBoard", data: () => "/data/home.json" },
  { re: /^\/b\/(.+)$/, nav: "board", code: board, render: "renderBoard", data: boardUrl },
  { re: /^\/model\/(.+)$/, nav: "", code: lazy(() => import("./js/pages/model.js")), render: "renderModel", data: modelUrl },
  { re: /^\/matrix$/, nav: "matrix", code: lazy(() => import("./js/pages/matrix.js")), render: "renderMatrix", data: () => "/data/matrix.json" },
  { re: /^\/compare(?:\/.*)?$/, nav: "compare", code: lazy(() => import("./js/pages/compare.js")), render: "renderCompare", data: () => "/data/compare.json" },
  { re: /^\/calibration$/, nav: "calibration", code: calibration, render: "renderCalibration", data: () => "/data/calibration.json" },
  { re: /^\/calibration\/(\d+)$/, nav: "calibration", code: calibration, render: "renderMapping", data: (id) => `/data/calibration/${id}.json` },
  { re: /^\/multivariate$/, nav: "multivariate", code: lazy(() => import("./js/pages/multivariate.js")), render: "renderMultivariate", data: () => "/data/multivariate.json" },
  { re: /^\/harness-tax$/, nav: "harness-tax", code: lazy(() => import("./js/pages/harness-tax.js")), render: "renderHarnessTax", data: () => "/data/harness-tax.json" },
  { re: /^\/method$/, nav: "method", code: lazy(() => import("./js/pages/method.js")), render: "renderMethod" },
  { re: /^\/publications$/, nav: "publications", code: lazy(() => import("./js/pages/publications.js")), render: "renderPublications" },
  { re: /^\/api$/, nav: "api", code: lazy(() => import("./js/pages/api.js")), render: "renderApi", data: () => "/data/api.json" },
];
const pageOf = (path) => PAGES.find((page) => page.re.test(path));
const argOf = (page, path) => decodeURIComponent(path.match(page.re)[1] || "");
// a page's module and data (null for a 404); a failure to load the module is marked .code
const fetchPage = (page, arg) => Promise.all([
  page.code().catch((err) => { throw Object.assign(err, { code: true }); }),
  page.data ? load(page.data(arg)).catch((err) => { if (err.status === 404) return null; throw err; }) : null,
]);

let shownPath = null;   // the path the page was last rendered (or is being loaded) for
const scrollToHash = () => { const el = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1))); if (el) el.scrollIntoView(); return !!el; };
async function route() {
  hideTip();
  const path = location.pathname;
  if (path === shownPath) return scrollToHash();   // only the #fragment changed: same page, no re-render
  shownPath = path;
  const page = pageOf(path);
  const arg = page ? argOf(page, path) : "";
  let module = null, data = null;
  if (page) {
    // the current page stays (dimmed if it takes a moment) until the new one's code and data are in
    main.setAttribute("aria-busy", "true");
    try {
      [module, data] = await fetchPage(page, arg);
    } catch (err) {
      if (shownPath !== path) return;   // another page was opened meanwhile
      shownPath = null;
      main.removeAttribute("aria-busy");
      // a page's code from before the site was updated is gone: load the page anew
      if (err.code && drawn) return location.reload();
      return renderError(err);
    }
    if (shownPath !== path) return;
    main.removeAttribute("aria-busy");
  }
  const nav = page ? page.nav : "";
  const inPlace = page ? module[page.render](data, arg) : renderNotFound("Page not found.");
  document.querySelectorAll("[data-nav]").forEach((a) => (a.dataset.nav === nav ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  centerIn($(".nav"), '[aria-current="page"]', pillNav);
  if (!inPlace && !scrollToHash()) window.scrollTo(0, 0);
}

// the path of an in-site link to a page of the site (one this router opens), if el is in one
function sitePath(el) {
  const a = el.closest && el.closest("a[href]");
  if (!a || a.target || a.hasAttribute("download")) return null;
  const url = new URL(a.href);
  return url.origin === location.origin && !url.hash && pageOf(url.pathname) ? url.pathname : null;
}

// in-site links switch pages without a reload
document.addEventListener("click", (e) => {
  const path = sitePath(e.target);
  if (!path || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  go(path);
});

// a link's page starts loading when the pointer rests on it (or a finger touches it), so most
// of the wait is over by the click
let hovered = null;
function prefetch(path) {
  const page = path && pageOf(path);
  if (page) fetchPage(page, argOf(page, path)).catch(() => {});
}
document.addEventListener("pointerover", (e) => {
  clearTimeout(hovered);
  const path = sitePath(e.target);
  if (path && path !== location.pathname) hovered = setTimeout(prefetch, 50, path);
}, { passive: true });
document.addEventListener("touchstart", (e) => prefetch(sitePath(e.target)), { passive: true });

// links from before pages had their own paths: #/model/x -> /model/x
if (location.hash.startsWith("#/")) history.replaceState(null, "", "/" + location.hash.slice(2));
// the first page's code and data load alongside the site's (route() then finds them loading)
const first = pageOf(location.pathname);
if (first) fetchPage(first, argOf(first, location.pathname)).catch(() => {});
load("/data/site.json")
  .then((data) => {
    setSite(data);
    initChrome();
    window.addEventListener("popstate", route);
    route();
  })
  .catch(renderError);
