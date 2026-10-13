# Reviewer: a GLM 5.3 Flash judge next to agy

Status: approved — 2026-10-13 (operator: "juge GLM, ok, mais prends GLM Flash, pas de GLM
normal, ça coûte trop cher", typed ~09:35Z in the coordination session, relayed under the
operator delegation `d47ac557` / `f959d3e2`; default choices of this spec accepted the same way)

Motivation: lowers the operator load induced by the review loop (ticket `c5231125`, class
"review loop"). Since red-rail#93 every producer is excluded from the judges, so a pull request
written by a Claude session with a ha codex worker — the brain-v42 lots' usual shape — has agy
as its only judge. On the night of 2026-10-12/13 agy fell back on roughly one deep pass in four,
and a pull request whose only judge falls back gets no verdict (brain-v42#359 stalled until it
was split). Ticket `c5f6de78`.

## Problem

The reviewer's chain is `agy, codex, claude` minus the producers. A Claude + codex change leaves
one provider. When that provider fails, the pass fails closed with "no judge available" — the
right outcome, but a frequent one: the merge queue waits for a human or for agy to come back.

A fourth provider that is neither Claude, nor codex, nor agy restores a second opinion for that
shape. The brain-v42 lots already use GLM 5.3 Flash through opencode as their independent judge;
the cost of the full GLM model is the operator's stated reason to never use it.

## Decisions

1. **A fourth reviewer provider, `glm`, reached through headless-agents' `opencode` adapter.**
   The pinned headless-agents (`headless-agents-v0.2.0`) already ships `OpencodeProvider`:
   inline config, an allow-listed tool wall that removes every built-in tool, an ephemeral HOME
   that dies with the run, the prompt in argv, stdin closed. No new runtime, no new dependency.
   `Provider` becomes `Literal["agy", "codex", "claude", "glm"]`.

2. **GLM 5.3 Flash only, pinned, never the full model.** The model is a constant of the policy,
   `GLM_FLASH = "openrouter/z-ai/glm-5.3-flash"`, used for BOTH tiers (light and deep): the
   operator's cost rule forbids the full model at any tier. The policy refuses, at load time,
   any `glm` model other than this exact string — a host `reviewer.yaml` that names
   `glm-5.3`, `z-ai/glm-5.3`, `glm-5.3-flashx` or any other id fails to load, naming the rule.
   There is no fallback to another GLM model, ever. The exact id is checked by a live run before
   merge (success criterion 5); if openrouter names the model differently, the constant changes,
   never the rule.

3. **A full judge, placed after agy.** The default chain becomes `agy, glm, codex, claude`. For
   a Claude + codex change it is `agy, glm`: light asks one or two judges as today, and the
   arbitration rule of ticket `4cdeaed9` is unchanged — `glm` is a provider like the others, its
   `request_changes` holds, its approve counts. It is not a fallback that only answers when agy
   fails: an independent second voice is the point.

4. **Its producer is recognised, and it never judges its own work.** `PRODUCERS` gains the
   needles `glm`, `z-ai` and `zhipu` (all → `glm`). An `Implemented-by: opencode…/…` trailer also
   excludes `glm`, because the opencode worker of ha runs GLM: an over-exclusion costs a judge,
   an under-exclusion lets a producer judge itself, and red-rail#93 already chose the safe side.

5. **The same isolation as the other judges.** One seat per run (`review-<repo>-<pr>-<sha>-glm`),
   no tool: `OpencodeProvider` removes every built-in tool before the model sees them, and the
   reviewer gives no MCP server, so the judge reads only the prompt. Its credentials are
   opencode's own `~/.local/share/opencode/auth.json`, symlinked into the ephemeral HOME by the
   adapter from the operator's real HOME (`real_home=Path.home()` is passed explicitly: the
   seat's sandboxed `HOME` holds neither the credentials nor opencode's `node_modules`).

6. **Its prompt limit is declared.** opencode takes its prompt in argv (`MAX_PROMPT_BYTES =
   120_000` in the adapter), like agy. `prompt_limits` gains `"glm": 115_000`, so `diff_budget`
   splits a large diff for it exactly as it does for agy.

7. **Its reply is read like the others'.** The adapter writes the model's final text to the
   report log; the reviewer parses it with the existing `parse_verdict` (first JSON object), and
   a missing or unparsable reply is `unparsable`, a provider failure is `provider_fallback`, as
   for agy and codex.

## Non-goals

- No GLM as a fallback for a provider that failed: it is a judge in its own right (decision 3).
- No other OpenRouter or opencode model, and no per-repository model choice.
- No change to the producer-exclusion rule, the rounds, the closure, or the verdict merge.
- No cost accounting: the operator's rule is enforced by the model pin, not by a budget.
- Keeping the seat's log when a judge drops out (ticket `c5f6de78`, diagnosis note) is a
  separate change.

## Success criteria

1. `ReviewPolicy().providers == ("agy", "glm", "codex", "claude")`; `chain_for` of a
   Claude + codex change is `("agy", "glm")`.
2. `policy.model("glm", "light") == policy.model("glm", "deep") == GLM_FLASH`.
3. Loading a policy whose `models` name any other `glm` id fails, naming the rule; a test
   iterates over `glm-5.3`, `z-ai/glm-5.3`, `openrouter/z-ai/glm-5.3`, `glm-5.3-flashx` and
   `opencode-go/glm-5.3`.
4. `producer_providers` maps `glm`, `z-ai`, `zhipu` and an `Implemented-by: opencode…/…` trailer
   to `glm`; `build_spec` for `glm` names its own seat and the opencode credentials; `run_provider`
   runs `OpencodeProvider` with the real HOME.
5. Before merge, one live `rail reviewer once` on a real pull request shows `judges: … glm` with a
   parsed verdict; the model id used is the pinned constant.
6. `make ci` is green and `rail check` passes on this repository.
