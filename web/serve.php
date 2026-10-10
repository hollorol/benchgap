<?php
// benchgap.net backend (Slim 4): the site's pages (index.html filled in for
// each path), the site data (data/: all of it, and each page's slice), the
// public API (api/v1/...) and llms.txt, computed from the score database.
// .htaccess sends every request that is not a static file here.
//
// The database is set in config.php (see config.example.php). Local preview:
//   php -S localhost:8000 -t web web/serve.php
declare(strict_types=1);

use Benchgap\Api;
use Benchgap\Pages;
use Benchgap\Site;
use Benchgap\Snapshot;
use Psr\Http\Message\ResponseInterface as Response;
use Psr\Http\Message\ServerRequestInterface as Request;
use Psr\Http\Server\RequestHandlerInterface as Handler;
use Slim\Exception\HttpNotFoundException;
use Slim\Factory\AppFactory;
use Slim\HttpCache\Cache;
use Slim\HttpCache\CacheProvider;
use Slim\Routing\RouteCollectorProxy;

// PHP's built-in dev server: static files as they are (like .htaccess)
if (PHP_SAPI === 'cli-server' && is_file(__DIR__ . parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH))) {
    return false;
}

require __DIR__ . '/vendor/autoload.php';

function database(): PDO
{
    static $db;
    if ($db !== null) {
        return $db;
    }
    // BENCHGAP_DSN: the local dev stack's database (compose.yaml); config.php otherwise
    $config = getenv('BENCHGAP_DSN') ? ['dsn' => getenv('BENCHGAP_DSN')] : require __DIR__ . '/config.php';
    return $db = new PDO($config['dsn'], $config['username'] ?? null, $config['password'] ?? null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
}

// The site's files kept in the temp directory, named by build(): the site data, and every page
// and document made from it. Another copy of this site may share the directory, hence __DIR__
function cachePrefix(): string
{
    return sys_get_temp_dir() . '/benchgap-' . substr(sha1(__DIR__), 0, 12) . '-';
}

function cacheFile(string $name): string
{
    return cachePrefix() . build() . "-$name";
}

// the database's current build and this code's version (the files a page is made of): a cached
// file is good while both stay the same
function build(): string
{
    static $build;
    $code = [...glob(__DIR__ . '/src/*.php'), __FILE__, __DIR__ . '/index.html', __DIR__ . '/assets/app.js', __DIR__ . '/assets/style.css'];
    return $build ??= sha1(implode(' ', array_map('filemtime', $code)) . ' ' . Snapshot::version(database()));
}

// writes a cached file under another name first, so a concurrent request never reads half of it.
// Without a writable temp directory nothing is kept, and every request builds what it needs again
function keep(string $file, string $contents): void
{
    $tmp = "$file." . getmypid();
    if (@file_put_contents($tmp, $contents) !== false && @chmod($tmp, 0600)) {
        @rename($tmp, $file);
    }
}

// the site data (Snapshot) of the database's current build. Building it takes seconds and even
// reading it back about 150 ms, so it is kept in a file, and so is every response made from it
// (cached()). One request builds it while the others wait to read it; it is the first file of a
// build, so it removes the previous builds' files
function snapshot(): array
{
    static $snapshot;
    if ($snapshot !== null) {
        return $snapshot;
    }
    $file = cacheFile('site.ser');
    $read = fn () => is_file($file) ? @unserialize((string) file_get_contents($file), ['allowed_classes' => false]) : false;
    $data = $read();
    if (!is_array($data)) {
        $lock = @fopen(cacheFile('site.lock'), 'c');
        if ($lock) {
            flock($lock, LOCK_EX);
            $data = $read();   // built by the request we waited for
        }
        if (!is_array($data)) {
            $data = Snapshot::build(database());
            keep($file, serialize($data));
            foreach (glob(cachePrefix() . '*') ?: [] as $old) {
                if (!str_contains($old, build())) {
                    @unlink($old);
                }
            }
        }
        if ($lock) {
            fclose($lock);
        }
    }
    return $snapshot = $data;
}

// Every page, data document (data/...) and API document depends only on the build and its path,
// so the first request for it keeps the response, and later ones send that instead of reading the
// site data back. Not the matrix's per-score details (one per cell)
function cached(Request $request, Handler $handler): Response
{
    $path = $request->getUri()->getPath();
    if ($request->getMethod() !== 'GET' || str_starts_with($path, '/data/score/')) {
        return $handler->handle($request);
    }
    $file = cacheFile(sha1($path) . '.response');
    $kept = is_file($file) ? @file_get_contents($file) : false;
    if ($kept !== false && str_contains($kept, "\n")) {
        [$type, $body] = explode("\n", $kept, 2);
        return respond((new Slim\Psr7\Factory\ResponseFactory())->createResponse(), $body, $type);
    }
    $response = $handler->handle($request);
    if ($response->getStatusCode() === 200) {
        keep($file, $response->getHeaderLine('Content-Type') . "\n" . $response->getBody());
    }
    return $response;
}

// the API over the site data, built once per request
function api(): Api
{
    static $api;
    return $api ??= new Api(snapshot());
}

// the front-end's documents (data/...), built once per request
function site(): Site
{
    static $site;
    return $site ??= new Site(snapshot());
}

// the site's pages over the API, built once per request
function pages(): Pages
{
    static $pages;
    return $pages ??= new Pages(api());
}

// a body with its Content-Type and an ETag; the Cache middleware answers a matching If-None-Match with 304
function respond(Response $response, string $body, string $type): Response
{
    $response->getBody()->write($body);
    return (new CacheProvider())->withEtag($response->withHeader('Content-Type', $type), sha1($body));
}

// a document (JSON unless a string of $type)
function send(Response $response, array|string $document, string $type = 'application/json'): Response
{
    $body = is_string($document) ? $document : json_encode($document, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    return respond($response, $body, "$type; charset=utf-8");
}

// throws a 404 for unknown benchmarks, models and mappings
function found(?array $document, Request $request): array
{
    return $document ?? throw new HttpNotFoundException($request);
}

// index.html filled in with one of the site's pages (see Pages), and with what to preload: the
// page's own data document (data/...), which app.js loads alongside data/site.json, and on the
// bundled site (deploy.yml) the chunks app.js imports and the page's own, $code (js/pages/$code.js)
function html(Pages $pages, array $page, Request $request, ?string $code = null, ?string $data = null): string
{
    $template = file_get_contents(__DIR__ . '/index.html');
    // the local dev stack (compose.yaml) counts no visits: BENCHGAP_ANALYTICS=off leaves the analytics script out
    if (getenv('BENCHGAP_ANALYTICS') === 'off') {
        $template = preg_replace('~<script data-host="https://app\.analyzati\.com"[^>]*></script>\n~', '', $template);
    }
    // app.js and style.css by their content, so browsers can keep them (.htaccess) and still get each new upload
    foreach (['/assets/app.js', '/assets/style.css'] as $asset) {
        $template = str_replace("\"$asset\"", "\"$asset?v=" . hash_file('crc32b', __DIR__ . $asset) . '"', $template);
    }
    $site = '<link rel="preload" href="/data/site.json" as="fetch" crossorigin>';
    $preload = [$site];
    if ($data !== null) {
        $preload[] = str_replace('/data/site.json', htmlspecialchars($data), $site);
    }
    $chunks = [...(glob(__DIR__ . '/assets/chunks/chunk-*.js') ?: []), ...($code === null ? [] : (glob(__DIR__ . "/assets/chunks/$code-*.js") ?: []))];
    foreach ($chunks as $chunk) {
        $preload[] = '<link rel="modulepreload" href="/assets/chunks/' . basename($chunk) . '">';
    }
    $template = str_replace($site, implode("\n", $preload), $template);
    return $pages->html($template, $page, $request->getUri()->getPath());
}

// the page $build makes with Pages, the module app.js renders it with (js/pages/$code.js) and the
// data document it renders it from (as app.js PAGES)
function page(Request $request, Response $response, Closure $build, string $code, ?string $data = null): Response
{
    return send($response, html(pages(), found($build(pages()), $request), $request, $code, $data), 'text/html');
}

// a data/ path, its parts URL-encoded (as boardUrl and modelUrl, assets/js/core.js)
function data(string ...$parts): string
{
    return '/data/' . implode('/', array_map('rawurlencode', $parts)) . '.json';
}

$app = AppFactory::create();
$app->add(cached(...));
// the API is readable from any origin (a kept response too)
$app->add(function (Request $request, Handler $handler): Response {
    $response = $handler->handle($request);
    return str_starts_with($request->getUri()->getPath(), '/api/') ? $response->withHeader('Access-Control-Allow-Origin', '*') : $response;
});
$app->add(new Cache('public', 0));  // Cache-Control: public, no-cache (always revalidate)
$errors = $app->addErrorMiddleware(false, true, true);
$errors->getDefaultErrorHandler()->forceContentType('application/json');
// a missing page is the site's 404 page (noindex); the API and the data answer JSON
$errors->setErrorHandler(HttpNotFoundException::class, function (Request $request, Throwable $error) use ($app, $errors): Response {
    if (preg_match('#^/(api/v1|data)(/|$)#', $request->getUri()->getPath())) {
        return $errors->getDefaultErrorHandler()($request, $error, false, false, false);
    }
    $response = $app->getResponseFactory()->createResponse(404);
    $response->getBody()->write(html(pages(), pages()->notFound(), $request));
    return $response->withHeader('Content-Type', 'text/html; charset=utf-8');
});

// the whole site data in one document; the site's pages load their own slices (Site)
$app->get('/data/benchgap.json', fn (Request $rq, Response $rs) => send($rs, snapshot()));
$app->group('/data', function (RouteCollectorProxy $data) {
    $data->get('/site.json', fn (Request $rq, Response $rs) => send($rs, site()->site()));
    $data->get('/home.json', fn (Request $rq, Response $rs) => send($rs, site()->home()));
    $data->get('/b/{name}/{version}.json', fn (Request $rq, Response $rs, array $a) =>
        send($rs, found(site()->board("{$a['name']}/{$a['version']}"), $rq)));
    $data->get('/model/{slug}.json', fn (Request $rq, Response $rs, array $a) => send($rs, found(site()->model($a['slug']), $rq)));
    $data->get('/matrix.json', fn (Request $rq, Response $rs) => send($rs, site()->matrix()));
    $data->get('/score/{model:[0-9]+}/{benchmark:[0-9]+}.json', fn (Request $rq, Response $rs, array $a) =>
        send($rs, found(site()->score((int) $a['model'], (int) $a['benchmark']), $rq)));
    $data->get('/calibration.json', fn (Request $rq, Response $rs) => send($rs, site()->calibration()));
    $data->get('/cross.json', fn (Request $rq, Response $rs) => send($rs, site()->cross()));
    $data->get('/multivariate.json', fn (Request $rq, Response $rs) => send($rs, site()->multivariate()));
    $data->get('/compare.json', fn (Request $rq, Response $rs) => send($rs, site()->compare()));
    $data->get('/harness-tax.json', fn (Request $rq, Response $rs) => send($rs, site()->harnessTax()));
    $data->get('/api.json', fn (Request $rq, Response $rs) => send($rs, site()->api()));
    $data->get('/calibration/{id:[0-9]+}.json', fn (Request $rq, Response $rs, array $a) =>
        send($rs, found(site()->mapping((int) $a['id']), $rq)));
});
$app->get('/sitemap.xml', fn (Request $rq, Response $rs) => send($rs, pages()->sitemap(), 'application/xml'));
$app->get('/llms.txt', fn (Request $rq, Response $rs) => send($rs, pages()->llms(), 'text/markdown'));
$app->get('/llms-full.txt', fn (Request $rq, Response $rs) => send($rs, pages()->llmsFull(), 'text/markdown'));

// the site's pages (keep in step with app.js PAGES)
$app->get('/', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->board($p->home()), 'board', data('home')));
$app->get('/b/{key:.+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->board($a['key']), 'board', data('b', ...explode('/', $a['key']))));
$app->get('/model/{slug:.+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->model($a['slug']), 'model', data('model', $a['slug'])));
$app->get('/matrix', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->matrix(), 'matrix', data('matrix')));
// two models side by side, or one and the frontier model closest to it - of one provider,
// or of any (the second stays dynamic)
$app->get('/compare', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->compare(), 'compare', data('compare')));
$app->get('/compare/{a:[^/]+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->compare($a['a']), 'compare', data('compare')));
$app->get('/compare/{a:[^/]+}/from/{provider:[^/]+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->compare($a['a'], null, $a['provider']), 'compare', data('compare')));
$app->get('/compare/{a:[^/]+}/{b:[^/]+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->compare($a['a'], $a['b']), 'compare', data('compare')));
$app->get('/calibration', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->calibration(), 'calibration', data('calibration')));
$app->get('/calibration/{id:[0-9]+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->mapping((int) $a['id']), 'calibration', data('calibration', $a['id'])));
$app->get('/multivariate', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->multivariate(), 'multivariate', data('multivariate')));
$app->get('/harness-tax', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->harnessTax(), 'harness-tax', data('harness-tax')));
$app->get('/method', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->methodPage(), 'method'));
$app->get('/publications', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->publications(), 'publications'));
$app->get('/api', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->apiPage(), 'api', data('api')));

$app->group('/api/v1', function (RouteCollectorProxy $v1) {
    $v1->get('[/[index.json]]', fn (Request $rq, Response $rs) => send($rs, api()->index()));
    $v1->get('/benchmarks.json', fn (Request $rq, Response $rs) => send($rs, api()->benchmarks()));
    $v1->get('/benchmarks/{name}/{version}.json', fn (Request $rq, Response $rs, array $a) =>
        send($rs, found(api()->benchmark("{$a['name']}/{$a['version']}"), $rq)));
    $v1->get('/models.json', fn (Request $rq, Response $rs) => send($rs, api()->models()));
    $v1->get('/models/{slug}.json', fn (Request $rq, Response $rs, array $a) => send($rs, found(api()->model($a['slug']), $rq)));
    $v1->get('/scores.json', fn (Request $rq, Response $rs) => send($rs, api()->scores()));
    $v1->get('/scores.csv', fn (Request $rq, Response $rs) => send($rs, api()->scoresCsv(), 'text/csv'));
    $v1->get('/mappings.json', fn (Request $rq, Response $rs) => send($rs, api()->mappings()));
    $v1->get('/mappings/{id:[0-9]+}.json', fn (Request $rq, Response $rs, array $a) =>
        send($rs, found(api()->mapping((int) $a['id']), $rq)));
    $v1->get('/harness-tax.json', fn (Request $rq, Response $rs) => send($rs, api()->harnessTax()));
    $v1->get('/harness-tax/{family_id:[a-z0-9][a-z0-9._-]*}.json', fn (Request $rq, Response $rs, array $a) =>
        send($rs, found(api()->harnessTaxFamily(rawurldecode($a['family_id'])), $rq)));
});

$app->run();
