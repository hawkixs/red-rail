# Plan: a GLM 5.3 Flash judge in the reviewer

Spec: `docs/specs/2026-10-13-reviewer-glm-flash-judge.md`. Ticket `c5f6de78`. Method: spec.
Every task is test first: the test is written, run, and seen failing for the stated reason before
the code changes. `make ci` is green after each task.

Measured before planning (2026-10-13): `opencode models openrouter` lists
`openrouter/z-ai/glm-5.3-flash` (and, refused by this plan, `openrouter/z-ai/glm-5.3`,
`openrouter/z-ai/glm-5.3-flashx`, `openrouter/z-ai/glm-5.3-prime`, `openrouter/~z-ai/glm-flash-latest`).
The pinned headless-agents v0.2.0 ships `headless_agents.providers.opencode.OpenCodeProvider`
(`real_home=`, `ephemeral_root=`; prompt in argv, `MAX_PROMPT_BYTES = 120_000`).

## Tasks

### Task 1 — the provider and its pinned model (`src/rail/reviewer/policy.py`)

- `Provider = Literal["agy", "codex", "claude", "glm"]`.
- `GLM_FLASH = "openrouter/z-ai/glm-5.3-flash"`, with a comment carrying the operator's cost rule
  and the date.
- `DEFAULT_MODELS["light"]["glm"] = DEFAULT_MODELS["deep"]["glm"] = GLM_FLASH`.
- `providers` default `("glm", "codex", "claude", "agy")` (agy secondary, operator decision 2026-10-13).
- `prompt_limits` default gains `"glm": 115_000` (argv, like agy).
- A `model_validator` on `ReviewPolicy`: for each tier, a `glm` model other than `GLM_FLASH`
  raises `ValueError("the glm judge runs GLM 5.3 Flash only (GLM_FLASH), operator cost rule
  2026-10-13: got …")`. It runs on the merged policy, so a host `reviewer.yaml` override is caught
  by `load_policy`.

Tests (`tests/test_reviewer_core.py`):
- `test_default_policy_is_the_measured_data` updated for the new chain, models and limits.
- `test_the_glm_judge_runs_glm_flash_at_both_tiers`.
- `test_any_other_glm_model_is_refused` parametrized over `glm-5.3`, `z-ai/glm-5.3`,
  `openrouter/z-ai/glm-5.3`, `openrouter/z-ai/glm-5.3-flashx`, `openrouter/z-ai/glm-5.3-prime`,
  `openrouter/~z-ai/glm-flash-latest`, `opencode-go/glm-5.3`, at light and at deep, through
  `load_policy` of a written file.
- `test_chain_for_a_pr_never_includes_the_producer`: Claude + codex → `("glm", "agy")`; all four
  producers → `()`.

Verify: the new tests fail before the change (glm missing from `Provider`, unknown model) and pass after; `uv run pytest -q tests/test_reviewer_core.py` exit code 0.

### Task 2 — its producer (`src/rail/reviewer/policy.py`)

- `PRODUCERS` gains `("glm", "glm")`, `("z-ai", "glm")`, `("zhipu", "glm")`, `("opencode", "glm")`.
  `opencode` matches the `Implemented-by: opencode-go/…` and `opencode/…` tokens.

Tests: `test_an_implemented_by_trailer_names_a_producer_too` gains an `opencode-go/glm-5.3-flash`
case → `{"glm"}`, and a `Co-Authored-By: GLM <…>` case → `{"glm"}`. The existing
`glm/glm-5.3-flash → frozenset()` case (a worker that can never judge) is REPLACED: `glm` is now a
judge, so it maps to `{"glm"}`.

Verify: the Task 2 trailer tests fail before the needles exist and pass after; `uv run pytest -q tests/test_reviewer_core.py` exit code 0.

### Task 3 — its seat and its run (`src/rail/reviewer/judges.py`)

- `CREDENTIALS["glm"] = Credentials(paths=(".local/share/opencode/auth.json",), mode="symlink")`.
- `build_spec` gives `glm` a `CapabilityProfile(credentials=CREDENTIALS["glm"])` (no MCP, no tool)
  and its own seat name ending `-glm`.
- `run_provider`: a `glm` branch runs `OpenCodeProvider(real_home=Path.home()).run(spec)` and
  reads the report log like agy and codex.

Tests (`tests/test_reviewer_judges.py` or the module that tests `build_spec` / `run_provider`):
- `build_spec(..., provider="glm")` → model `GLM_FLASH`, profile credentials are the opencode
  auth file, no MCP.
- `run_provider("glm", spec)` with a stub `OpenCodeProvider` (monkeypatched) asserts it was built
  with `real_home=Path.home()` and that the report log text is returned.

- `JudgeReply` gains `model: str`, set from `spec.model` in `judge()`; the verdict keeps the
  models of the judges that answered, and `service._render` adds the line
  `Models: <provider>=<model>, …` after the judges line.
- A contract test imports `headless_agents.providers.opencode.OpenCodeProvider` from the pinned
  dependency, so a renamed class fails CI instead of every glm run.

Tests, `tests/test_reviewer_service.py`, arbitration history: the four arbitration tests that
start around lines 244, 272, 297 and 323 (ticket `4cdeaed9` and its follow-ups) build their
policy with the OLD chain fixed explicitly, `ReviewPolicy(providers=("agy", "codex", "claude"))`,
so they keep proving what they were written for; each gains a GLM variant on the default chain
(the disputants are then agy and glm, and the dissent of a judge whose deep run fails still holds).
A test drives a glm runner that returns the fallback exit code with an empty report and asserts
the failure is `provider_fallback` and the pass fails closed when glm was the last judge left.

Tests, `tests/test_reviewer_service.py`: the two scenarios that pin the old chain (the light
disagreement arbitrated at deep, around line 238, and the timeout fallback, around line 372)
keep their meaning under the new default chain: with an unknown producer the chain is
`agy, glm, codex`, so the expected judge sequences are rewritten for it, and two cases are added:
a Claude + codex producer set judges with `agy, glm` only, and a change whose producers are all
four providers calls the runner zero times and fails closed. The check text of a passed review
contains `Models: glm=openrouter/z-ai/glm-5.3-flash` when glm answered.

Verify: the two tests fail before the `glm` branch exists (`unknown provider 'glm'`) and pass after; `make ci` exit code 0.

### Task 4 — documentation

- `CLAUDE.md` § Architecture, `reviewer/`: the chain names `glm` (GLM 5.3 Flash via opencode,
  pinned).
- `skills/rail-reviewer` if it lists the providers.

Verify: `make ci` exit code 0 and `uv run rail check` passes; the docs name `glm` and `GLM_FLASH`.

### Task 5 — proof before merge (spec criterion 5)

From the worktree, with the reviewer's token file: `uv run rail reviewer once --repository
hawkixs/brain-v42 --pr <an open Claude + codex PR>` and check that the check run's text names
`glm` among the judges with a parsed verdict and that its `Models:` line reads
`glm=openrouter/z-ai/glm-5.3-flash` exactly. Recorded in the pull request description.

Verify: the check run's text names `glm` among the judges with a parsed verdict (expected: `approve` or `request_changes`, never `unparsable`).

## Risks

- opencode needs its `node_modules` seeded in the real HOME; the adapter refuses otherwise. The
  host already runs opencode for ha, so it is seeded; a refusal is a `provider_fallback`, which
  fails closed.
- openrouter rate limits or an outage make `glm` fall back like agy does; the chain still fails
  closed, never to a producer.
