<?php

declare(strict_types=1);

namespace Benchgap;

/**
 * The model-compare view's numbers (the /compare page): every model's general
 * performance, and the frontier set.
 *
 * General performance is the share of benchmark duels a model is expected to
 * win against every other model with a standing. On each listed benchmark,
 * every two models with a score there, measured or estimated, play a duel:
 * the higher score wins, a tie is half a win each. A Bradley-Terry fit over
 * all the duels gives each model a strength p, and model i is expected to win
 * p_i / (p_i + p_j) of its duels against model j; general performance is the
 * mean of that over every other model with a standing. Each pair is held
 * against each other only on the benchmarks both have a score on, so a model
 * is not lifted by the benchmarks it was run on and others were not, and the
 * fit weighs a win by the strength of the one beaten. The estimates keep a
 * model from being judged only where it happened to be run. A model with no
 * listed score gets no standing.
 *
 * Nor does a model off the main field: its duels are only with models within
 * one field. The main field is the largest set of listed benchmarks linked by
 * the dense models measured on them (two benchmarks are linked when at least
 * LINK of them are measured on both); a model measured on benchmarks apart
 * from it and on none of its own (or, measured on no listed benchmark, estimated
 * only on benchmarks apart from it), such as the routing models of a family of
 * decision benchmarks only they are run on, stands high among a field no
 * general model is in, and its standing would put it on the frontier.
 *
 * The frontier is the top tenth of the dense models (Snapshot::DENSE) by
 * general performance - the strongest field a model can reasonably be held
 * against. The frontier model one is held against (pick()) is the closest in
 * general performance of the frontier models it shares the most measured
 * benchmarks with (at least two thirds as many as the best-covered one), so
 * the pair can be held side by side on measurements - for a model below the
 * frontier, the closest of all would be the same weakest frontier model for
 * most of them. Or a model is
 * held against the one just below it (below()), of the dense models.
 */
final class Compare
{
    // a model is held against the frontier models sharing at least this share of the most
    // measured benchmarks any of them shares with it (as SHARED_FLOOR, compare.js)
    public const SHARED_FLOOR = 2 / 3;
    // two listed benchmarks are linked (one field) when at least this many dense models are measured on both
    public const LINK = 5;
    // the Bradley-Terry fit (strengths()): virtual tied duels per model, the largest change in a
    // log strength that ends it, and the most rounds
    private const PRIOR = 1;
    private const TOLERANCE = 1e-8;
    private const MAX_ROUNDS = 5000;

    /**
     * The compare document of a snapshot: general performance per model, as [model, general,
     * n (the benchmarks it stands on, measured or estimated), the benchmarks (ids) it stands on
     * measured], strongest first, and the frontier (the same rows).
     */
    public static function compute(array $data): array
    {
        $listed = array_fill_keys(array_column(array_filter($data['benchmarks'], fn ($b) => $b['listed']), 'id'), true);
        $byBench = [];   // benchmark id => model id => its score there, measured or estimated
        $measured = [];   // model id => the benchmarks it stands on measured
        $scored = [];   // model id => the benchmarks it stands on, measured or estimated
        foreach ($data['scores'] as $s) {
            if (!isset($listed[$s['b']])) {
                continue;
            }
            $byBench[$s['b']][$s['m']] = $s['v'];
            $scored[$s['m']][$s['b']] = true;
            if ($s['s'] === 'm') {
                $measured[$s['m']][] = $s['b'];
            }
        }
        $dense = array_fill_keys(array_column(array_filter($data['models'], fn ($m) => $m['dense']), 'id'), true);
        $offField = self::offField(array_intersect_key($measured, $dense));
        $standing = [];   // the models with a standing, by id
        foreach ($scored as $m => $on) {
            // measured (or, measured nowhere listed, estimated) off the main field only: no standing
            if (array_diff_key(isset($measured[$m]) ? array_flip($measured[$m]) : $on, $offField)) {
                $standing[] = $m;
            }
        }
        sort($standing);
        $at = array_flip($standing);   // model id => its index in $standing

        // the duels: on each listed benchmark, every two models with a standing and a score there
        $n = count($standing);
        $wins = array_fill(0, $n, 0.0);   // index => duels won (a tie is half a win)
        $played = [];   // i * n + j (i < j) => the duels the two played
        foreach ($byBench as $scores) {
            $field = [];   // index => score
            foreach ($scores as $m => $v) {
                if (isset($at[$m])) {
                    $field[$at[$m]] = $v;
                }
            }
            ksort($field);
            $players = array_keys($field);
            $count = count($players);
            for ($k = 0; $k < $count; $k++) {
                $i = $players[$k];
                for ($l = $k + 1; $l < $count; $l++) {
                    $j = $players[$l];
                    if ($field[$i] > $field[$j]) {
                        $wins[$i]++;
                    } elseif ($field[$i] < $field[$j]) {
                        $wins[$j]++;
                    } else {
                        $wins[$i] += 0.5;
                        $wins[$j] += 0.5;
                    }
                    $played[$i * $n + $j] = ($played[$i * $n + $j] ?? 0) + 1;
                }
            }
        }
        $met = [];   // index => index => the duels the two played
        foreach ($played as $key => $duels) {
            $met[intdiv($key, $n)][$key % $n] = $duels;
            $met[$key % $n][intdiv($key, $n)] = $duels;
        }
        $p = self::strengths($wins, $met);

        $general = [];
        $others = $n - 1;
        foreach ($standing as $i => $m) {
            $share = 0.0;   // the expected share of duels won against every other model with a standing
            $pi = $p[$i];
            foreach ($p as $j => $pj) {
                if ($j !== $i) {
                    $share += $pi / ($pi + $pj);
                }
            }
            $on = $measured[$m] ?? [];
            sort($on);
            $general[] = [(int) $m, round($others ? $share / $others : 0.5, 4), count($scored[$m]), $on];
        }
        usort($general, fn ($x, $y) => $y[1] <=> $x[1] ?: $y[2] <=> $x[2] ?: $x[0] <=> $y[0]);

        $frontier = array_values(array_filter($general, fn ($g) => isset($dense[$g[0]])));
        $frontier = array_slice($frontier, 0, max(1, (int) ceil(count($frontier) / 10)));
        return ['general' => $general, 'frontier' => $frontier];
    }

    /**
     * Bradley-Terry strengths from the duels ($wins: index => duels won; $met: index => index =>
     * duels played), by the MM iteration: p_i = (wins_i + PRIOR/2) / (sum_j met_ij / (p_i + p_j)
     * + PRIOR / (p_i + 1)), every round from the last, scaled to a geometric mean of 1. PRIOR is
     * that many virtual duels, tied, against a model of strength 1, so a model that won (or lost)
     * every duel still gets a finite strength.
     */
    private static function strengths(array $wins, array $met): array
    {
        $p = array_fill(0, count($wins), 1.0);
        for ($round = 0; $p && $round < self::MAX_ROUNDS; $round++) {
            $next = [];
            $logs = 0.0;
            foreach ($p as $i => $pi) {
                $d = self::PRIOR / ($pi + 1.0);
                foreach ($met[$i] ?? [] as $j => $count) {
                    $d += $count / ($pi + $p[$j]);
                }
                $next[$i] = ($wins[$i] + self::PRIOR / 2) / $d;
                $logs += log($next[$i]);
            }
            $scale = exp($logs / count($next));
            $change = 0.0;
            foreach ($next as $i => $q) {
                $next[$i] = $q / $scale;
                $change = max($change, abs(log($next[$i] / $p[$i])));
            }
            $p = $next;
            if ($change < self::TOLERANCE) {
                break;
            }
        }
        return $p;
    }

    /**
     * The listed benchmarks apart from the main field (as keys): of those the dense models are
     * measured on ($on: dense model id => those benchmarks), the ones outside the largest set
     * linked by LINK or more dense models measured on both.
     */
    private static function offField(array $on): array
    {
        $both = [];   // benchmark id => benchmark id => the dense models measured on both
        foreach ($on as $bs) {
            foreach ($bs as $x) {
                foreach ($bs as $y) {
                    if ($x !== $y) {
                        $both[$x][$y] = ($both[$x][$y] ?? 0) + 1;
                    }
                }
            }
        }
        $all = array_fill_keys(array_merge([], ...array_values($on)), true);
        ksort($all);   // (a tie keeps the set of the lowest benchmark id)
        $main = [];   // the largest linked set so far
        $seen = [];
        foreach (array_keys($all) as $b) {
            if (isset($seen[$b])) {
                continue;
            }
            $field = [];
            $stack = [$b];
            while ($stack) {
                $x = array_pop($stack);
                if (!isset($field[$x])) {
                    $field[$x] = true;
                    foreach ($both[$x] ?? [] as $y => $count) {
                        if ($count >= self::LINK) {
                            $stack[] = $y;
                        }
                    }
                }
            }
            $seen += $field;
            if (count($field) > count($main)) {
                $main = $field;
            }
        }
        return array_diff_key($all, $main);
    }

    /**
     * The frontier model model $slug is held against, itself excepted: of $provider's frontier
     * models, or of the whole frontier when it has none (or $provider is null): the closest in
     * general performance of those it shares at least two thirds as many measured benchmarks with
     * as the best-covered one (SHARED_FLOOR; none shared: the closest of all); $model null (a model
     * with no standing) the strongest. Null if there is none. $frontier and $model are rows Api::compare() shapes (with 'slug', 'provider',
     * 'general' and 'measured'), the frontier strongest first; ties keep the stronger row.
     * (As frontierPick, assets/js/pages/compare.js)
     */
    public static function pick(array $frontier, string $slug, ?array $model, ?string $provider = null): ?array
    {
        $pool = self::pool($frontier, $slug, $provider);
        if (!$pool || $model === null) {
            return $pool[0] ?? null;
        }
        $mine = array_flip($model['measured']);   // of the best-covered ones
        $shared = array_map(fn ($f) => count(array_intersect_key(array_flip($f['measured']), $mine)), $pool);
        $floor = self::SHARED_FLOOR * max($shared);
        $pool = array_values(array_filter($pool, fn ($f, $i) => $shared[$i] >= $floor, ARRAY_FILTER_USE_BOTH));
        $best = $pool[0];
        foreach ($pool as $f) {
            if (abs($f['general'] - $model['general']) < abs($best['general'] - $model['general'])) {
                $best = $f;
            }
        }
        return $best;
    }

    /**
     * The model just below model $slug in general performance, itself excepted: of $provider's
     * dense models with a standing, or of all of them when it has none (or $provider is null);
     * none below: the closest above; a model with no standing the strongest. Null if there is
     * none. $general is the rows Api::compare() shapes (with 'slug', 'provider' and 'dense'),
     * strongest first. (As belowPick, assets/js/pages/compare.js)
     */
    public static function below(array $general, string $slug, ?string $provider = null): ?array
    {
        $rank = array_flip(array_column($general, 'slug'));
        $pool = self::pool(array_filter($general, fn ($g) => $g['dense']), $slug, $provider);
        if (!$pool || !isset($rank[$slug])) {
            return $pool[0] ?? null;
        }
        foreach ($pool as $g) {
            if ($rank[$g['slug']] > $rank[$slug]) {
                return $g;
            }
        }
        return $pool[count($pool) - 1];
    }

    /** The rows $slug can be held against: $provider's, or all when it has none; never $slug itself. */
    private static function pool(array $rows, string $slug, ?string $provider): array
    {
        $others = array_values(array_filter($rows, fn ($r) => $r['slug'] !== $slug));
        return array_values(array_filter($others, fn ($r) => $r['provider'] === $provider)) ?: $others;
    }
}
