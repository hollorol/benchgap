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
    // BENCHGAP_DSN: the local dev stack's database (compose.yaml); config.php otherwise
    $config = getenv('BENCHGAP_DSN') ? ['dsn' => getenv('BENCHGAP_DSN')] : require __DIR__ . '/config.php';
    return new PDO($config['dsn'], $config['username'] ?? null, $config['password'] ?? null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
}

// the site data (Snapshot) of the database's current build. Building it takes about half a
// second, so it is kept in a file (in the temp directory) until the data or this code changes;
// without a writable temp directory every request builds it again
function snapshot(): array
{
    static $snapshot;
    if ($snapshot !== null) {
        return $snapshot;
    }
    $db = database();
    $code = implode(' ', array_map('filemtime', glob(__DIR__ . '/src/*.php')));
    // this site's files (another copy of it may share the temp directory), one per build and code version
    $prefix = sys_get_temp_dir() . '/benchgap-' . substr(sha1(__DIR__), 0, 12);
    $file = "$prefix-" . sha1($code . ' ' . Snapshot::version($db)) . '.ser';
    $data = is_file($file) ? @unserialize((string) file_get_contents($file), ['allowed_classes' => false]) : false;
    if (!is_array($data)) {
        $data = Snapshot::build($db);
        // written under another name first, so a concurrent request never reads half a file
        $tmp = "$file." . getmypid();
        if (@file_put_contents($tmp, serialize($data)) !== false && @chmod($tmp, 0600) && @rename($tmp, $file)) {
            foreach (glob("$prefix-*.ser") ?: [] as $old) {
                if ($old !== $file) {
                    @unlink($old);
                }
            }
        }
    }
    return $snapshot = $data;
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

// a document with an ETag; the Cache middleware answers a matching If-None-Match with 304
function send(Response $response, array|string $document, string $type = 'application/json'): Response
{
    $body = is_string($document) ? $document : json_encode($document, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    $response->getBody()->write($body);
    return (new CacheProvider())->withEtag($response->withHeader('Content-Type', "$type; charset=utf-8"), sha1($body));
}

// throws a 404 for unknown benchmarks, models and mappings
function found(?array $document, Request $request): array
{
    return $document ?? throw new HttpNotFoundException($request);
}

// index.html filled in with one of the site's pages (see Pages), and with the page's own
// data document (data/...) to preload: app.js loads it alongside data/site.json
function html(Pages $pages, array $page, Request $request, ?string $data = null): string
{
    $template = file_get_contents(__DIR__ . '/index.html');
    // app.js and style.css by their content, so browsers can keep them (.htaccess) and still get each new upload
    foreach (['/assets/app.js', '/assets/style.css'] as $asset) {
        $template = str_replace("\"$asset\"", "\"$asset?v=" . hash_file('crc32b', __DIR__ . $asset) . '"', $template);
    }
    if ($data !== null) {
        $site = '<link rel="preload" href="/data/site.json" as="fetch" crossorigin>';
        $template = str_replace($site, "$site\n" . str_replace('/data/site.json', htmlspecialchars($data), $site), $template);
    }
    return $pages->html($template, $page, $request->getUri()->getPath());
}

// the page $build makes with Pages, and the data document app.js renders it from (as app.js PAGES)
function page(Request $request, Response $response, Closure $build, ?string $data = null): Response
{
    $pages = new Pages(api());
    return send($response, html($pages, found($build($pages), $request), $request, $data), 'text/html');
}

// a data/ path, its parts URL-encoded (as app.js boardUrl and modelUrl)
function data(string ...$parts): string
{
    return '/data/' . implode('/', array_map('rawurlencode', $parts)) . '.json';
}

$app = AppFactory::create();
$app->add(new Cache('public', 0));  // Cache-Control: public, no-cache (always revalidate)
$errors = $app->addErrorMiddleware(false, true, true);
$errors->getDefaultErrorHandler()->forceContentType('application/json');
// a missing page is the site's 404 page (noindex); the API and the data answer JSON
$errors->setErrorHandler(HttpNotFoundException::class, function (Request $request, Throwable $error) use ($app, $errors): Response {
    if (preg_match('#^/(api/v1|data)(/|$)#', $request->getUri()->getPath())) {
        return $errors->getDefaultErrorHandler()($request, $error, false, false, false);
    }
    $pages = new Pages(api());
    $response = $app->getResponseFactory()->createResponse(404);
    $response->getBody()->write(html($pages, $pages->notFound(), $request));
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
    $data->get('/harness-tax.json', fn (Request $rq, Response $rs) => send($rs, site()->harnessTax()));
    $data->get('/calibration/{id:[0-9]+}.json', fn (Request $rq, Response $rs, array $a) =>
        send($rs, found(site()->mapping((int) $a['id']), $rq)));
});
$app->get('/sitemap.xml', fn (Request $rq, Response $rs) => send($rs, (new Pages(api()))->sitemap(), 'application/xml'));
$app->get('/llms.txt', fn (Request $rq, Response $rs) => send($rs, (new Pages(api()))->llms(), 'text/markdown'));
$app->get('/llms-full.txt', fn (Request $rq, Response $rs) => send($rs, (new Pages(api()))->llmsFull(), 'text/markdown'));

// the site's pages (keep in step with app.js PAGES)
$app->get('/', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->board($p->home()), data('home')));
$app->get('/b/{key:.+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->board($a['key']), data('b', ...explode('/', $a['key']))));
$app->get('/model/{slug:.+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->model($a['slug']), data('model', $a['slug'])));
$app->get('/matrix', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->matrix(), data('matrix')));
$app->get('/calibration', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->calibration(), data('calibration')));
$app->get('/calibration/{id:[0-9]+}', fn (Request $rq, Response $rs, array $a) =>
    page($rq, $rs, fn (Pages $p) => $p->mapping((int) $a['id']), data('calibration', $a['id'])));
$app->get('/multivariate', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->multivariate(), data('multivariate')));
$app->get('/harness-tax', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->harnessTax(), data('harness-tax')));
$app->get('/method', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->methodPage()));
$app->get('/publications', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->publications()));
$app->get('/api', fn (Request $rq, Response $rs) => page($rq, $rs, fn (Pages $p) => $p->apiPage(), data('calibration')));

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
})->add(fn (Request $request, Handler $handler) => $handler->handle($request)->withHeader('Access-Control-Allow-Origin', '*'));

$app->run();
