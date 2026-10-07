<?php

declare(strict_types=1);

namespace Benchgap;

/**
 * The site's pages for readers that do not run JavaScript (search and AI
 * crawlers): each page is index.html with its own title, description and
 * canonical URL, and a plain-HTML summary in <main> that app.js replaces with
 * the interactive page. llms.txt and llms-full.txt give the same content as
 * Markdown. Everything is read from the public API documents.
 *
 * The titles, descriptions and ledes follow app.js (setMeta and the page
 * headers); keep the two in step.
 */
final class Pages
{
    public const DEFAULT_BENCH = 'terminal-bench/4.0';  // the leaderboard on the home page (app.js DEFAULT_BENCH)
    private const ABOUT = 'benchgap is an LLM benchmark leaderboard that fills in the missing scores. Most models are only '
        . 'ever run on a handful of benchmarks, so benchgap calibrates benchmarks against each other on the models '
        . 'measured on both, then estimates each missing score with its cross-validated error and a confidence level. '
        . 'Measured and estimated scores are always marked apart.';
    private const HOME_TITLE = 'LLM Benchmark Leaderboard with Estimated Scores';
    private const HOME_DESCRIPTION = "LLM benchmark scores: measured where available, estimated where missing, with every estimate's error and confidence.";
    // the pages besides those of each benchmark, model and calibration: title and summary for llms.txt
    private const PAGES = [
        '/' => ['Leaderboard', 'one benchmark at a time, measured and estimated scores ranked together'],
        '/matrix' => ['Score matrix', 'every model on every benchmark'],
        '/calibration' => ['Calibration', 'which benchmarks predict which, and how well'],
        '/method' => ['Method', 'how the missing scores are estimated, and when not to trust them'],
        '/api' => ['API', 'free JSON and CSV API, no key'],
    ];

    private array $index;
    private array $benchmarks;      // by key
    private array $models;          // by slug
    private array $capabilities;    // label by id
    private string $date;           // when the measured scores were retrieved

    public function __construct(private readonly Api $api)
    {
        $this->index = $api->index();
        $this->benchmarks = array_column($api->benchmarks()['benchmarks'], null, 'key');
        $this->models = array_column($api->models()['models'], null, 'slug');
        $this->capabilities = array_column($this->index['capabilities'], 'label', 'id');
        $this->date = $this->index['data_retrieved_at'] ?? '';
    }

    /** index.html filled in with a page (one of the page methods below) served at $path. */
    public function html(string $template, array $page, string $path): string
    {
        $doc = \Dom\HTMLDocument::createFromString($template, LIBXML_NOERROR);
        $title = $page['title'] . ' · benchgap';
        $url = Api::SITE_URL . ($page['canonical'] ?? $path);
        $doc->title = $title;
        $set = fn (string $selector, string $attribute, string $value) => $doc->querySelector($selector)?->setAttribute($attribute, $value);
        $set('meta[name="description"]', 'content', $page['description']);
        $set('meta[name="robots"]', 'content', $page['canonical'] === null ? 'noindex' : 'index, follow');
        $set('link[rel="canonical"]', 'href', $url);
        $set('meta[property="og:title"]', 'content', $title);
        $set('meta[property="og:description"]', 'content', $page['description']);
        $set('meta[property="og:url"]', 'content', $url);
        $dataset = $doc->getElementById('ld-dataset');
        $dataset->textContent = json_encode(
            json_decode($dataset->textContent, true) + ['dateModified' => $this->index['generated_at']],
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT
        );
        $doc->getElementById('data-date')->textContent = $this->date;
        $doc->getElementById('main')->innerHTML = $page['main'];
        return $doc->saveHtml();
    }

    /** llms.txt: what the site is and where everything is (https://llmstxt.org). */
    public function llms(): string
    {
        $site = Api::SITE_URL;
        $lines = [
            '# benchgap', '', '> ' . self::ABOUT, '', $this->facts(), '',
            '## Pages', '',
            ...array_map(fn ($path, $page) => "- [{$page[0]}]($site$path): {$page[1]}", array_keys(self::PAGES), self::PAGES),
            '', '## Leaderboards', '',
        ];
        foreach ($this->benchmarks as $b) {
            $lines[] = "- [{$b['label']}]({$b['page']}): {$b['n_measured']} measured and {$b['n_estimated']} estimated scores";
        }
        array_push(
            $lines, '', '## Data', '',
            "- [Every leaderboard as Markdown]($site/llms-full.txt)",
            "- [All scores as CSV]($site/api/v1/scores.csv)",
            "- [API index]($site/api/v1/): endpoints, counts and the confidence rules",
            "- [OpenAPI description]({$this->index['openapi']})",
            '', '## Optional', '',
            "- [Source code]({$this->index['source_code']}): the estimation pipeline, MIT-licensed",
        );
        return implode("\n", $lines) . "\n";
    }

    /** llms-full.txt: the method in brief and every leaderboard as a Markdown table. */
    public function llmsFull(): string
    {
        $lines = ['# benchgap: every leaderboard', '', '> ' . self::ABOUT, '', $this->facts(), '', '## Method', ''];
        foreach ($this->rules() as $item) {
            $lines[] = '- ' . strip_tags($item);
        }
        foreach ($this->benchmarks as $key => $b) {
            $scores = $this->api->benchmark($key)['scores'];
            array_push($lines, '', "## {$b['label']}", '', $this->benchmarkLead($b, $scores), '', "Page: {$b['page']}", '',
                '| # | Model | Score | Source |', '|---:|---|---:|---|');
            foreach ($scores as $i => $s) {
                $name = str_replace('|', '\|', $this->models[$s['model']]['name']);
                $lines[] = '| ' . ($i + 1) . " | $name | " . self::pct($s['score']) . '% | ' . self::source($s) . ' |';
            }
        }
        return implode("\n", $lines) . "\n";
    }

    /** sitemap.xml: every page of the site, all last changed when the data was rebuilt. */
    public function sitemap(): string
    {
        $pages = [
            ...array_map(fn ($path) => Api::SITE_URL . $path, array_keys(self::PAGES)),
            ...array_column($this->benchmarks, 'page'),
            ...array_column($this->models, 'page'),
            ...array_column($this->api->mappings()['mappings'], 'page'),
        ];
        $lastmod = substr($this->index['generated_at'] ?? '', 0, 10);
        $xml = new \XMLWriter();
        $xml->openMemory();
        $xml->startDocument('1.0', 'UTF-8');
        $xml->startElementNs(null, 'urlset', 'http://www.sitemaps.org/schemas/sitemap/0.9');
        foreach ($pages as $page) {
            $xml->startElement('url');
            $xml->writeElement('loc', $page);
            if ($lastmod) {
                $xml->writeElement('lastmod', $lastmod);
            }
            $xml->endElement();
        }
        $xml->endElement();
        return $xml->outputMemory();
    }

    // -- pages: title, description, canonical path (null: not to index) and <main>;
    //    null for an unknown benchmark, model or mapping -------------------------------

    public function board(string $key): ?array
    {
        $b = $this->benchmarks[$key] ?? null;
        if ($b === null) {
            return null;
        }
        $home = $key === self::DEFAULT_BENCH;
        $scores = $this->api->benchmark($key)['scores'];
        $rows = [];
        foreach ($scores as $i => $s) {
            $rows[] = [$i + 1, $this->link($this->models[$s['model']]), self::pct($s['score']) . '%', self::source($s)];
        }
        $table = $this->table(['#', 'Model', 'Score', 'Source'], $rows);
        $lead = '<p class="lede">' . self::esc($this->benchmarkLead($b, $scores)) . '</p>';
        $source = $b['source_url'] ? '<p>Measured scores: <a href="' . self::esc($b['source_url']) . '">' . self::esc($b['harness']) . '</a>.</p>' : '';
        $all = '<section class="section"><h2 class="h2">All leaderboards</h2>' . $this->benchmarkList() . '</section>';
        if ($home) {
            return $this->result(self::HOME_TITLE, self::HOME_DESCRIPTION, '/',
                $this->header('LLM benchmark leaderboard · gaps filled', 'LLM benchmark scores, with the gaps filled',
                    '<p class="lede">' . self::esc(self::ABOUT) . '</p><p>' . self::esc($this->facts()) . '</p>')
                . '<section class="section"><h2 class="h2">' . self::esc($b['label']) . " leaderboard</h2>$lead$source$table</section>$all");
        }
        return $this->result("{$b['label']} leaderboard",
            "{$b['label']} leaderboard: {$b['n_measured']} measured and {$b['n_estimated']} estimated LLM scores, each estimate with its error and confidence.",
            "/b/$key",
            $this->header($this->capabilities[$b['capability']] ?? $b['capability'], "{$b['label']} leaderboard", $lead . $source) . $table . $all);
    }

    public function model(string $slug): ?array
    {
        $m = $this->models[$slug] ?? null;
        if ($m === null) {
            return null;
        }
        $rows = array_map(fn ($s) => [
            $this->link($this->benchmarks[$s['benchmark']]), self::pct($s['score']) . '%', self::source($s),
        ], $this->api->model($slug)['scores']);
        $plural = $m['n_measured'] === 1 ? '' : 's';
        return $this->result("{$m['name']} benchmark scores",
            "{$m['name']} benchmark scores: measured on {$m['n_measured']} benchmark$plural"
                . ($m['n_estimated'] ? ", estimated on {$m['n_estimated']} more, with the error and confidence of each estimate." : '.'),
            '/model/' . rawurlencode($slug),
            $this->header("{$m['provider_name']} · model", "{$m['name']} benchmark scores",
                '<p class="lede">' . self::esc($this->modelLead($m)) . '</p>')
            . $this->table(['Benchmark', 'Score', 'Source'], $rows));
    }

    public function matrix(): array
    {
        $models = array_map(fn ($m) => '<li>' . $this->link($m) . " ({$m['n_measured']} measured, {$m['n_estimated']} estimated)</li>", $this->models);
        return $this->result('LLM benchmark score matrix',
            'Every model on every benchmark: measured LLM scores and calibrated estimates for the missing ones, side by side.', '/matrix',
            $this->header('Score matrix', 'Every model × every benchmark',
                '<p class="lede">Every model on every benchmark: measured LLM scores and calibrated estimates for the missing ones, side by side.</p>')
            . '<section class="section"><h2 class="h2">Models</h2><ul>' . implode('', $models) . '</ul></section>'
            . '<section class="section"><h2 class="h2">Benchmarks</h2>' . $this->benchmarkList() . '</section>');
    }

    public function calibration(): array
    {
        $g = $this->index['quality_gate'];
        $rows = array_map(fn ($m) => [
            '<a href="' . self::path($m['page']) . '">' . self::esc($this->benchmarks[$m['from']]['label'] . ' → ' . $this->benchmarks[$m['to']]['label']) . '</a>',
            self::esc($m['method_name']), $m['n_models'], number_format($m['loo_rmse_pp'], 1) . ' pp', $m['n_estimates'],
        ], $this->api->mappings()['mappings']);
        return $this->result('LLM benchmark calibrations',
            'Which LLM benchmarks predict which: the fitted cross-benchmark calibrations behind every estimate, with their errors.', '/calibration',
            $this->header('Calibration', 'Which benchmarks predict which',
                "<p class=\"lede\">For each ordered pair of same-capability benchmarks with at least {$g['min_pairs']} shared models, several monotone curves "
                . "are fitted and the one with the lowest leave-one-out error is kept, if it passes the quality gate (R² ≥ {$g['min_r2']}, error ≤ {$g['max_loo_pp']} pp).</p>")
            . $this->table(['Calibration', 'Curve', 'Models', 'Error', 'Estimates'], $rows));
    }

    public function mapping(int $id): ?array
    {
        $doc = $this->api->mapping($id);
        if ($doc === null) {
            return null;
        }
        $m = $doc['mapping'];
        $f = $this->benchmarks[$m['from']]['label'];
        $t = $this->benchmarks[$m['to']]['label'];
        $lead = "$t is estimated from $f with a {$m['method_name']} curve fitted on {$m['n_models']} models measured on both: "
            . "{$m['equation']}, R² = " . number_format($m['r2'], 2) . ', cross-validated error ' . number_format($m['loo_rmse_pp'], 1)
            . " pp. It is used for {$m['n_estimates']} estimate" . ($m['n_estimates'] === 1 ? '' : 's') . '.';
        $rows = array_map(fn ($s) => [
            $this->link($this->models[$s['model']]), self::pct($s['estimate']['inputs'][0]['score']) . '%', self::pct($s['score']) . '%', self::source($s),
        ], $doc['estimates']);
        return $this->result("$f → $t calibration",
            "How $f scores predict $t: the fitted {$m['method_name']} curve, the models it was fitted on and its cross-validated error.",
            "/calibration/$id",
            $this->header('Calibration', "$f → $t", '<p class="lede">' . self::esc($lead) . '</p>')
            . ($rows ? $this->table(['Estimated model', $f, $t, 'Source'], $rows) : ''));
    }

    public function methodPage(): array
    {
        return $this->result('How missing benchmark scores are estimated',
            'How benchgap estimates missing LLM benchmark scores: calibration curves, leave-one-out validation and confidence levels.', '/method',
            $this->header('Method', 'How the gaps are filled, and when not to trust it', '<p class="lede">' . self::esc(self::ABOUT) . '</p>')
            . '<ul>' . implode('', array_map(fn ($item) => "<li>$item</li>", $this->rules())) . '</ul>');
    }

    public function apiPage(): array
    {
        $base = Api::SITE_URL . '/api/v1/';
        $endpoints = array_map(
            fn ($e) => str_contains($e, '{') ? '<li><code>' . self::esc($e) . '</code></li>' : '<li><a href="/api/v1/' . self::esc($e) . '">' . self::esc($e) . '</a></li>',
            $this->index['endpoints']
        );
        return $this->result('Public API',
            'Free JSON and CSV API for LLM benchmark scores, measured and estimated, with an OpenAPI 3.1 description.', '/api',
            $this->header('API · v1', 'Public API',
                '<p class="lede">Everything on this site is available as plain JSON (and CSV): every benchmark, model, score and calibration, '
                . "with each estimate's confidence level and error. Free, no key, readable from any origin.</p><p>Base URL: <code>$base</code></p>")
            . '<ul>' . implode('', $endpoints) . '</ul>');
    }

    /** the 404 page (as app.js's renderNotFound) */
    public function notFound(): array
    {
        return $this->result('Not found', '', null, <<<'HTML'
            <section class="nf">
            <svg class="nf-mark" viewBox="0 0 120 64" aria-hidden="true"><defs><pattern id="nf-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="5"/></pattern></defs><rect x="4" y="20" width="26" height="44" rx="2"/><rect class="nf-gap" x="47" y="4" width="26" height="60" rx="2" fill="url(#nf-hatch)"/><rect x="90" y="30" width="26" height="34" rx="2"/></svg>
            <div class="eyebrow">Not found · 404</div>
            <h1 class="display">This one is a gap we <em>can’t</em> fill.</h1>
            <p class="lede">Page not found. It may have been renamed, or the link has a typo.</p>
            <a class="btn nf-home" href="/">Back to the leaderboard</a>
            <nav class="nf-links" aria-label="Elsewhere on benchgap"><div class="ctl-label">Or try</div><a href="/matrix">Every model × every benchmark</a><a href="/calibration">Which benchmarks predict which</a><a href="/api">Every score in the public API</a></nav>
            </section>
            HTML);
    }

    // -- text ----------------------------------------------------------------------

    /** the numbers behind the site, as one sentence */
    private function facts(): string
    {
        $c = $this->index['counts'];
        $t = $c['confidence'];
        return "As of {$this->date}: {$c['models']} models, {$c['benchmarks']} benchmark versions, {$c['measured']} measured and "
            . "{$c['estimated']} estimated scores ({$t['high']} high, {$t['medium']} medium and {$t['low']} low confidence). "
            . 'Measured scores come from public leaderboards (' . implode(', ', $this->index['harnesses']) . ').';
    }

    /** the leaderboard's lede (app.js benchLead), from its scores (highest first) */
    private function benchmarkLead(array $b, array $scores): string
    {
        $top = array_values(array_filter($scores, fn ($s) => $s['source'] === 'measured'))[0] ?? null;
        $lead = $top ? "As of {$this->date}, the highest measured score on {$b['label']} is " . self::pct($top['score']) . '% by '
            . $this->models[$top['model']]['name'] . '.' : '';
        $n = $b['n_estimated'];
        if ($n) {
            $lead .= ' ' . ($n === 1 ? '1 more model has an estimated score' : "$n more models have estimated scores")
                . ', calibrated from the benchmarks they were measured on.';
        }
        return trim($lead);
    }

    /** the model page's lede (app.js modelLead) */
    private function modelLead(array $m): string
    {
        return "As of {$this->date}, {$m['name']} ({$m['provider_name']}) has measured scores on {$m['n_measured']} benchmark"
            . ($m['n_measured'] === 1 ? '' : 's') . ($m['n_estimated'] ? " and estimated scores on {$m['n_estimated']} more" : '') . '.';
    }

    /** the method in brief (HTML list items) */
    private function rules(): array
    {
        $g = $this->index['quality_gate'];
        $r = $this->index['confidence_levels'];
        return [
            'Measured scores come from public evaluation leaderboards and are never altered; each benchmark is calibrated only against benchmarks of the same capability.',
            "For every ordered pair of benchmarks with at least {$g['min_pairs']} models measured on both, {$g['n_candidates']} monotone curves are fitted "
                . 'and the one with the lowest leave-one-out cross-validated error is kept. That error, in percentage points, is the ± shown with every estimate.',
            "A pair keeps no calibration unless its best curve reaches R² ≥ {$g['min_r2']} and an error of at most {$g['max_loo_pp']} pp; such gaps stay empty.",
            'A missing score is estimated from the best calibration out of a benchmark the model was measured on. Estimates are never used to make further estimates.',
            "Confidence: <b>high</b> for an error up to {$r['high_max_pp']} pp, <b>medium</b> up to {$r['medium_max_pp']} pp, <b>low</b> above; "
                . "extrapolation, a fit on fewer than {$r['min_reliable_n']} models or R² below {$r['min_informative_r2']} each lower it by one level.",
            'Estimates are predictions, not measurements, and hold for the source leaderboard\'s evaluation setup only.',
        ];
    }

    // -- html ------------------------------------------------------------------------

    private function result(string $title, string $description, ?string $canonical, string $body): array
    {
        return ['title' => $title, 'description' => $description, 'canonical' => $canonical, 'main' => "<div class=\"page\">$body</div>"];
    }

    private function header(string $eyebrow, string $h1, string $html): string
    {
        return '<header class="page-head"><div class="eyebrow">' . self::esc($eyebrow) . '</div><h1 class="h2">' . self::esc($h1) . "</h1>$html</header>";
    }

    /** a table of cells already in HTML */
    private function table(array $head, array $rows): string
    {
        $tr = fn ($cells, $tag) => '<tr>' . implode('', array_map(fn ($c) => "<$tag>$c</$tag>", $cells)) . '</tr>';
        return '<div class="list-wrap"><table class="list"><thead>' . $tr(array_map(self::esc(...), $head), 'th') . '</thead><tbody>'
            . implode('', array_map(fn ($r) => $tr($r, 'td'), $rows)) . '</tbody></table></div>';
    }

    private function benchmarkList(): string
    {
        $groups = [];
        foreach ($this->benchmarks as $b) {
            $groups[$b['capability']][] = '<li>' . $this->link($b) . '</li>';
        }
        return implode('', array_map(
            fn ($cap, $items) => '<h3 class="h3">' . self::esc($this->capabilities[$cap] ?? $cap) . '</h3><ul>' . implode('', $items) . '</ul>',
            array_keys($groups), $groups
        ));
    }

    /** a link to a benchmark's or a model's page */
    private function link(array $object): string
    {
        return '<a href="' . self::esc(self::path($object['page'])) . '">' . self::esc($object['label'] ?? $object['name']) . '</a>';
    }

    /** where a score comes from: measured, or an estimate with its error and confidence */
    private static function source(array $s): string
    {
        $e = $s['estimate'];
        return $e === null ? 'measured' : 'estimated ± ' . number_format($e['error_pp'], 1) . " pp, {$e['confidence']} confidence";
    }

    private static function pct(float $v): string
    {
        return number_format($v * 100, 1);
    }

    private static function path(string $url): string
    {
        return parse_url($url, PHP_URL_PATH);
    }

    private static function esc(string|int|float $s): string
    {
        return htmlspecialchars((string) $s, ENT_QUOTES);
    }
}
