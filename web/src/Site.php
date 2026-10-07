<?php

declare(strict_types=1);

namespace Benchgap;

/**
 * The front-end's documents (data/...): each page of app.js loads only its own
 * slice of the site data (Snapshot) instead of every score at once.
 *
 * - site.json: what every page needs (build metadata, capabilities, every
 *   benchmark and model with its counts, the home page's benchmark)
 * - home.json, b/{name}/{version}.json: a leaderboard's scores
 * - model/{slug}.json: a model's scores
 * - matrix.json: every listed benchmark's cells, as [model, benchmark, value, kind]
 *   (kind: CELL_KINDS); an estimate's details come from score/{m}/{b}.json
 * - calibration.json: the calibrations between listed benchmarks, without
 *   their points and curves
 * - calibration/{id}.json: one calibration with its points, curve and estimates
 *
 * Scores, benchmarks, models and mappings keep the Snapshot's shapes and ids.
 */
final class Site
{
    // a matrix cell's kind: measured, or the confidence of an estimate
    public const CELL_KINDS = ['m' => 0, 'high' => 1, 'medium' => 2, 'low' => 3];

    private array $listed;   // listed benchmark ids => true

    public function __construct(private readonly array $data)
    {
        $this->listed = array_fill_keys(array_column(array_filter($data['benchmarks'], fn ($b) => $b['listed']), 'id'), true);
    }

    public function site(): array
    {
        return [
            'meta' => $this->data['meta'] + ['home' => Snapshot::home($this->data['benchmarks'])],
            'capabilities' => $this->data['capabilities'],
            'benchmarks' => $this->data['benchmarks'],
            'models' => $this->data['models'],
        ];
    }

    /** A leaderboard: the benchmark's id and all its scores; null if there is no such benchmark. */
    public function board(string $key): ?array
    {
        $b = $this->find($this->data['benchmarks'], 'key', $key);
        return $b === null ? null : ['benchmark' => $b['id'], 'scores' => $this->scores(fn ($s) => $s['b'] === $b['id'])];
    }

    public function home(): array
    {
        return $this->board(Snapshot::home($this->data['benchmarks']));
    }

    /** A model's id and all its scores; null if there is no such model. */
    public function model(string $slug): ?array
    {
        $m = $this->find($this->data['models'], 'slug', $slug);
        return $m === null ? null : ['model' => $m['id'], 'scores' => $this->scores(fn ($s) => $s['m'] === $m['id'])];
    }

    public function matrix(): array
    {
        $cells = [];
        foreach ($this->data['scores'] as $s) {
            if (isset($this->listed[$s['b']])) {
                // 4 decimals: the matrix shows whole percents
                $cells[] = [$s['m'], $s['b'], round($s['v'], 4), self::CELL_KINDS[$s['s'] === 'm' ? 'm' : $s['tier']]];
            }
        }
        return ['cells' => $cells];
    }

    /** One score in full (an estimate with where it came from); null if there is none. */
    public function score(int $model, int $benchmark): ?array
    {
        return $this->scores(fn ($s) => $s['m'] === $model && $s['b'] === $benchmark)[0] ?? null;
    }

    public function calibration(): array
    {
        $maps = [];
        foreach ($this->data['mappings'] as $m) {
            if (isset($this->listed[$m['from']], $this->listed[$m['to']])) {
                $maps[] = array_intersect_key($m, array_flip(['id', 'from', 'to', 'method', 'n', 'r2', 'loo', 'n_used']));
            }
        }
        return ['mappings' => $maps];
    }

    /** One calibration with its points and curve, the estimates it made and its reverse; null if there is none. */
    public function mapping(int $id): ?array
    {
        $m = $this->find($this->data['mappings'], 'id', $id);
        if ($m === null) {
            return null;
        }
        $reverse = null;
        foreach ($this->data['mappings'] as $r) {
            if ($r['from'] === $m['to'] && $r['to'] === $m['from']) {
                $reverse = ['id' => $r['id'], 'loo' => $r['loo']];
            }
        }
        return [
            'mapping' => $m,
            'estimates' => $this->scores(fn ($s) => $s['s'] === 'e' && $s['via']['kind'] === 'uni' && $s['via']['mapping'] === $id),
            'reverse' => $reverse,
        ];
    }

    private function scores(callable $keep): array
    {
        return array_values(array_filter($this->data['scores'], $keep));
    }

    private function find(array $rows, string $field, mixed $value): ?array
    {
        foreach ($rows as $row) {
            if ($row[$field] === $value) {
                return $row;
            }
        }
        return null;
    }
}
