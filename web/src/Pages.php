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
 * The titles, descriptions and ledes follow the front-end's pages (assets/js/pages/:
 * setMeta and the page headers); keep the two in step.
 */
final class Pages
{
    private const ABOUT = 'benchgap is an LLM benchmark leaderboard that fills in the missing scores. Most models are only '
        . 'ever run on a handful of benchmarks, so benchgap calibrates benchmarks against each other on the models '
        . 'measured on both, then estimates each missing score with its cross-validated error and a confidence level. '
        . 'Measured and estimated scores are always marked apart.';
    private const HOME_TITLE = 'LLM Benchmark Leaderboard with Estimated Scores';
    private const HOME_DESCRIPTION = "LLM benchmark scores: measured where available, estimated where missing, with every estimate's error and confidence.";
    // what general performance means (the compare page; the same definition as Compare.php)
    private const COMPARE_LEDE = 'General performance is the mean percentile of a model\'s measured scores across the '
        . 'listed benchmarks; the frontier is its strongest tenth. Measured scores only - never estimates.';
    // the compare page's lede (js/pages/compare.js LEDE); the definition above rides along on the pair pages
    private const COMPARE_INTRO = 'Measured scores decide who leads; estimates fill in the rest, hatched, with their confidence. '
        . 'Pick two models, or one and the frontier model to hold it against: its closest rival in general performance, '
        . 'or for a model below the frontier the closest of the frontier models it shares the most benchmarks with.';
    // the pages besides those of each benchmark, model and calibration: title and summary for llms.txt
    private const PAGES = [
        '/' => ['Leaderboard', 'one benchmark at a time, measured and estimated scores ranked together'],
        '/matrix' => ['Score matrix', 'every model on every benchmark'],
        '/compare' => ['Compare', 'two models side by side on every benchmark, or one against a frontier model picked for it'],
        '/calibration' => ['Calibration', 'which benchmarks predict which, and how well'],
        '/multivariate' => ['Multivariate', 'each benchmark predicted from several others together, with every model\'s cross-validated prediction'],
        '/harness-tax' => ['Harness tax', 'how much harnesses disagree about the same models on the same benchmark, measured scores only'],
        '/method' => ['Method', 'how the missing scores are estimated, and when not to trust them'],
        '/publications' => ['Publications', 'the papers on predicting benchmark scores without running every eval, and how benchgap relates to them'],
        '/api' => ['API', 'free JSON and CSV API, no key'],
    ];

    private array $index;
    private array $benchmarks;      // by key
    private array $models;          // by slug
    private array $listed;          // the listed benchmarks (Snapshot::LISTED) by key: the ones the site shows
    private array $listedModels;    // the models with a score on a listed benchmark, by slug
    private string $home;           // the home page's benchmark key (Snapshot::home)
    private array $capabilities;    // label by id
    private string $date;           // when the measured scores were retrieved

    public function __construct(private readonly Api $api)
    {
        $this->index = $api->index();
        $this->benchmarks = array_column($api->benchmarks()['benchmarks'], null, 'key');
        $this->listed = array_filter($this->benchmarks, fn ($b) => $b['listed']);
        $this->home = $api->home();
        $this->models = array_column($api->models()['models'], null, 'slug');
        $this->listedModels = array_filter($this->models, fn ($m) => $m['listed']);
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
        foreach ($this->listed as $b) {
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
        foreach ($this->listed as $key => $b) {
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
            ...array_column($this->listed, 'page'),
            ...array_column($this->listedModels, 'page'),
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

    public function home(): string
    {
        return $this->home;
    }

    // -- pages: title, description, canonical path (null: not to index) and <main>;
    //    null for an unknown benchmark, model or mapping -------------------------------

    public function board(string $key): ?array
    {
        $b = $this->benchmarks[$key] ?? null;
        if ($b === null) {
            return null;
        }
        $home = $key === $this->home;
        $scores = $this->api->benchmark($key)['scores'];
        $rows = [];
        foreach ($scores as $i => $s) {
            $rows[] = [$i + 1, $this->link($this->models[$s['model']]), self::pct($s['score']) . '%', self::source($s)];
        }
        $table = $this->table(['#', 'Model', 'Score', 'Source'], $rows);
        $lead = '<p class="lede">' . self::esc($this->benchmarkLead($b, $scores)) . '</p>';
        $source = $b['source_url'] ? '<p>Measured scores: <a href="' . self::esc($b['source_url']) . '">' . self::esc(self::host($b['source_url'])) . '</a>.</p>' : '';
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
        ], $this->listedScores($slug));
        $plural = $m['n_measured'] === 1 ? '' : 's';
        return $this->result("{$m['name']} benchmark scores",
            "{$m['name']} benchmark scores: measured on {$m['n_measured']} benchmark$plural"
                . ($m['n_estimated'] ? ", estimated on {$m['n_estimated']} more, with the error and confidence of each estimate." : '.'),
            '/model/' . rawurlencode($slug),
            $this->header("{$m['provider_name']} · model", "{$m['name']} benchmark scores",
                '<p class="lede">' . self::esc($this->modelLead($m)) . '</p>')
            . $this->table(['Benchmark', 'Score', 'Source'], $rows));
    }

    /**
     * The compare page's pair summary (app.js renders the interactive page): the two
     * models' scores on the benchmarks both have one, and their standings. One model
     * (or none) picks the frontier model to hold it against (Compare::pick) - of one
     * provider, or of any; a named pair stays a pair.
     */
    public function compare(?string $a = null, ?string $b = null, ?string $from = null): ?array
    {
        if (($a !== null && !isset($this->models[$a])) || ($b !== null && !isset($this->models[$b]))) {
            return null;
        }
        $doc = $this->api->compare();
        $general = array_column($doc['general'], null, 'slug');   // strongest first
        $frontier = array_column($doc['frontier'], null, 'slug');
        $rank = array_flip(array_keys($general));
        $nStanding = count($general);
        $standing = fn (string $slug): string => isset($general[$slug])
            ? self::pct($general[$slug]['general']) . ' on ' . $general[$slug]['n'] . ' measured (rank #'
                . ($rank[$slug] + 1) . ' of ' . $nStanding . ')'
            : '— (no measured scores to stand on)';
        if ($a === null) {
            $rows = array_map(fn ($f) => [
                $this->link($this->models[$f['slug']]),
                self::pct($f['general']),
                $f['n'],
            ], $doc['frontier']);
            return $this->result('Compare two LLM models',
                'Compare two LLM models benchmark by benchmark: measured scores and estimates side by side, or one against a frontier model picked for it.',
                '/compare',
                $this->header('Model compare', 'Two models, head to head', '<p class="lede">' . self::esc(self::COMPARE_INTRO) . '</p>')
                . $this->table(['Frontier model', 'General performance', 'Measured on'], $rows)
                . '<p class="muted">Pick two models on the <a href="/compare">interactive page</a>, or one and '
                    . 'a frontier model is picked for it - of one provider, or of any.</p>');
        }
        $ma = $this->models[$a];
        $auto = $b === null;
        if ($from === 'any') {
            $from = null;
        }
        if ($auto && $from !== null && !in_array($from, array_column($this->models, 'provider'), true)) {
            return null;
        }
        if ($auto) {
            $b = Compare::pick($doc['frontier'], $a, $general[$a] ?? null, $from)['slug'] ?? null;
        }
        if ($b === null || $b === $a) {
            return null;
        }
        $mb = $this->models[$b];
        $sa = array_column($this->listedScores($a), null, 'benchmark');
        $sb = array_column($this->listedScores($b), null, 'benchmark');
        $shared = array_intersect_key($sa, $sb);
        $wins = [0, 0, 0];   // a ahead, b ahead, even (both measured)
        foreach ($shared as $k => $s) {
            if ($s['source'] === 'measured' && $sb[$k]['source'] === 'measured') {
                $d = $s['score'] <=> $sb[$k]['score'];
                $wins[$d === 0 ? 2 : ($d > 0 ? 0 : 1)]++;
            }
        }
        $lead = sprintf(
            '%s stands at %s, %s at %s%s. Of the %d benchmark%s both have a score on, %s leads on %d and %s on %d where both are measured.',
            $ma['name'], $standing($a), $mb['name'], $standing($b),
            isset($frontier[$b]) ? ' - a frontier model' : '',
            count($shared), count($shared) === 1 ? '' : 's',
            $ma['name'], $wins[0], $mb['name'], $wins[1]
        );
        $rows = array_map(fn ($k) => [
            $this->link($this->benchmarks[$k]),
            self::pct($sa[$k]['score']) . '%',
            self::pct($sb[$k]['score']) . '%',
            sprintf('%+.1f', ($sa[$k]['score'] - $sb[$k]['score']) * 100),
        ], array_keys($shared));
        $pick = '';
        if ($auto) {
            $of = $from !== null && $mb['provider'] === $from ? 'of ' . self::esc($mb['provider_name']) . ' ' : '';
            $nameA = '<b>' . self::esc($ma['name']) . '</b>';
            $shared = isset($general[$a]) ? count(array_intersect($general[$a]['benchmarks'], $general[$b]['benchmarks'])) : 0;
            $pick = ' <b>' . self::esc($mb['name']) . '</b> is ' . match (true) {
                !isset($general[$a]) => "the strongest frontier model $of($nameA has no measured standing)",
                isset($frontier[$a]) => "the frontier model {$of}closest to $nameA in general performance",
                $shared > 0 => "the frontier model {$of}closest to $nameA in general performance of those measured on the most benchmarks with it ($shared shared)",
                default => "the frontier model {$of}closest to $nameA in general performance",
            };
            if (isset($general[$a], $general[$b])) {
                $gap = ($general[$b]['general'] - $general[$a]['general']) * 100;
                $pick .= $gap === 0.0 ? ' (even with it)' : sprintf(' (%.1f points %s)', abs($gap), $gap > 0 ? 'ahead of it' : 'behind it');
            }
            if (isset($general[$a]) && !isset($frontier[$a]) && $shared === 0) {
                $pick .= ' - no frontier model shares a measured benchmark with it';
            }
            $pick .= '; the interactive page follows it as the field moves.';
        }
        return $this->result("{$ma['name']} vs {$mb['name']} benchmark scores",
            "{$ma['name']} vs {$mb['name']} on the benchmarks both have a score on: measured scores and estimates side by side.",
            null,
            $this->header('Model compare', "{$ma['name']} vs {$mb['name']}",
                '<p class="lede">' . self::esc($lead) . '</p>')
            . $this->table(['Benchmark', $ma['name'], $mb['name'], 'Δ (pp)'], $rows)
            . '<p class="muted">' . self::esc(self::COMPARE_LEDE) . $pick
                . ' Every estimate carries its error and confidence on the model pages.</p>');
    }

    public function matrix(): array
    {
        $models = array_map(fn ($m) => '<li>' . $this->link($m) . " ({$m['n_measured']} measured, {$m['n_estimated']} estimated)</li>",
            $this->listedModels);
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

    public function multivariate(): array
    {
        // of listed benchmarks from listed ones
        $fits = array_filter($this->api->multivariate(), fn ($m) => isset($this->listed[$m['target']]) && !array_diff_key(array_flip($m['features']), $this->listed));
        $rows = array_map(fn ($m) => [
            $this->link($this->benchmarks[$m['target']]) . ' ~ ' . implode(' + ', array_map(fn ($f) => $this->link($this->benchmarks[$f]), $m['features'])),
            self::esc($m['method_name']), $m['n_models'], number_format($m['loo_rmse_pp'], 1) . ' pp',
            $m['alone_loo_rmse_pp'] === null ? '—' : number_format($m['alone_loo_rmse_pp'], 1) . ' pp',
        ], $fits);
        return $this->result('Multivariate LLM benchmark predictions',
            'Each LLM benchmark predicted from several others together, of any capability: the fitted model, its cross-validated error and every prediction.', '/multivariate',
            $this->header('Multivariate', 'Each benchmark from several others',
                '<p class="lede">For each benchmark, candidates of any capability feed two searches combined - '
                . 'one elastic net fit whose lasso part zeroes the useless ones, and greedy forward selection trying '
                . 'every candidate - always ending with at least two; on what they find, the linear fit and a '
                . 'multivariate Michaelis–Menten curve compete by cross-validated error. '
                . 'Shown for analysis: the estimates come from the calibrations.</p>')
            . $this->table(['Model', 'Fit', 'Models', 'Error', 'Best one alone'], $rows));
    }

    public function harnessTax(): array
    {
        $doc = $this->api->harnessTax();
        $tiers = $doc['aggregates']['by_tier'] ?? [];
        $headline = $doc['aggregates']['headline'] ?? [];
        $ratio = $headline['ratio'] ?? null;
        $lede = 'The same benchmark, run under different harnesses or protocols, disagrees about the same models - '
            . 'on the agentic benchmarks by far more than on the tool-free ones. Measured scores only: no estimate enters this page.';
        $tiersText = implode(' · ', array_map(
            fn ($tier, $t) => "{$tier}: " . ($t['pooled_mean_abs_pp'] === null ? 'n/a' : number_format($t['pooled_mean_abs_pp'], 1) . ' pp'),
            array_keys($tiers), $tiers
        ));
        $rows = array_map(fn ($p) => [
            self::esc(self::pairName($p['a']['key'])) . ' (' . self::esc($p['a']['harness'] ?? '?') . ') vs '
                . self::esc(self::pairName($p['b']['key'])) . ' (' . self::esc($p['b']['harness'] ?? '?') . ')'
                . '<br><span class="muted">' . self::esc(implode(' · ', array_filter([
                    $p['tier'] ?? 'untiered', $p['pair_type'], $p['same_item_set'],
                    $p['status'] === 'candidate' ? 'candidate family' : null,
                    $p['low_overlap'] ? 'low overlap' : null,
                    $p['family_id'],
                ]))) . '</span>',
            $p['n_models'],
            $p['mean_abs_pp'] === null ? '—' : number_format($p['mean_abs_pp'], 1) . ' pp',
            $p['kendall_tau'] === null ? '—' : number_format($p['kendall_tau'], 2),
            ($p['n_models'] === 0 || ($p['n_positive'] + $p['n_negative']) === 0)
                ? '—'
                : "{$p['n_positive']} up / {$p['n_negative']} down",
        ], array_filter($doc['pairs'], fn ($p) => $p['n_models'] > 0));   // the biggest disagreement first (Snapshot); as app.js, none with no model on both
        $outliers = count($doc['audit']['outliers']);
        return $this->result('The harness tax: how much harnesses disagree',
            'How much the same benchmark\'s measured scores disagree across harnesses and run protocols, benchmark family by family. Measured scores only.',
            '/harness-tax',
            $this->header('Harness tax', 'The same benchmark, measured differently',
                '<p class="lede">' . self::esc($lede) . '</p>'
                . "<p>Pooled mean |delta| by tier - $tiersText; verified families only, the agentic vs tool-free ratio is "
                . ($ratio === null ? 'n/a' : number_format($ratio, 1) . 'x') . '.</p>')
            . $this->table(['Pair', 'Models', 'Mean |Δ|', 'Kendall τ', 'Direction'], $rows)
            . '<p class="muted">Measured scores only, never estimates; every per-model delta keeps both scores\' retrieval '
                . 'dates and source URLs in the <a href="/api/v1/harness-tax.json">API</a>. Families whose item sets are not '
                . 'verified are flagged and stay out of the headline aggregates; low-overlap pairs are greyed on the '
                . '<a href="/harness-tax">interactive page</a>; ' . $outliers . ' per-model delta' . ($outliers === 1 ? '' : 's')
                . ' beyond 20 pp sit in the audit queue.</p>');
    }

    /** a pair side's key without the trailing /current (the site shows the bare name) */
    private static function pairName(string $key): string
    {
        [$name, $version] = array_pad(explode('/', $key, 2), 2, null);
        return $version === null || $version === 'current' ? $name : $key;
    }

    public function methodPage(): array
    {
        return $this->result('How missing benchmark scores are estimated',
            'How benchgap estimates missing LLM benchmark scores: calibration curves, multivariate mappings, leave-one-out validation and confidence levels.', '/method',
            $this->header('Method', 'How the gaps are filled, and when not to trust it', '<p class="lede">' . self::esc(self::ABOUT) . '</p>')
            . '<ul>' . implode('', array_map(fn ($item) => "<li>$item</li>", $this->rules())) . '</ul>');
    }

    public function publications(): array
    {        $rows = array_map(fn ($p) => [
            '<a href="https://arxiv.org/abs/' . self::esc($p['id']) . '" rel="noopener" target="_blank">' . self::esc($p['title']) . '</a>'
                . '<br><span class="muted">' . self::esc("{$p['authors']}, {$p['year']}") . '</span>',
            $p['from'] === null ? '—' : self::esc($p['from']),
            self::esc($p['approach']),
            self::esc($p['relation']),
        ], self::PAPERS);
        $lede = 'benchgap is one entry in an active research line: predicting a model\'s benchmark scores without running '
            . 'every evaluation. These are the papers closest to what this site does, and how they relate to it.';
        return $this->result('Publications: the research behind benchgap',
            'Papers on predicting LLM benchmark scores from other benchmarks - matrix completion, scaling laws, latent factors - and how benchgap relates to each.',
            '/publications',
            $this->header('Publications', 'The research behind the gapfilling', '<p class="lede">' . self::esc($lede) . '</p>')
            . $this->table(['Paper', 'Predicts from', 'Approach', 'How benchgap relates'], $rows)
            . '<section class="section"><h2 class="h2">The multivariate predictions</h2><article class="prose">'
            . implode('', array_map(fn ($p) => "<p>$p</p>", self::MULTIVARIATE_WORK))
            . '</article></section>'
            . '<section class="section"><h2 class="h2">Where benchgap differs</h2><ul>'
            . implode('', array_map(fn ($item) => "<li>$item</li>", self::RELATED_WORK))
            . '</ul></section>');
    }

    /** the papers closest to what benchgap does (publications): what a score is predicted from and how they relate */
    private const PAPERS = [
        ['title' => 'You Don\'t Need to Run Every Eval', 'authors' => 'Zeng & Papailiopoulos', 'year' => 2026, 'id' => '2606.24020',
            'from' => 'a model\'s other benchmark scores',
            'approach' => 'the model × benchmark score matrix is nearly rank-2; matrix completion in logit space (BenchPress)',
            'relation' => 'the closest relative: the same gapfilling problem with one global factor model. benchgap keeps local, explicit calibrations per benchmark pair, each with its own cross-validated error, and refuses to transfer across capabilities.'],
        ['title' => 'Sloth: scaling laws for LLM skills to predict multi-benchmark performance across families', 'authors' => 'Polo et al.', 'year' => 2024, 'id' => '2412.06540',
            'from' => 'training compute and latent skills',
            'approach' => 'scaling laws over low-dimensional skill factors, within and across model families',
            'relation' => 'predicts hypothetical models and needs training metadata. benchgap maps an existing model from its measured scores alone, which is all closed API models publish.'],
        ['title' => 'Observational Scaling Laws and the Predictability of Language Model Performance', 'authors' => 'Ruan et al.', 'year' => 2024, 'id' => '2405.10938',
            'from' => 'simple benchmarks and compute',
            'approach' => 'a latent capability variable regressed onto downstream benchmarks',
            'relation' => 'the same score-from-scores idea, anchored to compute. benchgap is compute-agnostic, so it also works for models whose training details are unknown.'],
        ['title' => 'From Benchmarks to Skills: Low-Rank Factors for LLM Evaluation', 'authors' => 'Maimon et al.', 'year' => 2025, 'id' => '2507.20208',
            'from' => 'a subset of a model\'s scores',
            'approach' => 'psychometric low-rank factorization; profiling a model from a few tasks',
            'relation' => 'closest in the fill-the-profile goal, but in latent space. benchgap stays in observable benchmark space and shows the fitted curve for every pair.'],
        ['title' => 'Efficient Benchmarking Is Just Feature Selection and Multiple Regression', 'authors' => 'Bowyer et al.', 'year' => 2026, 'id' => '2605.25773',
            'from' => 'a small coreset of benchmark items',
            'approach' => 'feature selection plus regression to predict full-benchmark scores',
            'relation' => 'the item-level analogue of the multivariate view\'s elastic net, whose lasso part selects the useful benchmarks.'],
        ['title' => 'metabench: A Sparse Benchmark of Reasoning and Knowledge in Large Language Models', 'authors' => 'Kipnis et al.', 'year' => 2024, 'id' => '2407.12844',
            'from' => 'a sparse (~3%) subset of items',
            'approach' => 'item-level distillation that preserves scores and rankings',
            'relation' => 'item-level. benchgap works from published aggregate scores, so it needs no access to benchmark items at all.'],
        ['title' => 'Look Before you Leap: Estimating LLM Benchmark Scores from Descriptions', 'authors' => 'Park et al.', 'year' => 2025, 'id' => '2509.20645',
            'from' => 'a redacted text description of the task',
            'approach' => 'an LLM as the regressor (the PRECOG corpus); no evaluation runs at all',
            'relation' => 'predicts before any evaluation exists; benchgap predicts after a model has some measured scores. Complementary ends of the pipeline.'],
        ['title' => 'How predictable is language model benchmark performance?', 'authors' => 'Owen', 'year' => 2024, 'id' => '2401.04757',
            'from' => 'training compute',
            'approach' => 'empirical analysis of benchmark performance across five orders of magnitude of compute',
            'relation' => 'a different input: predictability against compute, not scores from scores.'],
        ['title' => 'How Benchmark Prediction from Fewer Data Misses the Mark', 'authors' => 'Zhang et al.', 'year' => 2025, 'id' => '2506.07673',
            'from' => null,
            'approach' => 'a systematic evaluation of 11 score-prediction methods across 19 benchmarks',
            'relation' => 'the caution this site\'s guardrails are built around: predictors fail on models unlike their calibration set.'],
        ['title' => 'PredictaBoard: Benchmarking LLM Score Predictability', 'authors' => 'Pacchiardi et al.', 'year' => 2025, 'id' => '2502.14445',
            'from' => null,
            'approach' => 'benchmarks score predictability itself, via assessors that anticipate a model\'s errors',
            'relation' => 'instance-level predictability rather than score-level estimation; a complementary lens on the same uncertainty.'],
    ];

    /** the multivariate view against the papers above (publications page, HTML paragraphs) */
    private const MULTIVARIATE_WORK = [
        'Predicting one benchmark from several others jointly is where benchgap meets the papers above head-on: '
        . 'BenchPress, Sloth and From Benchmarks to Skills do the same thing through latent factors over the whole score matrix. '
        . 'The <a href="/multivariate">multivariate view</a> does it with explicit features - the measured benchmarks themselves, '
        . 'named in every fit - selected per target benchmark.',
        'The selection echoes Efficient Benchmarking Is Just Feature Selection and Multiple Regression, one level up: '
        . 'they select items, benchgap selects benchmarks. An elastic net whose lasso part zeroes the useless candidates '
        . 'runs alongside a greedy forward search trying every one; on the features they find, a linear fit and a '
        . 'multivariate Michaelis–Menten curve compete by cross-validated error.',
        'What a joint model has and a per-target fit does not is strength borrowed across all benchmarks at once - '
        . 'BenchPress finds most of the score matrix is two numbers per model. benchgap trades that for fits a reader can check: '
        . 'every feature is a real benchmark, and every fit carries its own cross-validated error and a measured-vs-predicted scatter. '
        . 'These mappings are the deterministic precursor of that joint model: a Bayesian network over benchmark scores, '
        . 'imputing every gap with one coherent posterior, is where the roadmap points.',
    ];

    /** what sets benchgap apart from the papers above (publications page, HTML list items) */
    private const RELATED_WORK = [
        '<b>Capability gating.</b> A factor model imputes between any two benchmarks. benchgap calibrates only within a capability group, so a model never evaluated on vision keeps that gap instead of inheriting an estimate from text benchmarks.',
        '<b>No estimate recursion.</b> Every input to an estimate is a measured score; an estimate never feeds another estimate. A factor model completes a matrix that already contains its own outputs.',
        '<b>Per-cell error.</b> Each estimate carries its own leave-one-out error and confidence level, and a pair whose best curve still fits poorly keeps no mapping at all. The papers above report one aggregate error over held-out cells.',
        '<b>The shared limit.</b> As How Benchmark Prediction from Fewer Data Misses the Mark shows, every method in this line misestimates models unlike its calibration set. Confidence levels flag the known risk factors - extrapolation, small fits, weak R² - but nothing here detects a genuinely novel model.',
    ];


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
                '<p class="lede">Every benchmark, model, score and calibration, and the harness-tax analysis, as plain JSON (and CSV), '
                . "with each estimate's confidence level and error. Free, no key, readable from any origin. The site's views made from "
                . 'these scores (model compare, the multivariate and cross-domain fits) are not part of v1.</p>'
                . "<p>Base URL: <code>$base</code></p>")
            . '<ul>' . implode('', $endpoints) . '</ul>');
    }

    /** the 404 page (as renderNotFound, assets/js/ui.js) */
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

    /** the leaderboard's lede (benchLead, assets/js/pages/board.js), from its scores (highest first) */
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

    /** the model page's lede (modelLead, assets/js/pages/model.js) */
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
        $rules = [
            'Measured scores come from public evaluation leaderboards and are never altered; each benchmark is calibrated only against benchmarks of the same capability.',
            "For every ordered pair of benchmarks with at least {$g['min_pairs']} models measured on both, {$g['n_candidates']} monotone curves are fitted "
                . 'and the one with the lowest leave-one-out cross-validated error is kept. That error, in percentage points, is the ± shown with every estimate.',
            "A pair keeps no calibration unless its best curve reaches R² ≥ {$g['min_r2']} and an error of at most {$g['max_loo_pp']} pp; such gaps stay empty.",
            'A missing score is estimated from the best calibration out of a benchmark the model was measured on. Estimates are never used to make further estimates.',
            'Several same-capability benchmarks may be combined into a multivariate mapping: an elastic net whose lasso zeroes the useless sources or a greedy forward search finds the features, '
                . 'and then a linear fit and a multivariate Michaelis–Menten curve compete. It is stored only if it passes the gate and beats the target\'s best single calibration, '
                . 'and preferred for a model measured on all of its sources - a model missing one source falls back to the univariate path.',
            'Cross-domain predictability and the multivariate view are analyses only: no estimate crosses a capability.',
            "Confidence: <b>high</b> for an error up to {$r['high_max_pp']} pp, <b>medium</b> up to {$r['medium_max_pp']} pp, <b>low</b> above; "
                . "extrapolation, a fit on fewer than {$r['min_reliable_n']} models or R² below {$r['min_informative_r2']} each lower it by one level.",
            'The harness tax (see <a href="/harness-tax">/harness-tax</a>) measures how much harnesses disagree about the same models on the same benchmark, from measured scores only; '
                . 'it never feeds the estimates, and it is why each of them holds for its source harness\'s evaluation setup only.',
            'Estimates are predictions, not measurements, and hold for the source leaderboard\'s evaluation setup only.',
        ];
        // the end-to-end validation (method.js validated() reads the same numbers)
        $h = $this->index['holdout'] ?? null;
        if ($h !== null) {
            $lv = $h['by_level_mae_pp'];
            $rules[] = sprintf(
                'The whole pipeline is validated end to end: masking measured scores and refitting everything, estimates fill about %d%% of masked cells at about %.1f pp mean absolute error '
                    . '(%d%% lower than a matrix-completion baseline on the same cells); the confidence levels are correctly ordered (%.1f / %.1f / %.1f pp realized MAE for high/medium/low), '
                    . 'though the ± labels understate the realized RMSE by roughly 40–70%%; reweighted to the published mix, a published estimate should be expected to carry about %.1f pp MAE (%.1f pp RMSE).',
                100 * $h['random_coverage'][0],
                $h['random_mae_pp'][0],
                (int) round($h['pipeline_vs_svd2_pct']),
                $lv['high'], $lv['medium'], $lv['low'],
                $h['reweighted']['mae_pp'], $h['reweighted']['rmse_pp'],
            );
        }
        return $rules;
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
        foreach ($this->listed as $b) {
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

    /** A model's scores (API objects) on the listed benchmarks. */
    private function listedScores(string $slug): array
    {
        return array_filter($this->api->model($slug)['scores'], fn ($s) => isset($this->listed[$s['benchmark']]));
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

    /** The site a URL points to, for link text: "https://benchlm.ai/benchmarks/x" -> "benchlm.ai". */
    private static function host(string $url): string
    {
        return preg_replace('/^www\./', '', parse_url($url, PHP_URL_HOST) ?: $url);
    }
}
