# Reviewer: a judge that did not see a file cannot move that file's findings

Status: draft — 2026-10-13. Method `spec`, option (b) first, chosen by the coordination brain_57
under the operator delegation `d47ac557` / `c5773695` (tooling of the review: `f38ce645`).

Motivation: lowers the operator load induced by the review loop (ticket `c5231125`, class
"review loop"). On brain-v42#376 one defect, already fixed, needed two operator rulings because
the same twin findings flipped between `fixed` and `still_open` from round to round. Ticket
`e356b432`.

## Problem

A change larger than one judge's window is read in pieces (`split_diff`), cut only between
files; a single file larger than the budget gets a piece of its own and is cut inside
(`oversized`, then `build_prompt` at `max_diff_chars`, then `judge()` again when a provider
takes its prompt in argv). Each judge answers the same review context, which lists every
earlier finding.

Measured on brain-v42#376 from its verdict receipts:

| time (UTC) | head | mode | truncated | F-376-1 (migration) | F-376-2 (`tables.py`) |
|---|---|---|---|---|---|
| 04:07–04:52 | 6efd960c… | incremental | no | fixed | fixed |
| 09:42 | 663de5ab | deep, 5 pieces | yes | **still_open** | fixed |
| 10:10 | 663de5ab | closure after a ruling | yes | fixed | fixed |
| 10:29 | f3c954a8 | deep, 5 pieces | yes | fixed | **still_open** |
| 11:10 | f3c954a8 | closure after a ruling | no | fixed | fixed |

The incremental passes were never cut and never flipped. Every flip came from a whole-PR pass
in pieces, one of which held a file too large to bound.

Which cut it was is NOT established. The receipts record only `diff_truncated`, not the cut
stage. In the chunked path, `diff_truncated` comes from `oversized()`: a file whose patch exceeds
the split budget. That budget is computed over the whole provider chain, argv-limited providers
included. So a file can be "oversized" for splitting while a provider without an argv limit
(codex) still receives it whole (review round 2). The most likely attribution is an oversized
file in the chunked path, but it remains unverified. The decisions below do not depend on it:
they govern any judge that saw less than the judged diff, whichever cut produced it. The
red-cockpit#21 case is as reported in the ticket and is not re-measured here.

Mechanism, from the code at `61b6309`:

1. `fixed` is sticky in `rounds.assign` **unless a judge repeats the finding's id in its
   findings** (`repeated`). That is the only edge from `fixed` back to `still_open`.
2. `_within_part` (chunked path only) demotes a blocking finding on a file outside the judge's
   piece to `important`, **but keeps its id**. So a judge that never saw the file still
   reopens it by repeating the id the review context gave it.
3. `_without_blind_answers` drops a `previous` answer only when no head excerpt could be made
   for the finding's file (`blind`). With an excerpt present, an answer from a piece that
   lacks the file counts.
4. In an oversized piece the file's header is present, so `_files(chunk)` counts the file as
   read even though the judge saw only its first part.
5. The single-piece path never applies `_within_part`: a single file cut by `build_prompt` or
   by `judge()`'s argv shrink has no protection at all.
6. The check says only "the verdict covers the first part only", without naming what was not
   read.

Red-cockpit#21 shows the same symptom with a plan document that fills the window.

## Decisions

D1 — **What a reply saw.** Each `JudgeReply` carries what its judge was actually shown: the
diff text after prioritising and every cut (`build_prompt`, then `judge()`'s shrink). From
that text, one pure function computes a `Sight`. It holds the files whose patch is complete.
For each file cut inside, it holds the new-side line ranges of the hunks that are fully
visible. A `Sight` never comes from a judge's own claim.

D2 — **Which findings it governs.** Header paths are kept exactly as `headers.HEADER` reads
them: they are repository-relative, and a directory may legitimately be named `a` or `b`. A
renamed file counts under both its old and its new path, in the judged diff and in a `Sight`
alike. A finding's path is a judge's spelling, resolved against the judged diff's paths. The
candidates are the spelling itself and the spelling with ONE leading `./`, `a/` or `b/`
removed. Exactly one candidate in the judged diff resolves the finding to that path. None means
it lies outside the judged diff. More than one is ambiguous, and its words are inadmissible.
A reply's word on an earlier finding is *admissible* when one of these holds:
- every header of the judged diff (the delta, or the whole PR) is readable, the finding lies
  outside the judged diff, AND that finding's OWN head excerpt reached the judges.
  `_head_excerpts` quotes windows around open findings only, so a `fixed` finding never
  qualifies, even when another finding's window covers the same file. A finding on a file the
  change does not touch is thus verified against its own excerpt, as today; any other is
  moved by no one;
- the file is complete in the reply's `Sight`;
- the file is cut in the reply's `Sight`, the finding has a line, and that line is inside a
  fully visible hunk.
Otherwise the word is inadmissible. This covers a finding with no line on a cut file, and any
finding when a header of the judged diff cannot be read, since membership is then unknown.

This deliberately reverses one behaviour. Today, a judge whose piece lacks a file but which
read that file's head excerpt can keep a finding `still_open` against the judge that read the
file in the diff (`test_a_judge_that_read_the_excerpt_keeps_its_still_open`). Under D2, for a
file in the judged diff, the piece that holds the file decides. The excerpt governs only files
outside it.

D3 — **Inadmissible words are dropped, both kinds.** Before `_merge`, for every reply in both
paths (pieces and single piece), and before `rounds.assign` sees anything:
- a `previous` answer on an inadmissible finding is removed (both `fixed` and `still_open`);
- a finding that repeats a known id on an inadmissible location loses the repetition. It is
  dropped, not renumbered as new: it is the same claim, unverified.
`rounds.assign` is unchanged. An earlier finding that no admissible reply answered keeps its
last status: `fixed` stays fixed (sticky), and anything else stays `still_open`, as today.

D4 — **Named, never silent.** The verdict records two lists. `unseen_files` holds the files of
the judged diff that no reply saw complete. `not_reviewed` holds the ids of earlier findings
that no admissible reply answered while at least one inadmissible word about them was dropped.
Both lists are written into the receipt data as new keys; older readers ignore them. They are
bounded: at most 50 paths for `unseen_files`, with `unseen_count` giving the full number, and
`not_reviewed` is bounded by the findings cap. The payload budget is checked on the whole
attestation, not only the findings. The check
text replaces "covers the first part only" with the file list (capped, then "and N more"), plus
one line per `not_reviewed` id: "F-x: not re-verified (file not seen), keeps <status>".
`diff_truncated` keeps its meaning.

D5 — **A judge cannot block on what it did not see.** A *new* finding is demoted when its
location is not seen in its reply's `Sight`: a file outside the piece, a line outside the
visible hunks of a cut file, or no line on a cut file. This applies in both paths. Demotion
sets the severity to `important` AND the class to `note`. Today's `_within_part` lowers only
the severity, and on `spec_plan` `rounds.enforce_class` keeps a proposed `blocker` class, so
the finding still blocks.

D6 — **Fail closed on an unreadable piece.** When a piece's headers cannot be parsed (the
`_headers_parsed` case), its `Sight` is empty. All of its words on earlier findings are then
inadmissible: no flip, never a silent "seen". Its NEW findings are an explicit exception to the
D5 demotion: they are kept as today, blocking included (ticket `b1df8461`), because a judge on
an unreadable piece read a file the reviewer could not name.

## Non-goals

- Option (a), judging an oversized file whole in sub-pieces cut by hunk. It may follow as its
  own spec once (b) has run on real pull requests; (b) is what makes a cut safe in the
  meantime.
- Changing which findings appear in the review context, or the excerpt budget.
- Changing the round machine (rounds 1, 2, 3, closure) or the ruling rules.
- A per-repository exclusion of large generated files: `review.ignored_globs` exists already.

## Success criteria

1. Replaying the shape of brain-v42#376's 09:42 and 10:29 passes leaves both findings
   `fixed`. In the fixture, a piece without the migration file repeats `F-376-1`'s id, and a
   piece holding a cut `tables.py` repeats `F-376-2`'s id (no line). Each of the two assertions
   fails on main on its own.
2. A complete file in a piece still moves its findings both ways, as today. The single existing
   test that encodes the reversed behaviour
   (`test_a_judge_that_read_the_excerpt_keeps_its_still_open`) is rewritten to the new rule.
   Every other existing test stays green.
3. In the single-piece path, a file cut by `build_prompt` or by the argv shrink is covered by
   the same rule (a test for each cut).
4. A finding on a file outside the judged diff is still governed by the excerpt rule alone.
5. The check text names the unseen files and the not-reviewed ids; no check text says "first
   part only" any more.
6. `make ci` is green, and `rail check` passes on this repository.
