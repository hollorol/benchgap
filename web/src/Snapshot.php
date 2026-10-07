<?php

declare(strict_types=1);

namespace Benchgap;

use PDO;

/**
 * The site's data document (data/benchgap.json), computed from the
 * benchgap database.
 *
 * It carries everything the browser would otherwise have to re-derive: the
 * rendered equation and a sampled curve per mapping, the dense-core display
 * flags, the display labels, and a confidence level for every estimate, so
 * the front-end only renders.
 *
 * Confidence of a gapfilled score:
 * - base level from the mapping's leave-one-out RMSE: <= HIGH_MAX_PP is high,
 *   <= MEDIUM_MAX_PP is medium, above is low;
 * - each of these demotes it one level (low is the floor): extrapolated
 *   (input outside the training range), fitted on fewer than MIN_RELIABLE_N
 *   paired models, R² below MIN_INFORMATIVE_R2.
 * The reasons are exported too, so the site can say why an estimate has low
 * confidence instead of only that it does.
 */
final class Snapshot
{
    // The method's own settings, shown on the site; tests/test_web.py checks they
    // match src/benchgap (fit.py quality gate, report.py display filter and the
    // capability order, which is the order of CAPABILITY_LABELS).
    public const QUALITY_GATE = ['min_pairs' => 5, 'min_r2' => 0.3, 'max_loo_pp' => 15.0];
    public const DENSE = ['min_models' => 8, 'min_benchmarks' => 3];

    private const RELIABILITY = [
        'high_max_pp' => 5.0, 'medium_max_pp' => 10.0, 'min_reliable_n' => 8, 'min_informative_r2' => 0.5,
    ];
    private const TIERS = ['high', 'medium', 'low'];

    /** Display names (benchmark/version -> label); others show as "name version". */
    private const DISPLAY_NAMES = [
        'terminal-bench/2.1' => 'Terminal-Bench 2.1',
        'terminal-bench/4.0' => 'Terminal-Bench 4.0',
        'terminal-bench-hard/1.0' => 'Terminal-Bench Hard',
        'terminal-bench-science/0.1' => 'Terminal-Bench Science 0.1',
        'gpqa/diamond' => 'GPQA Diamond',
        'mmlu-pro/1.0' => 'MMLU-Pro',
        'global-mmlu-lite/1.0' => 'Global-MMLU-Lite',
        'hle/1.0' => "Humanity's Last Exam",
        'critpt/1.0' => 'CritPt',
        'math-500/1.0' => 'MATH-500',
        'aime-2025/2025' => 'AIME 2025',
        'livecodebench/1.0' => 'LiveCodeBench',
        'scicode/1.0' => 'SciCode',
        'tau-bank/3-banking' => 'τ³-Bench Banking',
        'tau-bench/2-telecom' => 'τ²-Bench Telecom',
        'automationbench/1.0' => 'AutomationBench',
        'apex-agents/1.0' => 'APEX-Agents',
        'analyst-agent/1.0' => 'AA-AnalystAgent',
        'enterprise-ops-gym/1.0' => 'EnterpriseOps-Gym',
        'harvey-lab/1.0' => 'Harvey LAB',
        'itbench/sre' => 'ITBench SRE',
        'ifbench/1.0' => 'IFBench',
        'mmmu-pro/1.0' => 'MMMU-Pro',
        'gdp-pdf/1.0' => 'GDP.pdf',
    ];

    public const CAPABILITY_LABELS = [
        'agentic-terminal' => 'Agentic · terminal',
        'agentic-tool' => 'Agentic · tools',
        'coding' => 'Coding',
        'math' => 'Math',
        'knowledge' => 'Knowledge & reasoning',
        'instruction-following' => 'Instruction following',
        'vision' => 'Vision & documents',
        'long-context' => 'Long context',
        'general' => 'General',
    ];

    /** Model name prefix => [provider id, label]; first match wins, the rest is "other". */
    private const PROVIDERS = [
        '/^Claude/' => ['anthropic', 'Anthropic'],
        '/^(GPT|o\d)/' => ['openai', 'OpenAI'],
        '/^Gemini/' => ['google', 'Google'],
        '/^Grok/' => ['xai', 'xAI'],
        '/^DeepSeek/' => ['deepseek', 'DeepSeek'],
        '/^GLM/' => ['zhipu', 'Zhipu (GLM)'],
        '/^Qwen/' => ['alibaba', 'Alibaba (Qwen)'],
        '/^Kimi/' => ['moonshot', 'Moonshot'],
        '/^MiniMax/' => ['minimax', 'MiniMax'],
        '/^Mistral/' => ['mistral', 'Mistral'],
        '/^Nemotron/' => ['nvidia', 'NVIDIA'],
        '/^Muse/' => ['meta', 'Meta'],
        '/^MiMo/' => ['xiaomi', 'Xiaomi'],
        '/^Step/' => ['stepfun', 'StepFun'],
    ];
    private const OTHER_PROVIDER = ['other', 'Other'];

    private const METHOD_LABELS = [
        'linear' => 'linear',
        'mm' => 'Michaelis–Menten',
        'mm_offset' => 'Michaelis–Menten + offset',
        'mm_offset_inv' => 'inverse Michaelis–Menten',
        'hill' => 'Hill',
        'logistic' => 'offset logistic',
        'linear_mv' => 'ridge regression (multivariate)',
        'mm_mv' => 'multivariate Michaelis–Menten',
    ];

    /** The data document for the database behind $db. */
    public static function build(PDO $db): array
    {
        $versions = $db->query(
            'SELECT v.id, v.version, v.harness, v.source_url, b.name AS benchmark, b.capability'
            . ' FROM benchmark_versions v JOIN benchmarks b ON b.id = v.benchmark_id'
        )->fetchAll();
        $rank = array_flip(array_keys(self::CAPABILITY_LABELS));
        usort($versions, fn ($a, $b) => [$rank[$a['capability']] ?? 99, $a['benchmark'], $a['version']]
            <=> [$rank[$b['capability']] ?? 99, $b['benchmark'], $b['version']]);

        $measured = $db->query("SELECT model_id, version_id FROM scores WHERE source = 'measured'")->fetchAll();
        [$denseVersions, $denseModels] = self::denseCore($measured);

        $benchmarks = [];
        foreach ($versions as $r) {
            $key = "{$r['benchmark']}/{$r['version']}";
            $benchmarks[] = [
                'id' => (int) $r['id'],
                'key' => $key,
                'label' => self::DISPLAY_NAMES[$key] ?? "{$r['benchmark']} {$r['version']}",
                'capability' => $r['capability'],
                'harness' => $r['harness'],
                'source_url' => $r['source_url'],
                'dense' => isset($denseVersions[$r['id']]),
            ];
        }
        $capabilities = [];
        foreach (array_unique(array_column($benchmarks, 'capability')) as $c) {
            $capabilities[] = ['id' => $c, 'label' => self::CAPABILITY_LABELS[$c] ?? $c];
        }

        // only the selected (best) mapping per benchmark pair is shown and used by gapfill
        $mappings = [];
        foreach ($db->query('SELECT * FROM mappings ORDER BY id') as $m) {
            $m['metrics'] = json_decode($m['metrics_json'], true);
            $pair = "{$m['from_version_id']}:{$m['to_version_id']}";
            if (!isset($mappings[$pair]) || self::fitError($m) < self::fitError($mappings[$pair])) {
                $mappings[$pair] = $m;
            }
        }
        $mappings = array_column($mappings, null, 'id');
        ksort($mappings);
        $multi = [];
        foreach ($db->query('SELECT * FROM multi_mappings ORDER BY id') as $m) {
            $m['metrics'] = json_decode($m['metrics_json'], true);
            $multi[$m['id']] = $m;
        }
        $points = [];
        foreach ($db->query('SELECT mapping_id, model_id, x, y FROM mapping_points ORDER BY mapping_id, model_id') as $p) {
            $points[$p['mapping_id']][] = [(int) $p['model_id'], self::num($p['x']), self::num($p['y'])];
        }

        $scores = [];
        $used = [];
        $tiers = array_fill_keys(self::TIERS, 0);
        foreach ($db->query('SELECT * FROM scores ORDER BY version_id, model_id, source') as $s) {
            $row = ['m' => (int) $s['model_id'], 'b' => (int) $s['version_id'], 'v' => self::num($s['value'])];
            if ($s['source'] === 'measured') {
                $scores[] = $row + ['s' => 'm'];
                continue;
            }
            $pred = $s['prediction_json'] ? json_decode($s['prediction_json'], true) : [];
            if (isset($s['multi_mapping_id'], $multi[$s['multi_mapping_id']])) {
                $mp = $multi[$s['multi_mapping_id']];
                $from = [];
                foreach ($pred['input_scores'] ?? [] as $b => $x) {
                    $from[] = ['b' => (int) $b, 'v' => self::num($x)];
                }
                $via = ['kind' => 'multi', 'mapping' => (int) $mp['id'], 'n' => (int) $mp['n_points'], 'from' => $from];
            } elseif (isset($s['mapping_id'], $mappings[$s['mapping_id']])) {
                $mp = $mappings[$s['mapping_id']];
                $used[$mp['id']] = ($used[$mp['id']] ?? 0) + 1;
                $via = ['kind' => 'uni', 'mapping' => (int) $mp['id'], 'n' => (int) $mp['n_points'],
                    'from' => [['b' => (int) $mp['from_version_id'], 'v' => self::num($pred['input_score'] ?? null)]]];
            } else {
                continue;  // orphaned estimate (its mapping was refitted away)
            }
            $extrapolated = (bool) ($pred['extrapolated'] ?? false);
            $loo = $mp['metrics']['LOO_RMSE'] ?? null;
            [$tier, $why] = self::reliability($loo, $mp['metrics']['R2'] ?? null, (int) $mp['n_points'], $extrapolated);
            $tiers[$tier]++;
            $scores[] = $row + [
                's' => 'e',
                'sd' => self::num($loo),
                'tier' => $tier,
                'why' => $why,
                'x' => $extrapolated,
                'method' => $mp['method'],
                'via' => $via,
            ];
        }

        // counts from the exported scores, so they always match what the site shows
        $count = [];
        foreach ($scores as $sc) {
            $count['b'][$sc['b']][$sc['s']] = ($count['b'][$sc['b']][$sc['s']] ?? 0) + 1;
            $count['m'][$sc['m']][$sc['s']] = ($count['m'][$sc['m']][$sc['s']] ?? 0) + 1;
        }
        foreach ($benchmarks as &$b) {
            $b['n_measured'] = $count['b'][$b['id']]['m'] ?? 0;
            $b['n_estimated'] = $count['b'][$b['id']]['e'] ?? 0;
        }
        unset($b);

        $models = [];
        foreach ($db->query('SELECT id, slug, name FROM models ORDER BY name') as $m) {
            $name = $m['name'] ?? $m['slug'];
            $models[] = [
                'id' => (int) $m['id'],
                'slug' => $m['slug'],
                'name' => $name,
                'provider' => self::provider($name),
                'dense' => isset($denseModels[$m['id']]),
                'n_measured' => $count['m'][$m['id']]['m'] ?? 0,
                'n_estimated' => $count['m'][$m['id']]['e'] ?? 0,
            ];
        }

        $mappingDocs = [];
        foreach ($mappings as $m) {
            $params = json_decode($m['params_json'], true);
            $range = $m['train_range_json'] ? json_decode($m['train_range_json'], true) : null;
            $xRange = $range ? [self::num($range['x_min']), self::num($range['x_max'])] : null;
            $mappingDocs[] = [
                'id' => (int) $m['id'],
                'from' => (int) $m['from_version_id'],
                'to' => (int) $m['to_version_id'],
                'method' => $m['method'],
                'equation' => Curves::equation($m['method'], $params),
                'curve' => Curves::sample($m['method'], $params, $xRange),
                'n' => (int) $m['n_points'],
                'r2' => self::num($m['metrics']['R2'] ?? null),
                'rmse' => self::num($m['metrics']['RMSE'] ?? null),
                'loo' => self::num($m['metrics']['LOO_RMSE'] ?? null),
                'range' => $xRange,
                'points' => $points[$m['id']] ?? [],
                'n_used' => $used[$m['id']] ?? 0,
            ];
        }

        $harnesses = array_values(array_unique(array_column($benchmarks, 'harness')));
        sort($harnesses);
        $estimated = array_sum($tiers);
        return [
            'meta' => [
                'generated_at' => self::generatedAt($db),
                'retrieved_at' => $db->query("SELECT MAX(retrieved_at) FROM scores WHERE source = 'measured'")->fetchColumn(),
                'harnesses' => $harnesses,
                'counts' => [
                    'models' => count($models),
                    'benchmarks' => count($benchmarks),
                    'measured' => count($scores) - $estimated,
                    'estimated' => $estimated,
                    'mappings' => count($mappingDocs),
                    'confidence' => $tiers,
                ],
                'quality_gate' => self::QUALITY_GATE + ['n_candidates' => count(Curves::EQUATIONS)],
                'confidence_levels' => self::RELIABILITY,
                'dense' => self::DENSE,
                'methods' => self::METHOD_LABELS,
                'providers' => array_column([...array_values(self::PROVIDERS), self::OTHER_PROVIDER], 1, 0),
            ],
            'capabilities' => $capabilities,
            'benchmarks' => $benchmarks,
            'models' => $models,
            'scores' => $scores,
            'mappings' => $mappingDocs,
        ];
    }

    /** Confidence level of a gapfilled score and the reasons for it. */
    public static function reliability(?float $looRmse, ?float $r2, int $n, bool $extrapolated): array
    {
        $why = [];
        if ($looRmse === null) {
            $level = 2;
            $why[] = 'no cross-validated error available';
        } else {
            $pp = $looRmse * 100;
            $level = $pp <= self::RELIABILITY['high_max_pp'] ? 0 : ($pp <= self::RELIABILITY['medium_max_pp'] ? 1 : 2);
            if ($level === 1) {
                $why[] = sprintf('cross-validated error ±%.1f pp', $pp);
            } elseif ($level === 2) {
                $why[] = sprintf('large cross-validated error ±%.1f pp', $pp);
            }
        }
        if ($extrapolated) {
            $level++;
            $why[] = 'extrapolated beyond the scores the mapping was fitted on';
        }
        if ($n < self::RELIABILITY['min_reliable_n']) {
            $level++;
            $why[] = "fitted on only $n paired models";
        }
        if ($r2 !== null && $r2 < self::RELIABILITY['min_informative_r2']) {
            $level++;
            $why[] = sprintf('mapping explains only %.0f%% of the variance (R²=%.2f)', $r2 * 100, $r2);
        }
        return [self::TIERS[min($level, 2)], $why];
    }

    /** Provider id of a model, from its name. */
    public static function provider(string $name): string
    {
        foreach (self::PROVIDERS as $pattern => [$id]) {
            if (preg_match($pattern, $name)) {
                return $id;
            }
        }
        return self::OTHER_PROVIDER[0];
    }

    /**
     * Benchmark version ids and model ids of the dense core shown by default:
     * repeatedly drop versions with fewer than DENSE['min_models'] measured
     * models and models with fewer than DENSE['min_benchmarks'] measured
     * versions until both hold. Display only; the data keeps everything.
     */
    private static function denseCore(array $measured): array
    {
        $versions = array_fill_keys(array_column($measured, 'version_id'), true);
        $models = array_fill_keys(array_column($measured, 'model_id'), true);
        do {
            $perVersion = $perModel = [];
            foreach ($measured as ['model_id' => $m, 'version_id' => $v]) {
                if (isset($versions[$v], $models[$m])) {
                    $perVersion[$v] = ($perVersion[$v] ?? 0) + 1;
                    $perModel[$m] = ($perModel[$m] ?? 0) + 1;
                }
            }
            $dropV = array_filter(array_keys($versions), fn ($v) => ($perVersion[$v] ?? 0) < self::DENSE['min_models']);
            $dropM = array_filter(array_keys($models), fn ($m) => ($perModel[$m] ?? 0) < self::DENSE['min_benchmarks']);
            $versions = array_diff_key($versions, array_flip($dropV));
            $models = array_diff_key($models, array_flip($dropM));
        } while ($dropV || $dropM);
        return [$versions, $models];
    }

    /** Sort key of a candidate mapping: lowest LOO error, then lowest RMSE. */
    private static function fitError(array $m): array
    {
        return [$m['metrics']['LOO_RMSE'] ?? 1e9, $m['metrics']['RMSE'] ?? 1e9];
    }

    /** When the data was built: the newest row in the database, as UTC ISO 8601. */
    private static function generatedAt(PDO $db): ?string
    {
        $latest = max(array_map(
            fn ($table) => $db->query("SELECT MAX(created_at) FROM $table")->fetchColumn(),
            ['scores', 'mappings', 'multi_mappings']
        ));
        return $latest ? str_replace(' ', 'T', $latest) . 'Z' : null;
    }

    /** A float rounded for a compact payload (null stays null). */
    private static function num(mixed $x, int $digits = 6): ?float
    {
        return $x === null ? null : round((float) $x, $digits);
    }
}
