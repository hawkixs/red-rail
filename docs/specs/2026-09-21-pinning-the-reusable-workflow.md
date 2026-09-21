# Pinning the reusable CI workflow

Status: proposed — 2026-09-21

## Problem

A scaffolded project calls the rail's continuous integration like this:

```yaml
uses: hawkixs/red-rail/.github/workflows/rail-ci.yml@main
```

So **the gates that guard a repository can change without a commit in that repository**. This
rail's main branch moved eleven times in a single day; every onboarded project's CI changed
eleven times, silently, and none of their histories record it. For a project whose entire
doctrine is that evidence must be verifiable rather than declarative, having the guard defined
elsewhere and mutable is a strange exception.

The pilot that found it pinned its own call to a commit, and the cost of doing so arrived the
same day: two gate fixes it had itself reported no longer reached it. That is the dilemma in
one sentence — **pinning removes the ability to fix a gate everywhere at once, and `@main`
removes a repository's control over when its guards change.**

There is a second, smaller problem in the same file, already fixed: the template handed every
repository secret to the called workflow. It is mentioned here only because it shows the class
— nobody re-reads a generated CI file once it works.

## Decisions

1. **The rendered call pins an explicit ref, and the template never invents it.** A new copier
   answer, `rail_ref`, carries it. The template renders `@{{ rail_ref }}` and nothing else, so
   there is one place where the pin comes from and it is recorded in `.copier-answers.yml`
   alongside every other answer.

2. **`copier`'s own `_commit` is not the pin, because it is not a ref.** Measured: copier
   records `git describe --tags --always` output, which for this repository today is
   `v0.4.0-44-g4c257be`. Git resolves that locally; GitHub Actions does not — it is neither a
   tag nor a branch on the remote. Rendering `@{{ _copier_answers._commit }}` would have
   produced a workflow that cannot resolve on every project scaffolded from a tagged
   red-rail. The pin is a commit SHA, resolved explicitly.

3. **`rail new` resolves the SHA; `rail upgrade` re-resolves it.** The bump therefore lands in
   the diff of an `upgrade`, next to the template drift it already resorbs — one explicit,
   reviewable gesture rather than a silent effect. A project that never upgrades keeps the
   gates it was scaffolded with, which is the property the pilot asked for; a project that
   upgrades takes the current ones, which is the property the fleet needs.

4. **`main` stays the default answer.** A human running `copier copy` directly, and every
   project already scaffolded, keep working unchanged. Pinning is what `rail new` and
   `rail upgrade` do, not what the template imposes.

5. **Resolution is injectable and never silent.** The SHA comes from `git ls-remote` against
   the template source, through a seam the tests replace. If it cannot be resolved, the
   command says so and stops rather than falling back to `main` — a pin that quietly becomes
   a moving branch is worse than no pin, because it reads as pinned.

## Non-goals

- **No change to already-scaffolded projects.** Nothing rewrites another repository's
  workflow. An existing project moves when it runs `rail upgrade`, and not before.
- **No gate on the pin.** Nothing checks that a project's workflow is pinned, or how old its
  pin is. That is the audit's business if it ever becomes one, and adding a gate here would
  repeat the mistake this phase already found: the value is in the artefact, not the check.
- **The reviewer's own truncation limit is out of scope.** Separate defect, separate decision.

## Success criteria

- A project rendered by `rail new` has a workflow calling the reusable one at a 40-character
  commit SHA, and `.copier-answers.yml` records the same value.
- A project rendered by a bare `copier copy` still calls it at `main` and still works.
- `rail upgrade` changes the pin when the template has moved, and the change is visible in
  `git diff` — the bump is a reviewable line, not a side effect.
- An unresolvable template source makes `rail new` fail with the reason, and never renders a
  workflow pinned to `main` while claiming to be pinned.
- `rail check` stays green on this repository and `make ci` passes.
