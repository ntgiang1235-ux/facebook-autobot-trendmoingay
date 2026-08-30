# Phase 4K2B — Style Experiment Lifecycle Design

Date: 2026-08-31
Status: Approved continuation of Phase 4 Adaptive Content Intelligence
Branch: `codex/phase-4k2b-style-experiment-lifecycle`

## Goal

Turn a controlled style experiment from a temporary `explore` registry entry into a deterministic lifecycle that can keep exploring, promote a proven winner, or retire a clearly weak variant using real Facebook performance data.

## Core decisions

- Lifecycle decisions are deterministic Python; Gemini never promotes or retires a style.
- Experiment exposure is identified by `content_posts.style_experiment_key`, not by the post-quality classifier's observed `hook_type` / `style_type` / `cta_type` label.
- Only canonical `final` metrics count toward the mature sample threshold.
- Minimum evidence for a lifecycle decision is 5 mature experiment posts and 5 mature parent/control posts inside the existing 14-day learning window.
- Parent/control observations must have no `style_experiment_key` and must match the experiment's parent value on the corresponding observed dimension.
- Promotion requires the experiment's recency-weighted 14-day score to be at least 5% above the parent, its 7-day score to be no worse than 5% below the parent, and its success rate to be at least the parent's success rate.
- Retirement requires the experiment to be at least 20% worse than the parent on the weighted 14-day score and at least 10% worse on the 7-day score.
- All other mature-but-inconclusive results remain `explore`.
- Missing/insufficient parent evidence never causes promotion or retirement.
- At most one `explore` experiment remains active at a time.

## Promotion must affect exploitation

A registry status change alone is insufficient because production exploitation reads `strategy_stats`. Therefore learning must project experiment exposures into the corresponding strategy dimension using the experiment token as the value:

- `hook:<variant>` -> `hook_type=<variant>` strategy statistic
- `tone:<variant>` -> `style_type=<variant>` strategy statistic
- `cta:<variant>` -> `cta_type=<variant>` strategy statistic

The normal observed classifier label remains available separately and continues to contribute to its own strategy statistic. The projected experiment statistic is an intentional treatment-effect statistic, not a replacement for observed classification.

A promoted registry value becomes eligible for exploitation only when its projected strategy stat is mature (`>=5` final samples), `active`, and has positive weight. Retired values must not be selected.

## Weekly orchestration

The existing hardened `style_evolve` action remains the single weekly entry point.

1. Evaluate any pending experiment first.
2. If evidence is insufficient or the result is inconclusive, keep it in `explore` and finish as a normal skip; do not generate another experiment.
3. If the experiment is promoted or retired, persist the registry transition.
4. After a decisive transition, the same weekly run may generate one new bounded experiment, preserving the invariant that no more than one `explore` experiment exists.
5. Dependency/database failures must preserve the hardening contract and fail the workflow rather than false-green.

## Safety

- No schema-destructive migration.
- No new external service or dependency.
- Existing 80/20 exploit/explore behavior remains unchanged.
- Existing quality gate remains mandatory for every restyled candidate.
- Lifecycle calculations reuse the approved 14-day recency weighting and canonical mature/final metric semantics.
