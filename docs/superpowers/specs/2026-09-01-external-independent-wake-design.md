# Phase 4N.4 External Independent Wake Scheduler Design

## Context

Production publishing liveness is currently healthy, but GitHub Actions scheduled-event delivery has shown material delays across both the primary dispatcher cron and the in-repo Dispatch Watchdog cron. The existing watchdog improves queue isolation, but it still depends on GitHub's scheduler and therefore does not remove the common failure domain.

The existing dispatch path is intentionally safe: `hardening_runner.py dispatch` delegates to the current dispatcher, while Turso `daily_plan` remains the sole authority for which slot is due. Dispatch is expected to claim at most one currently due slot and to no-op when no slot is eligible.

## Goal

Add a truly external wake source that can trigger the existing production dispatch path without creating a second scheduling authority, duplicating publisher logic, or exposing Facebook/Turso runtime secrets outside GitHub Actions.

## Chosen Approach

Use an external cron service to call GitHub's `workflow_dispatch` API for a dedicated workflow in this repository.

The external service only decides **when to wake GitHub Actions**. It does not decide whether a post should be published. The dedicated workflow calls only `python hardening_runner.py "dispatch"`; Turso `daily_plan` and the existing atomic dispatcher continue to decide whether a slot is due and claimable.

This provides an independent scheduling source while keeping execution, application dependencies, Facebook credentials, Turso credentials, and publication logic inside the existing GitHub Actions runtime.

## Architecture

Data flow:

1. External cron fires every 30 minutes on a fixed offset from the existing GitHub cron schedules.
2. External cron sends an authenticated request to GitHub's workflow-dispatch endpoint for the repository default branch.
3. GitHub starts a dedicated `External Dispatch Wake` workflow using `workflow_dispatch` only.
4. The workflow checks out the repository, installs the existing Python dependencies, and runs `python hardening_runner.py "dispatch"`.
5. The dispatcher queries Turso `daily_plan` and either atomically claims at most one currently due slot or returns a safe no-op.
6. If a slot is claimed, the existing publication path performs generation, pre-publish checks, Facebook publication, and ledger updates exactly as it does today.

## Workflow Contract

Create `.github/workflows/external-dispatch-wake.yml` with these constraints:

- Trigger: `workflow_dispatch` only.
- No `schedule:` block.
- No `SCHEDULED_CRON` environment variable, so external wakes are not rejected by GitHub schedule-staleness handling.
- Dedicated concurrency group: `facebook-autobot-external-dispatch-wake`.
- `cancel-in-progress: false`.
- Runtime command must be exactly `python hardening_runner.py "dispatch"`.
- Must not invoke planner, content-category actions, direct Facebook publishing scripts, or a generic externally supplied action.
- Runtime secrets remain GitHub repository/environment secrets already used by dispatch.

The existing `.github/workflows/dispatch-watchdog.yml` stays in place as a secondary GitHub-native fallback. The primary scheduler workflow also remains unchanged unless later evidence justifies removing redundant GitHub schedules.

## External Scheduler Contract

The external scheduler runs every 30 minutes and only performs an authenticated GitHub API call to dispatch `external-dispatch-wake.yml` on `main`.

Recommended offset: `:12` and `:42` each hour. This avoids exact collision with the primary `:07/:37` dispatcher and the GitHub-native watchdog `:22/:52`, while still keeping a wake opportunity close enough to planned slots.

The external scheduler must not store or receive Facebook, Gemini, Telegram, Pexels, affiliate, or Turso credentials. It stores only the minimum GitHub credential needed to trigger this repository workflow.

## Authentication and Least Privilege

Preferred credential is a fine-grained GitHub personal access token scoped only to `ntgiang1235-ux/facebook-autobot-trendmoingay`, with Actions write permission and the minimum metadata/read permissions GitHub requires.

The token is stored only in the external scheduler's encrypted secret store. It must never be committed to the repository, logged in plaintext, echoed by scripts, embedded in workflow YAML, or exposed as a query-string parameter.

If the selected external cron provider supports custom HTTP headers, the request uses:

- `Authorization: Bearer <token>`
- `Accept: application/vnd.github+json`
- `X-GitHub-Api-Version: 2022-11-28`

The request body selects `ref: main` and contains no user-controlled action input.

## Safety Properties

This change must preserve all of the following:

- Turso `daily_plan` is the only scheduling authority for publish slots.
- External cron is a wake source only; it never calculates or chooses a content slot.
- At most one due slot is claimable per dispatcher execution under the existing dispatcher contract.
- No arbitrary catch-up or backfill of old slots is introduced.
- No direct call to Facebook Graph API is made by the external service.
- No duplicate publisher implementation is added.
- Multiple wake sources are safe because non-due dispatches no-op and due-slot claiming remains atomic.
- Delayed external wakes must not be blocked by the GitHub `SCHEDULED_CRON` staleness gate.

## Failure Handling

External cron HTTP failure:
- The external provider records the failed request according to its own delivery history.
- Existing GitHub-native primary/watchdog schedules remain available as fallbacks.
- No publication state is mutated by a failed wake request.

GitHub workflow-dispatch accepted but runner delayed:
- The workflow may start late, but because it has no `SCHEDULED_CRON`, the request reaches the existing dispatcher.
- The dispatcher itself decides whether a currently due slot remains eligible.

No due slot:
- The workflow exits as a normal safe no-op/skipped dispatch outcome.
- This is considered healthy behavior, not a failure.

Overlapping wake sources:
- Each source may invoke dispatch independently.
- The existing atomic claim/due-slot semantics prevent a legitimate slot from being published twice.
- Verification must explicitly inspect publication counts and logs after initial rollout.

Credential compromise:
- Revoke the external GitHub token immediately.
- The token must not grant access to Facebook/Turso secrets.
- GitHub-native schedulers continue to function independently of that token.

## Testing Strategy

Use TDD for the repository-side workflow contract.

Add a focused test file, `tests/test_external_dispatch_wake_workflow.py`, that initially fails because the workflow is absent and then verifies:

- the workflow file exists;
- `workflow_dispatch` is present;
- `schedule:` is absent;
- concurrency group is exactly `facebook-autobot-external-dispatch-wake`;
- `cancel-in-progress` is false;
- command is exactly `python hardening_runner.py "dispatch"`;
- `SCHEDULED_CRON` is absent;
- planner is not invoked;
- no generic action input is accepted;
- all required existing runtime secrets are wired in GitHub Actions.

After implementation, run the repository CI and Python compile checks already used by the project.

## Production Rollout and Verification

Rollout is staged:

1. Merge repository-side workflow and contract tests to `main` after CI passes.
2. Create the external scheduler with the least-privilege token and `:12/:42` cadence.
3. Observe at least one real external HTTP wake and corresponding GitHub Actions run with event `workflow_dispatch`.
4. Inspect the job log to prove it executed `hardening_runner.py dispatch`.
5. Confirm the dispatch result is either one legitimate publish or a safe no-op.
6. Confirm there is no duplicate publish/catch-up flood by checking publication ledger/counts around that wake.
7. Run production readiness read-only and report production liveness separately from unrelated Phase 4 readiness degradations.

## Success Criteria

Phase 4N.4 is runtime-ready only when all are true:

- a real external provider has successfully triggered `external-dispatch-wake.yml` on `main`;
- the run uses event `workflow_dispatch`;
- the job executes the existing dispatch command and completes successfully or no-ops safely;
- no duplicate/catch-up publication occurs;
- production liveness remains READY after verification;
- the external provider holds no Facebook/Turso application secrets.

Repository CI passing alone is not sufficient to claim external scheduler runtime readiness.

## Out of Scope

- Rewriting the dispatcher.
- Moving the Python bot runtime to another hosting platform.
- Creating a Vercel proxy endpoint.
- Changing content generation, strategy learning, publisher behavior, or `daily_plan` generation.
- Removing current GitHub-native schedules in this phase.
- Forcing Phase 4 learning/rollback readiness to green without genuine production evidence.
