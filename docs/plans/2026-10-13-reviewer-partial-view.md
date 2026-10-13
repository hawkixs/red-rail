# Plan: a judge that did not see a file cannot move that file's findings

Spec: `docs/specs/2026-10-13-reviewer-partial-view.md`. Ticket `e356b432`. Method: spec.
Every task is test first: the test is written, run, and seen failing for the stated reason before
the code changes. `make ci` is green after each task.

Read before planning (main `61b6309`): `split.py` (`split_diff`, `oversized`, `_files`-style
header parsing in `_name`), `judges.py` (`build_prompt` cuts `prioritise_diff(diff)` at
`max_diff_chars`; `judge()` shrinks again under `prompt_limits`; `JudgeReply` is a frozen
dataclass), `service.py` (`_within_part`, `_without_blind_answers`, `_head_excerpts` returning
`blind`, `_merge`, the chunked and single-piece paths near line 1060, the check text near line
535), `rounds.py` (`assign`: `fixed` is sticky unless the id is repeated), `verdict.py`
(`ReviewVerdict`, `extra="forbid"`; its attestation data near line 112).

## Tasks

### Task 1 — what a judge was shown (`src/rail/reviewer/split.py`)

Add a frozen `Sight` (dataclass): `complete: frozenset[str]` (paths whose whole patch is in the
text) and `cut: Mapping[str, tuple[tuple[int, int], ...]]` (for a path whose patch was cut, the
inclusive new-side line ranges of the hunks that are fully visible). Add a pure
`sight_of(visible: str, whole: str) -> Sight`. Here `visible` is the text the judge received
between BEGIN and END DIFF, without the reviewer's own "[diff truncated by the reviewer]"
marker, and `whole` is the piece it was cut from. A file is complete when its patch in
`visible` equals its patch in `whole`. A file is cut when its header is visible but its patch is
not whole; then each `@@ -a,b +c,d @@` hunk whose body is entirely visible contributes `(c,
c+d-1)` (a hunk with `d == 0` contributes nothing). `Sight` keys are header paths EXACTLY as
`headers.HEADER` reads them (never stripped). A renamed file is keyed under BOTH its old and its
new path, from the `a` and `b` groups, never through `split._name`, which returns the old path
only. Add `headers.resolve(spelling: str, paths: Collection[str]) -> str | None | Ambiguous`.
Its candidates are the spelling and the spelling with one leading `./`, `a/` or `b/` removed. It
returns the single candidate found in `paths`, `None` when none is, or `AMBIGUOUS` when more
than one is (spec D2). Headers that cannot be parsed give an empty `Sight` (spec D6).

Tests (`tests/test_reviewer_split.py`): a whole piece is all
complete; a piece cut inside its second file has the first complete and the second cut with only
its fully visible hunks; a hunk cut midway is excluded; a pure-deletion hunk adds no range; a
rename is keyed by both paths; a directory literally named `b` (`b/src/x.py`) stays distinct
from `src/x.py`; `resolve` returns the single match, `None`, or `AMBIGUOUS` when both
`src/x.py` and `b/src/x.py` are in the diff and the spelling is `b/src/x.py`; garbage gives an
empty `Sight`.

Verify: the new tests fail before the change (no `Sight`, no `sight_of`) and pass after; `uv run pytest -q tests/test_reviewer_split.py` exit code 0.

### Task 2 — the reply carries its sight (`src/rail/reviewer/judges.py`)

`build_prompt` also returns the visible diff body (after `prioritise_diff` and the cut; keep
the `truncated` flag). `judge()` keeps the body of the prompt it finally sends (after the argv
shrink loop) and sets a new `JudgeReply.sight: Sight | None = None` from
`sight_of(visible, diff)`. A reply built elsewhere (tests, mechanical paths) keeps `None`,
meaning "saw the whole input it was given". Do not change the prompt text.

Tests: `judge()` with a fake runner on a diff larger than `max_diff_chars` yields a sight whose
last file is cut; with a `prompt_limits` entry forcing the shrink, the sight reflects the shrunk
body; a small diff yields every file complete.

Verify: the new tests fail before the change (`JudgeReply` has no `sight`) and pass after; `uv run pytest -q tests/test_reviewer_core.py` exit code 0.

### Task 3 — inadmissible words are dropped (`src/rail/reviewer/service.py`)

Add a pure `_admissible(reply, judged_files, known) -> tuple[JudgeReply, set[str]]`. Here
`judged_files` holds the paths of the diff being judged in this pass (the delta when
incremental, else the whole PR), and `known` maps earlier finding ids to `(file, line)` from the
loop state's judged findings. The second value is the set of ids whose word was dropped. For
each earlier id the reply speaks about, in a `previous` answer or as a repeated `id` in
`findings`:
- if any header of the judged diff is unreadable (`headers.parsed` is false), drop the word
  (membership unknown, spec D6);
- else resolve the finding's path against `judged_files` (`headers.resolve`). `AMBIGUOUS`:
  drop the word. `None` (outside the judged diff): keep the word only when THIS finding's id is
  among the ids whose own window `_head_excerpts` quoted (it returns that id set beside
  `blind`); otherwise drop it, for a `previous` answer and a repeated id alike. A `fixed` id is
  never in that set, even if another finding's window covers its file;
- a resolved path is then checked against the reply's `Sight` with that exact path:
  - if `reply.sight` is None, keep it (the reply saw its whole input; in the chunked path
  `_within_part` builds the sight from the piece, see below);
  - else if the path is in `sight.complete`, keep it;
  - else if the path is in `sight.cut` and the line is not None and falls inside one of its
    ranges, keep it;
  - otherwise drop it. For a `previous` answer, remove the answer. For a repeated id, remove the
  finding from `findings`; do not renumber it as new.
After dropping, a reply left with no blocking finding approves, as `_within_part` already does.

Apply it to every reply in BOTH paths before `_merge`. In the chunked path, a reply whose
`sight` is None gets the sight of its piece, `sight_of(chunk, chunk)`, so a part lacking the
file drops its words. This replaces the part of `_within_part` that only filtered `blind`
answers, and supersedes `_without_blind_answers` (whose rule is now the excerpt condition
above). Demotion (spec D5), in both paths: a NEW finding whose location is not seen in its
reply's `Sight` (file outside, line outside the visible ranges of a cut file, or no line on a
cut file) gets severity `important` AND class `note`, so `rounds.enforce_class` cannot keep a
proposed `blocker` on `spec_plan`. Exception (spec D6): a reply whose piece has an unreadable
header keeps its new findings untouched, as today; only its words on earlier findings drop.

Collect, across replies, `unseen_files` (paths of `judged_files` that no reply saw complete) and
`dropped` (the union of the second values). An id that has at least one dropped word and no kept
word from any reply becomes `not_reviewed`.

Tests (`tests/test_reviewer_service.py`, following its fake-judge pattern): a part without
the file repeats a fixed id, and the finding stays `fixed`; a part without the file answers
`still_open` on an open finding while the part with the file answers `fixed`, and the finding
becomes `fixed`; a cut piece answers on a line past its last visible hunk, the answer is dropped
and the id is `not_reviewed`; a finding on a file outside the judged diff still follows the
excerpt rule, and a repeated `fixed` id outside the judged diff (no excerpt) stays fixed; a
stored finding spelled `b/src/x.py` resolves to `src/x.py` when only that is in the diff, and is
dropped as ambiguous when both `src/x.py` and `b/src/x.py` are, with no status change; a fixed
finding at line 1500 sharing a file with an open finding at line 50 (whose excerpt shows lines
20-80), on a file outside the delta, stays fixed when a judge repeats its id; an unreadable piece
keeps its new blocking finding and `_finish` still blocks, on both artifacts; a rename
seen under its new path governs a finding stored under its old path; an unreadable header in the
judged diff drops every word and changes no status; a demoted blocker does not block through
`_finish`, on `code` and on `spec_plan`; the single-piece path with a cut file applies the same
rule. Coverage of the replies that actually reach `assign`: light replies replaced by a deep
arbitration, a dissenter kept after a failed deep call, a closure with a fix ruling, and the
separately built `no_verdict` verdict (it carries `unseen_files` too). Rewrite
`test_a_judge_that_read_the_excerpt_keeps_its_still_open` to the new rule (spec D2): the
`b.go` piece's `fixed` now decides.

Verify: the new tests fail before the change (a fixed finding reopens, a blind still_open outvotes) and pass after; `uv run pytest -q tests/test_reviewer_service.py tests/test_reviewer_loop.py` exit code 0.

### Task 4 — the verdict names what was not read (`verdict.py`, `service.py`)

`ReviewVerdict` gains `unseen_files: tuple[str, ...] = ()`, `unseen_count: int = 0` and
`not_reviewed: tuple[str, ...] = ()` (defaults keep every existing construction valid). The attestation data gains both keys
(old receipts lack them; every reader uses `.get`). The check text drops "the verdict covers the
first part only". When `unseen_files` is non-empty it writes "Not read whole: " followed by at
most 10 paths, then "and N more". It then writes one line per `not_reviewed` id: "F-x: not
re-verified (file not seen), keeps <status>". `diff_truncated` is unchanged.

Tests: rendering with 0, 3 and 12 unseen files; a `not_reviewed` line carries the finding's kept
status; the attestation data carries both keys; a receipt without them still loads into
`rounds.loop_state`; with 500 unseen paths the receipt holds 50 and `unseen_count: 500`, and the
whole payload stays under the attestation limit (the budget is checked on the whole data, not the
findings alone).

Documentation, in this task: `CLAUDE.md` § Architecture, `reviewer/service.py` line, says a word
on an earlier finding counts only from a judge that saw its location (spec
2026-10-13-reviewer-partial-view). `tests/test_gates_docs.py`'s existing checks cover the file.

Verify: the new tests fail before the change (no `unseen_files`, "first part only" still rendered) and pass after; `uv run pytest -q tests/test_reviewer_verdict.py tests/test_reviewer_service.py tests/test_reviewer_rounds.py` exit code 0.

### Task 5 — the brain-v42#376 regression (spec criterion 1)

A fixture built in the test module rebuilds the shape of the real passes. The real diff is not
vendored: it is large and belongs to another repository. A whole-PR pass (no delta), split into
three pieces:
- piece A: `alembic/versions/065_programmes.py`, complete;
- piece B: an unrelated small file only;
- piece C: `src/brain_v42/db/tables.py`, oversized. `split_diff` keeps an oversized piece whole;
  the cut happens in `judge()`. C's reply is therefore produced through the real `judge()` with
  a fake runner and a policy whose `max_diff_chars` cuts `tables.py`. The test first asserts
  that C's `sight` has `tables.py` in `cut`, not in `complete`.
Loop state: F-376-1 (migration, line 109) and F-376-2 (`tables.py`, no line) are both `fixed`.
Fake judges: A approves with no word on either finding; B repeats `F-376-1` with its id, as
blocking; C repeats `F-376-2` with its id. Two separate tests, each failing on main by itself:
F-376-1 stays `fixed`, and F-376-2 stays `fixed`; both are listed in `not_reviewed`. A third
test: when A repeats `F-376-1` (it saw the migration whole), the finding reopens, so a real
regression still counts.

Verify: the first two tests fail on main `61b6309` (each finding reopens) and pass after; the
third passes on both; `uv run pytest -q tests/test_reviewer_service.py -k 376` exit code 0.

### Task 6 — proof before merge

`make ci` green. Then on the host, after merge and reinstall, the next whole-PR pass in pieces
on a real pull request shows "Not read whole:" in its check when a file was cut, and no
"first part only".

Verify: `make ci` exit code 0 and its summary line read; after reinstall, the check text of the next cut pass contains "Not read whole:" and not "first part only".

## Risks

- A judge that legitimately sees a regression only through its head excerpt can no longer
  reopen a fixed finding on a file in the judged diff that its piece lacks. Accepted (spec D2):
  the piece that holds the file decides, and `rail reviewer rule` remains the ruling path.
- Hunk ranges are new-side lines. A finding whose line points at an unchanged line between two
  visible hunks of a cut file is treated as unseen, which is conservative and fail closed.
- `JudgeReply` gains a field with a default, and `ReviewVerdict` gains two fields with defaults.
  No receipt schema version bump is needed, because readers ignore unknown keys and default the
  missing ones.
