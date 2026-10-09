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
 * against - and the closest frontier model of one is the frontier model whose
 * general performance differs from it the least, itself excepted.
 */
final class Compare
{
    /** The compare document of a snapshot: general performance per model, and the frontier. */
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
        }
        $general = [];
        foreach ($n as $m => $count) {
            $general[] = [(int) $m, round($sum[$m] / $count, 4), $count];
        }
        usort($general, fn ($x, $y) => $y[1] <=> $x[1] ?: $y[2] <=> $x[2] ?: $x[0] <=> $y[0]);

        $dense = array_fill_keys(array_column(array_filter($data['models'], fn ($m) => $m['dense']), 'id'), true);
        $frontier = array_values(array_filter($general, fn ($g) => isset($dense[$g[0]])));
        $frontier = array_slice($frontier, 0, max(1, (int) ceil(count($frontier) / 10)));
        return ['general' => $general, 'frontier' => $frontier];
    }

    /**
     * The frontier model closest in general performance to $target (a model's general
     * score), the model $excludeSlug itself excepted; null if there is none. $frontier
     * holds the rows Api::compare() shapes (with 'slug' and 'general'), strongest first;
     * ties keep the stronger row.
     */
    public static function closest(array $frontier, ?float $target, ?string $excludeSlug = null): ?array
    {
        if ($target === null) {
            return null;
        }
        $best = null;
        $bestD = null;
        foreach ($frontier as $f) {
            if ($f['slug'] === $excludeSlug) {
                continue;
            }
            $d = abs($f['general'] - $target);
            if ($best === null || $d < $bestD) {
                $best = $f;
                $bestD = $d;
            }
        }
        return $best;
    }
}
