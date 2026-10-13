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

D2 — **Which findings it governs.** A reply's word on an earlier finding is *admissible* when
any of these holds:
- the finding's file is not in the diff being judged at all (the delta, or the whole PR). This
  case keeps today's excerpt rule (`blind`) unchanged, so a finding on a file the change does
  not touch is still verified against its head excerpt;
- the file is complete in the reply's `Sight`;
- the file is cut in the reply's `Sight`, the finding has a line, and that line is inside a
  fully visible hunk.
Otherwise the word is inadmissible. A finding with no line on a cut file is inadmissible.

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
Both lists are written into the receipt data as new keys; older readers ignore them. The check
text replaces "covers the first part only" with the file list (capped, then "and N more"), plus
one line per `not_reviewed` id: "F-x: not re-verified (file not seen), keeps <status>".
`diff_truncated` keeps its meaning.

D5 — **Blocking stays bounded as today.** `_within_part`'s demotion of a blocking *new*
finding on a file outside the piece stays. D2 extends it to a line outside the visible hunks of
a cut file in the reply's `Sight`, in both paths.

D6 — **Fail closed on an unreadable piece.** When a piece's headers cannot be parsed (the
`_headers_parsed` case), its `Sight` is empty. All of its words on earlier findings in the
judged diff are then inadmissible: no flip, never a silent "seen". Its new findings are kept as
today (ticket `b1df8461`).

## Non-goals

- Option (a), judging an oversized file whole in sub-pieces cut by hunk. It may follow as its
  own spec once (b) has run on real pull requests; (b) is what makes a cut safe in the
  meantime.
- Changing which findings appear in the review context, or the excerpt budget.
- Changing the round machine (rounds 1, 2, 3, closure) or the ruling rules.
- A per-repository exclusion of large generated files: `review.ignored_globs` exists already.

## Success criteria

1. Replaying brain-v42#376's 09:42 and 10:29 passes (a fixture built from their shape: a piece
   without the migration file repeats `F-376-1`; a cut piece answers on a line past its cut)
   leaves both findings at their last status, and lists them as `not_reviewed`.
2. A complete file in a piece still moves its findings both ways, exactly as today (existing
   tests stay green unchanged, beyond the new receipt keys).
3. In the single-piece path, a file cut by `build_prompt` or by the argv shrink is covered by
   the same rule (a test for each cut).
4. A finding on a file outside the judged diff is still governed by the excerpt rule alone.
5. The check text names the unseen files and the not-reviewed ids; no check text says "first
   part only" any more.
6. `make ci` is green, and `rail check` passes on this repository.
