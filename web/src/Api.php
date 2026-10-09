<?php

declare(strict_types=1);

namespace Benchgap;

/**
 * The public API (api/v1/...): the site's data document (Snapshot) as
 * read-only resources with stable, human-readable keys. Benchmarks are
 * addressed by name/version and models by slug, never by database ids, which
 * change between builds; mappings keep their id, the only handle they have.
 * The endpoints are described in web/api/v1/openapi.json.
 */
final class Api
{
    private const VERSION = 'v1';
    public const SITE_URL = 'https://benchgap.net';
    private const URL = self::SITE_URL . '/api/' . self::VERSION;
    private const REPO_URL = 'https://github.com/hollorol/benchgap';
    private const DATA_NOTICE = 'Measured scores: data from BenchLM.ai (https://benchlm.ai/data), CC BY-NC 4.0'
        . ' (https://creativecommons.org/licenses/by-nc/4.0/); estimates are benchgap\'s additions. The benchgap code is MIT-licensed.';
    private const CSV_COLUMNS = [
        'model', 'model_name', 'provider', 'benchmark', 'benchmark_label', 'capability',
        'score', 'source', 'confidence', 'error_pp', 'extrapolated', 'method', 'inputs',
    ];

    // snapshot rows by database id, and by their public keys
    private array $bench;
    private array $model;
    private array $mapping;
    private array $benchByKey;
    private array $modelBySlug;
    private array $scores;          // every score as an API object
    private array $scoresBy = ['benchmark' => [], 'model' => [], 'mapping' => []];  // ... grouped by key
    private array $about;           // fields every JSON document carries

    public function __construct(private readonly array $data)
    {
        $this->bench = array_column($data['benchmarks'], null, 'id');
        $this->model = array_column($data['models'], null, 'id');
        $this->mapping = array_column($data['mappings'], null, 'id');
        $this->benchByKey = array_column($data['benchmarks'], null, 'key');
        $this->modelBySlug = array_column($data['models'], null, 'slug');
        $this->scores = array_map($this->score(...), $data['scores']);
        foreach ($this->scores as $s) {
            $this->scoresBy['benchmark'][$s['benchmark']][] = $s;
            $this->scoresBy['model'][$s['model']][] = $s;
            if (isset($s['estimate']['mapping_id'])) {
                $this->scoresBy['mapping'][$s['estimate']['mapping_id']][] = $s;
            }
        }
        $meta = $data['meta'];
        $this->about = [
            'api_version' => self::VERSION,
            'generated_at' => $meta['generated_at'],
            'data_retrieved_at' => $meta['retrieved_at'],
            'harnesses' => $meta['harnesses'],
            'counts' => $meta['counts'],
        ];
    }

    // -- documents ---------------------------------------------------------------

    public function index(): array
    {
        return $this->about + [
            'documentation' => self::SITE_URL . '/api',
            'openapi' => self::URL . '/openapi.json',
            'source_code' => self::REPO_URL,
            'data_notice' => self::DATA_NOTICE,
            'endpoints' => [
                'benchmarks' => 'benchmarks.json',
                'benchmark' => 'benchmarks/{name}/{version}.json',
                'models' => 'models.json',
                'model' => 'models/{slug}.json',
                'scores' => 'scores.json',
                'scores_csv' => 'scores.csv',
                'mappings' => 'mappings.json',
                'mapping' => 'mappings/{id}.json',
                'harness_tax' => 'harness-tax.json',
                'harness_tax_family' => 'harness-tax/{family_id}.json',
                'openapi' => 'openapi.json',
            ],
            'confidence_levels' => $this->data['meta']['confidence_levels'],
            'quality_gate' => $this->data['meta']['quality_gate'],
            'capabilities' => $this->data['capabilities'],
            'holdout' => $this->data['holdout']['headline'] ?? null,
        ];
    }

    public function benchmarks(): array
    {
        return $this->about + ['benchmarks' => array_map($this->benchmarkObject(...), $this->data['benchmarks'])];
    }

    /** One benchmark with its scores, highest first; null if there is no such benchmark. */
    public function benchmark(string $key): ?array
    {
        $b = $this->benchByKey[$key] ?? null;
        if ($b === null) {
            return null;
        }
        $scores = $this->scoresBy['benchmark'][$key] ?? [];
        usort($scores, fn ($x, $y) => $y['score'] <=> $x['score']);
        return $this->about + ['benchmark' => $this->benchmarkObject($b), 'scores' => $scores];
    }

    public function models(): array
    {
        return $this->about + ['models' => array_map($this->modelObject(...), $this->data['models'])];
    }

    public function model(string $slug): ?array
    {
        $m = $this->modelBySlug[$slug] ?? null;
        if ($m === null) {
            return null;
        }
        return $this->about + [
            'model' => $this->modelObject($m),
            'scores' => $this->scoresBy['model'][$slug] ?? [],
        ];
    }

    public function scores(): array
    {
        return $this->about + ['scores' => $this->scores];
    }

    public function mappings(): array
    {
        // the snapshot lists mappings by id
        return $this->about + ['mappings' => array_map(fn ($m) => $this->mappingObject($m), $this->data['mappings'])];
    }

    /** The multivariate view's fits, by benchmark key (for its page; not part of the API). */
    public function multivariate(): array
    {
        return array_map(fn ($m) => [
            'target' => $this->bench[$m['to']]['key'],
            'features' => array_map(fn ($f) => $this->bench[$f]['key'], $m['from']),
            'method_name' => $this->methodName($m['method']),
            'n_models' => $m['n'],
            'loo_rmse_pp' => self::pp($m['loo']),
            'alone_loo_rmse_pp' => self::pp($m['alone']),
        ], $this->data['cross_multi_mappings'] ?? []);
    }

    /**
     * The harness-tax analysis: the families, every pair with its metrics, the tier
     * aggregates and the audit queue. Measured scores only; never used for estimates.
     */
    public function harnessTax(): array
    {
        return $this->about + [
            'measured_only' => true,
            'families' => array_map($this->harnessFamilyObject(...), $this->data['harness_tax']['families'] ?? []),
            'pairs' => array_map($this->harnessPairObject(...), $this->data['harness_tax']['pairs'] ?? []),
            'aggregates' => $this->data['harness_tax']['aggregates'] ?? [],
            'audit' => ['outliers' => $this->outliers()],
            'url' => self::URL . '/harness-tax.json',
        ];
    }

    /** One family's pairs with every per-model delta and its provenance; null if there is none. */
    public function harnessTaxFamily(string $familyId): ?array
    {
        $families = array_values(array_filter($this->data['harness_tax']['families'] ?? [], fn ($f) => $f['family_id'] === $familyId));
        if (!$families) {
            return null;
        }
        $deltas = $this->data['harness_tax']['deltas'] ?? [];
        $pairs = array_values(array_filter($this->data['harness_tax']['pairs'] ?? [], fn ($p) => $p['family_id'] === $familyId));
        return $this->about + [
            'measured_only' => true,
            'family' => $this->harnessFamilyObject($families[0]),
            'pairs' => array_map(fn ($p) => $this->harnessPairObject($p)
                + ['deltas' => array_map($this->harnessDeltaObject(...), $deltas[$p['id']] ?? [])], $pairs),
        ];
    }

    // a per-model |delta| beyond this lands in the audit queue (harness_tax.OUTLIER_PP)
    private const OUTLIER_PP = 20.0;

    /** The audit queue: per-model deltas beyond OUTLIER_PP, with both scores' provenance. */
    private function outliers(): array
    {
        $byId = [];
        foreach ($this->data['harness_tax']['pairs'] ?? [] as $p) {
            $byId[$p['id']] = $p;
        }
        $out = [];
        foreach ($this->data['harness_tax']['deltas'] ?? [] as $pairId => $deltas) {
            foreach ($deltas as $d) {
                if (abs($d['delta_pp']) > self::OUTLIER_PP) {
                    $p = $byId[$pairId];
                    $out[] = $this->harnessDeltaObject($d)
                        + ['family_id' => $p['family_id'], 'a' => $p['a'], 'b' => $p['b']];
                }
            }
        }
        return $out;
    }

    private function harnessFamilyObject(array $f): array
    {
        return [
            'family_id' => $f['family_id'],
            'label' => $f['label'],
            'capability' => $f['capability'],
            'tier' => $f['tier'],
            'same_item_set' => $f['same_item_set'],
            'status' => $f['status'],
            'pair_type' => $f['pair_type'],
            'audit_note' => $f['audit_note'],
            'origin' => $f['origin'],
            'versions' => $f['versions'],
            'url' => self::URL . '/harness-tax/' . rawurlencode($f['family_id']) . '.json',
        ];
    }

    private function harnessPairObject(array $p): array
    {
        return [
            'family_id' => $p['family_id'],
            'tier' => $p['tier'],
            'capability' => $p['capability'],
            'pair_type' => $p['pair_type'],
            'same_item_set' => $p['same_item_set'],
            'status' => $p['status'],
            'low_overlap' => $p['low_overlap'],
            'a' => $p['a'],
            'b' => $p['b'],
            'n_models' => $p['n'],
            'mean_abs_pp' => $p['mean_abs_pp'],
            'median_abs_pp' => $p['median_abs_pp'],
            'max_abs_pp' => $p['max_abs_pp'],
            'share_gt_5pp' => $p['share_gt_5'],
            'share_gt_10pp' => $p['share_gt_10'],
            'kendall_tau' => $p['kendall_tau'],
            'n_rank_flips' => $p['n_rank_flips'],
            'n_positive' => $p['n_positive'],
            'n_negative' => $p['n_negative'],
            'sign_test_p' => $p['sign_p'],
            'directionality' => $p['directionality'],
        ];
    }

    private function harnessDeltaObject(array $d): array
    {
        return [
            'model' => $d['slug'],
            'name' => $d['name'],
            'score_a' => $d['score_a'],
            'score_b' => $d['score_b'],
            'delta_pp' => $d['delta_pp'],
            'retrieved_a' => $d['retrieved_a'],
            'retrieved_b' => $d['retrieved_b'],
        ];
    }

    /** One mapping with its points, curve and the estimates it produced. */
    public function mapping(int $id): ?array
    {
        $m = $this->mapping[$id] ?? null;
        if ($m === null) {
            return null;
        }
        return $this->about + [
            'mapping' => $this->mappingObject($m, true),
            'estimates' => $this->scoresBy['mapping'][$id] ?? [],
        ];
    }

    /** Every score as CSV rows (CSV_COLUMNS), for spreadsheets. */
    public function scoresCsv(): string
    {
        $out = fopen('php://temp', 'r+');
        fputcsv($out, self::CSV_COLUMNS, escape: '');
        foreach ($this->scores as $s) {
            $e = $s['estimate'] ?? [];
            $m = $this->modelBySlug[$s['model']];
            $b = $this->benchByKey[$s['benchmark']];
            fputcsv($out, [
                $s['model'], $m['name'], $m['provider'], $s['benchmark'], $b['label'], $b['capability'],
                $s['score'], $s['source'], $e['confidence'] ?? '', $e['error_pp'] ?? '',
                // v1 has always written the flag as True/False
                isset($e['extrapolated']) ? ($e['extrapolated'] ? 'True' : 'False') : '',
                $e['method'] ?? '',
                implode(';', array_map(fn ($i) => "{$i['benchmark']}={$i['score']}", $e['inputs'] ?? [])),
            ], escape: '');
        }
        rewind($out);
        return stream_get_contents($out);
    }

    // -- objects -------------------------------------------------------------------

    private function benchmarkObject(array $b): array
    {
        [$name, $version] = explode('/', $b['key'], 2);
        return [
            'key' => $b['key'],
            'name' => $name,
            'version' => $version,
            'label' => $b['label'],
            'capability' => $b['capability'],
            'harness' => $b['harness'],
            'source_url' => $b['source_url'],
            'n_measured' => $b['n_measured'],
            'n_estimated' => $b['n_estimated'],
            'listed' => $b['listed'],
            'url' => self::URL . "/benchmarks/{$b['key']}.json",
            'page' => self::SITE_URL . "/b/{$b['key']}",
        ];
    }

    private function modelObject(array $m): array
    {
        return [
            'slug' => $m['slug'],
            'name' => $m['name'],
            'provider' => $m['provider'],
            'provider_name' => $this->data['meta']['providers'][$m['provider']] ?? $m['provider'],
            'n_measured' => $m['n_measured'],
            'n_estimated' => $m['n_estimated'],
            'listed' => $m['listed'],
            'url' => self::URL . "/models/{$m['slug']}.json",
            'page' => self::SITE_URL . '/model/' . rawurlencode($m['slug']),
        ];
    }

    private function score(array $s): array
    {
        $out = [
            'model' => $this->model[$s['m']]['slug'],
            'benchmark' => $this->bench[$s['b']]['key'],
            'score' => $s['v'],
            'source' => $s['s'] === 'm' ? 'measured' : 'estimated',
            'estimate' => null,
        ];
        if ($s['s'] === 'e') {
            $uni = $s['via']['kind'] === 'uni';
            $out['estimate'] = [
                'confidence' => $s['tier'],
                'error_pp' => self::pp($s['sd']),
                'reasons' => $s['why'],
                'extrapolated' => $s['x'],
                'method' => $s['method'],
                'method_name' => $this->methodName($s['method']),
                'kind' => $uni ? 'univariate' : 'multivariate',
                'mapping_id' => $uni ? $s['via']['mapping'] : null,
                'inputs' => array_map(
                    fn ($f) => ['benchmark' => $this->bench[$f['b']]['key'], 'score' => $f['v']],
                    $s['via']['from']
                ),
            ];
        }
        return $out;
    }

    private function mappingObject(array $m, bool $withPoints = false): array
    {
        $out = [
            'id' => $m['id'],
            'from' => $this->bench[$m['from']]['key'],
            'to' => $this->bench[$m['to']]['key'],
            'method' => $m['method'],
            'method_name' => $this->methodName($m['method']),
            'equation' => $m['equation'],
            'n_models' => $m['n'],
            'r2' => $m['r2'],
            'rmse_pp' => self::pp($m['rmse']),
            'loo_rmse_pp' => self::pp($m['loo']),
            'train_range' => $m['range'],
            'n_estimates' => $m['n_used'],
            'url' => self::URL . "/mappings/{$m['id']}.json",
            'page' => self::SITE_URL . "/calibration/{$m['id']}",
        ];
        if ($withPoints) {
            $out['points'] = array_map(
                fn ($p) => ['model' => $this->model[$p[0]]['slug'], 'x' => $p[1], 'y' => $p[2]],
                $m['points']
            );
            $out['curve'] = $m['curve'];
        }
        return $out;
    }

    private function methodName(string $method): string
    {
        return $this->data['meta']['methods'][$method] ?? $method;
    }

    /** A fraction as percentage points, 2 decimals. */
    private static function pp(?float $x): ?float
    {
        return $x === null ? null : round($x * 100, 2);
    }
}
