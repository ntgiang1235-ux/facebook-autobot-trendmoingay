# Phase 4I Adaptive Creative Strategy Design

## Goal
Close the creative-learning loop by making adaptive publishes actually choose, use, and record hook, writing-style, and CTA variants that the 14-day learner can score later.

## Approved constraints
- GitHub Actions + Turso only; no VPS, model training, vector database, or self-modifying source.
- Keep approximately 80% exploit / 20% explore using `adaptive_config.exploration_rate`.
- Selection must be deterministic for the same dispatcher run/slot so retries cannot silently change the creative treatment.
- Use only variants registered in `style_registry`; retired variants are never selectable.
- When performance data is insufficient, fall back safely to registered baseline variants rather than failing publication.
- Creative instructions may change presentation, not source facts. They must explicitly forbid inventing facts to satisfy a hook.
- Manual publishes remain outside adaptive learning and keep unknown/default creative metadata unless explicitly supplied.

## Architecture
1. `app/creative_strategy.py` maps registry dimensions `hook/tone/cta` to strategy-stat dimensions `hook_type/style_type/cta_type`.
2. The dispatcher selects one `CreativeProfile` after claiming a plan slot and before entering publication context. A stable SHA-256 seed from run key, category, strategy version and dimension drives selection.
3. `PublicationContext` carries the selected hook/style/CTA values for the whole business job.
4. Generators append a bounded creative instruction suffix derived from the current context. The suffix is advisory about presentation but mandatory about factual integrity and the chosen CTA.
5. `publication_ledger` persists the actual context values in the canonical `content_posts` row after confirmed Facebook publication.
6. The existing Phase 4H learner later scores those values from 24h/72h metrics; no additional storage is introduced.

## Safety and fallback behavior
- Missing registry rows are seeded from the approved baseline registry.
- Missing/insufficient strategy stats use deterministic baseline selection.
- A selector failure must fail before content publication; it must never result in an untracked adaptive post.
- Prompt integration never injects raw database/user text; only fixed registered identifiers are mapped to fixed instruction text.
- Unknown registry identifiers fall back to neutral instructions rather than being interpolated into prompts.

## Scope
Adaptive production actions: `post`, `finance`, `philosophy`, `fun`, `recipe`, `video`.
Operational jobs (`reply`, `summary`, `health`, reports, metrics, planner, learn) are unchanged.
