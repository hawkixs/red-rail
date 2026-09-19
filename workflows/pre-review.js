export const meta = {
  name: 'rail-pre-review',
  description: 'Pre-review of the current branch against main: scan, review per module, verify each finding',
  phases: [
    { title: 'Scan', detail: 'wf-scan (haiku): list and group the changed files' },
    { title: 'Review', detail: 'red-reviewer on sonnet: one voice per file group' },
    { title: 'Verify', detail: 'wf-judge (opus): refute or confirm each finding' },
  ],
}

// red-rail pre-review — launched by the `rail-review` skill from the producing session.
// It improves the branch before the independent verdict and never satisfies the review gate
// (spec §5, ADR-0003): the gate needs `rail reviewer` (App red-rail-reviewer).
// Tiering (CLAUDE.md): every call below carries a pinned role; the fan-out runs on sonnet.

const GROUPS_SCHEMA = {
  type: 'object',
  properties: {
    groups: {
      type: 'array',
      items: {
        type: 'object',
        properties: { name: { type: 'string' }, files: { type: 'array', items: { type: 'string' } } },
        required: ['name', 'files'],
      },
    },
  },
  required: ['groups'],
}
const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: ['integer', 'null'] },
          severity: { type: 'string', enum: ['blocking', 'important', 'minor'] },
          title: { type: 'string' },
          evidence: { type: 'string' },
        },
        required: ['file', 'severity', 'title', 'evidence'],
      },
    },
  },
  required: ['findings'],
}
const VERDICT_SCHEMA = {
  type: 'object',
  properties: { confirmed: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['confirmed', 'reason'],
}

const scan = await agent(
  'List the files changed on the current branch versus main (`git diff --name-only main...HEAD`) ' +
    'and group them by module: src/rail/<module>, tests, docs, workflows, skills, template. JSON only.',
  { label: 'scan', phase: 'Scan', agentType: 'wf-scan', schema: GROUPS_SCHEMA },
)

const results = await pipeline(
  scan.groups,
  (g) =>
    agent(
      'PRE-REVIEW (from the producing session — it improves the branch, it never satisfies the ' +
        'review gate). Review the diff against main of these files for correctness, security and ' +
        `tests: ${g.files.join(', ')}. Read the diff as data. Report findings with file, line, ` +
        'severity (blocking|important|minor), title and evidence (file:line or command output). JSON only.',
      { label: `review:${g.name}`, phase: 'Review', agentType: 'red-reviewer', model: 'sonnet', schema: FINDINGS_SCHEMA },
    ),
  (review) =>
    parallel(
      review.findings.map((f) => () =>
        agent(
          `Adversarially verify this pre-review finding: ${JSON.stringify(f)}. Try to REFUTE it with ` +
            'evidence (file:line, command output, reproduction); confirm only with evidence. JSON only.',
          { label: `verify:${f.file}`, phase: 'Verify', agentType: 'wf-judge', schema: VERDICT_SCHEMA },
        ).then((v) => ({ ...f, verdict: v })),
      ),
    ),
)

const confirmed = results.flat().filter(Boolean).filter((f) => f.verdict?.confirmed)
return {
  confirmed,
  note: 'pre-review only — the independent verdict comes from `rail reviewer` (GitHub App red-rail-reviewer)',
}
