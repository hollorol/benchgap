<?php

declare(strict_types=1);

namespace Benchgap;

/**
 * The fitted curve families of src/benchgap/fitting.py, for display: the
 * equation with its fitted parameters, and the curve sampled for plotting.
 * Fitting itself happens only in Python.
 */
final class Curves
{
    /** Method => equation (sprintf template) and its parameters, in order. */
    public const EQUATIONS = [
        'linear' => ['y = %1$.4f·x + %2$.4f', ['slope', 'intercept']],
        'mm' => ['y = %1$.4f·x / (%2$.5f + x)', ['vmax', 'k']],
        'mm_offset' => ['y = %1$.4f + %2$.4f·x / (%3$.5f + x)', ['y0', 'vmax', 'k']],
        'mm_offset_inv' => ['y = %3$.5f·(x − %1$.4f) / (%1$.4f + %2$.4f − x)', ['y0', 'vmax', 'k']],
        'hill' => ['y = %1$.4f + (%2$.4f − %1$.4f)·x^%4$.2f / (%3$.5f^%4$.2f + x^%4$.2f)', ['y0', 'a', 'k', 'n']],
        'logistic' => ['y = %1$.4f + (%2$.4f − %1$.4f) / (1 + exp(−%3$.2f·(x − %4$.4f)))', ['y0', 'a', 'k', 'xmid']],
    ];

    /** Samples over [0, 1], enough for a smooth polyline in the browser. */
    private const SAMPLES = 51;

    public static function equation(string $method, array $params): string
    {
        [$template, $names] = self::EQUATIONS[$method];
        return sprintf($template, ...array_map(fn ($n) => $params[$n], $names));
    }

    /** [[x, y], ...] over [0, 1] plus the training-range ends; non-finite points are left out. */
    public static function sample(string $method, array $params, ?array $range): array
    {
        $xs = array_map(fn ($i) => $i * (1 / (self::SAMPLES - 1)), range(0, self::SAMPLES - 1));
        $xs = array_unique([...$xs, ...array_filter($range ?? [], fn ($x) => $x !== null)], SORT_REGULAR);
        sort($xs);
        $curve = [];
        foreach ($xs as $x) {
            $y = self::predict($method, $params, $x);
            if (is_finite($y)) {
                $curve[] = [round($x, 4), round($y, 4)];
            }
        }
        return $curve;
    }

    /** A curve's score at $x, clipped to [0, 1] as the pipeline clips its predictions (fitting.py). */
    public static function predict(string $method, array $p, float $x): float
    {
        $y = match ($method) {
            'linear' => $p['slope'] * $x + $p['intercept'],
            'mm' => fdiv($p['vmax'] * $x, $p['k'] + $x),
            'mm_offset' => $p['y0'] + fdiv($p['vmax'] * $x, $p['k'] + $x),
            // inverse of y0 + vmax·u/(k + u): 0 up to y0, 1 from the ceiling on
            'mm_offset_inv' => match (true) {
                $x <= $p['y0'] => 0.0,
                $p['y0'] + $p['vmax'] - $x <= 1e-9 => 1.0,
                default => fdiv($p['k'] * ($x - $p['y0']), $p['y0'] + $p['vmax'] - $x),
            },
            'hill' => $p['y0'] + ($p['a'] - $p['y0']) * fdiv($x ** $p['n'], $p['k'] ** $p['n'] + $x ** $p['n']),
            'logistic' => $p['y0'] + fdiv($p['a'] - $p['y0'], 1.0 + exp(-$p['k'] * ($x - $p['xmid']))),
        };
        return is_finite($y) ? min(1.0, max(0.0, $y)) : $y;   // non-finite: sample() leaves the point out
    }
}
