<?php

declare(strict_types=1);

namespace Benchgap;

/**
 * The model-compare view's numbers (the /compare page): every model's general
 * performance, and the frontier set.
 *
 * General performance is the mean percentile of a model's measured scores
 * across the listed benchmarks: on each benchmark, the share of measured
 * models it scores above (benchmarks with fewer than two measured models give
 * no standing). Measured scores only, like every analysis on the site; a
 * model with no standing anywhere gets none.
 *
 * The frontier is the top tenth of the dense models (Snapshot::DENSE) by
 * general performance - the strongest field a model can reasonably be held
 * against. The frontier model one is held against (pick()) is, for a frontier
 * model, its closest rival in general performance; for a model below the
 * frontier, the closest of the frontier models it shares the most measured
 * benchmarks with (at least two thirds as many as the best-covered one) - the
 * closest of all would be the same weakest frontier model for most of them.
 */
final class Compare
{
    // a model below the frontier is held against the frontier models sharing at least this share
    // of the most measured benchmarks any of them shares with it (as SHARED_FLOOR, compare.js)
    public const SHARED_FLOOR = 2 / 3;

    /**
     * The compare document of a snapshot: general performance per model, as [model, general,
     * n, the benchmarks (ids) it stands on], strongest first, and the frontier (the same rows).
     */
    public static function compute(array $data): array
    {
        $listed = array_fill_keys(array_column(array_filter($data['benchmarks'], fn ($b) => $b['listed']), 'id'), true);
        $byBench = [];   // benchmark id => its measured scores, sorted
        foreach ($data['scores'] as $s) {
            if ($s['s'] === 'm' && isset($listed[$s['b']])) {
                $byBench[$s['b']][] = $s['v'];
            }
        }
        foreach ($byBench as &$scores) {
            sort($scores);
        }
        unset($scores);

        // percentile: the share of the benchmark's other measured models strictly below v
        $percentile = function (int $b, float $v) use ($byBench): ?float {
            $scores = $byBench[$b] ?? [];
            $n = count($scores);
            if ($n < 2) {
                return null;
            }
            $lo = 0;
            $hi = $n;
            while ($lo < $hi) {
                $mid = intdiv($lo + $hi, 2);
                if ($scores[$mid] < $v) {
                    $lo = $mid + 1;
                } else {
                    $hi = $mid;
                }
            }
            return $lo / ($n - 1);
        };

        $sum = [];
        $n = [];
        $on = [];   // model id => the benchmarks it stands on
        foreach ($data['scores'] as $s) {
            if ($s['s'] !== 'm' || !isset($listed[$s['b']])) {
                continue;
            }
            $p = $percentile($s['b'], $s['v']);
            if ($p === null) {
                continue;
            }
            $sum[$s['m']] = ($sum[$s['m']] ?? 0) + $p;
            $n[$s['m']] = ($n[$s['m']] ?? 0) + 1;
            $on[$s['m']][] = $s['b'];
        }
        $general = [];
        foreach ($n as $m => $count) {
            sort($on[$m]);
            $general[] = [(int) $m, round($sum[$m] / $count, 4), $count, $on[$m]];
        }
        usort($general, fn ($x, $y) => $y[1] <=> $x[1] ?: $y[2] <=> $x[2] ?: $x[0] <=> $y[0]);

        $dense = array_fill_keys(array_column(array_filter($data['models'], fn ($m) => $m['dense']), 'id'), true);
        $frontier = array_values(array_filter($general, fn ($g) => isset($dense[$g[0]])));
        $frontier = array_slice($frontier, 0, max(1, (int) ceil(count($frontier) / 10)));
        return ['general' => $general, 'frontier' => $frontier];
    }

    /**
     * The frontier model model $slug is held against, itself excepted: of $provider's frontier
     * models, or of the whole frontier when it has none (or $provider is null). A frontier
     * model gets its closest rival in general performance; a model below the frontier the
     * closest of those it shares at least two thirds as many measured benchmarks with as the
     * best-covered one (SHARED_FLOOR; none shared: the closest of all); $model null (a model
     * with no standing) the strongest. Null if there is none. $frontier and $model are rows Api::compare() shapes (with 'slug', 'provider',
     * 'general' and 'benchmarks'), the frontier strongest first; ties keep the stronger row.
     * (As frontierPick, assets/js/pages/compare.js)
     */
    public static function pick(array $frontier, string $slug, ?array $model, ?string $provider = null): ?array
    {
        $others = array_values(array_filter($frontier, fn ($f) => $f['slug'] !== $slug));
        $own = array_values(array_filter($others, fn ($f) => $f['provider'] === $provider));
        $pool = $own ?: $others;
        if (!$pool || $model === null) {
            return $pool[0] ?? null;
        }
        if (count($others) === count($frontier)) {   // below the frontier: of the best-covered ones
            $mine = array_flip($model['benchmarks']);
            $shared = array_map(fn ($f) => count(array_intersect_key(array_flip($f['benchmarks']), $mine)), $pool);
            $floor = self::SHARED_FLOOR * max($shared);
            $pool = array_values(array_filter($pool, fn ($f, $i) => $shared[$i] >= $floor, ARRAY_FILTER_USE_BOTH));
        }
        $best = $pool[0];
        foreach ($pool as $f) {
            if (abs($f['general'] - $model['general']) < abs($best['general'] - $model['general'])) {
                $best = $f;
            }
        }
        return $best;
    }
}
