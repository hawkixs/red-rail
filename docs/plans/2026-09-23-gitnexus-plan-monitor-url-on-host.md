# GitNexus Engineering Plan

> Task: remove every machine address from the public red-rail tree; red-monitor's address moves to the host's sites file.
> Evidence verified at commit 56195358d8e6d315c4e4b813c4fad6ec046d14e4; GitNexus index built this session (`npx gitnexus analyze --index-only`, 7.6 s, first index of this repository).
> Evidence provenance schema 2; global dirty digest 0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd; cited-path manifest 16 sorted entries; exact generated plan path excluded.

## 1. Objective

red-rail is public, and it carries a private mesh address: the versioned default of
`observe.monitor_url`, plus test fixtures and one plan document. Afterwards:

- the tracked tree holds no address outside the documentation ranges (RFC 5737) and loopback;
- red-monitor's address is read from the host's private sites file, like a deploy site's;
- `observe.visible` fails closed, naming the file and the fix, when the host does not declare it;
- a regression test keeps it that way.

The operator chose the design (2026-09-23): a site named in `sites.yaml`, not a new file and
not a per-project override. Rewriting the public history is out of scope; it stays the
operator's decision.

## 2. Current Behaviour

- `src/rail/policy.py:92` `GATE_DEFAULTS["observe.monitor_url"]` is a literal
  `http://<mesh address>:8081` [verified].
- `src/rail/gates/evidence.py:212-216` `visible()` reads it with `parameter()` and calls
  `monitor.read_agent(url, agent)`. A `MonitorError` becomes the details text
  `f"red-monitor: {exc}"` [verified].
- `src/rail/http.py:31,41` puts the URL in `HttpError`, and `src/rail/monitor.py:55,59` puts it
  in `MonitorError`. So today an unreachable monitor prints its address in `rail check` output
  [verified].
- `src/rail/deploy/sites.py:80-97` `load_site(name, path=None)` reads the 0600 host file
  (`RAIL_SITES_FILE` or `~/.config/red-rail/sites.yaml`). Every failure is a `DeployError` that
  names the site, the file and the fix [verified].
- The same file provides `substitute_address` and `redact_address` [verified].
- `src/rail/deploy/private_compose.py:117-121,150-153` is the established pattern: site →
  `load_site` → substitute the token → redact every outgoing text [verified].
- `src/rail/model.py:26` defines `ADDRESS_TOKEN = "${BIND_ADDRESS}"` [verified].

## 3. Relevant Architecture

- `policy.py` holds the versioned defaults. It imports `rail.gates` and `rail.model`, never
  `rail.deploy` [verified]. So the default stays a plain string that spells the token.
- `evidence.py` holds the gates, which never raise.
- `deploy/sites.py` is the only reader of the host's address file.
- `observe.visible` has `workstation` scope, so CI skips it (CLAUDE.md). CI therefore never
  needs a sites file.
- `contract_guard._addresses` refuses every literal IPv4 address except 192.0.2.0/24
  (`src/rail/contract_guard.py:46-59`) [verified]. Its tests therefore need a refused address:
  198.51.100.0/24 (RFC 5737 TEST-NET-2) is documentation and is still refused.

## 4. GitNexus Findings

- `context visible` (repo red-rail): no incoming calls, because it is registered through the
  gate registry. Outgoing calls: `parameter`, `read_agent`, `stack_containers`, `image_digest`,
  `declarations`, `_newest`, `_absent`, `Need.result` [graph].
- `context load_site`: its only caller is `private_compose.Parameters.read`. It calls
  `sites_file` and `read_private_file` [graph].
- Inventory by `grep` / `git grep` of the tracked tree, receipts excluded [verified]:
  - `src/rail/policy.py:92`;
  - `tests/test_policy.py:131`;
  - `tests/test_gates_evidence.py:385`;
  - `tests/test_monitor.py:56-84` (6 occurrences);
  - `tests/test_contract_guard.py:20-43,79-81` (mesh addresses, plus one private LAN address
    at line 45);
  - `tests/test_cli_ledger.py:588,597`;
  - `docs/plans/2026-09-19-phase-3-red-probe.md` (lines 83, 189, 591-663).
  - No other IPv4 literal outside documentation or loopback.

## 5. Statement-Level PDG Findings

No PDG layer: the index was built without `--pdg`. The change is one function's
configuration read, and the reading of `visible` lines 190-254 at the pinned commit covers it:
the URL feeds only `read_agent`, and only `MonitorError` text flows into the details.

## 6. Proposed Changes

1. `src/rail/policy.py` `GATE_DEFAULTS`:
   - `observe.monitor_url` becomes `"http://${BIND_ADDRESS}:8081"`, with a comment saying the
     host fills the token from `observe.monitor_site`;
   - add `observe.monitor_site: "red-monitor"`, a label and not an address, which must match
     `SITE_PATTERN`.
   - A project can still override `observe.monitor_url` with a literal URL in `rail.yaml`; no
     token means no site lookup.
2. `src/rail/gates/evidence.py` `visible()`. When `ADDRESS_TOKEN in url`:
   - `load_site(site).address`, where `DeployError` becomes a failing `GateResult` with
     `f"red-monitor: {exc}"`, so the message names the site, the file and the fix;
   - then `substitute_address`.
   - `MonitorError` text is passed through `redact_address(text, address, site)` when an address
     was resolved.
   - Imports: `from rail.deploy import DeployError` and
     `from rail.deploy.sites import load_site, redact_address, substitute_address`, plus
     `ADDRESS_TOKEN` from `rail.model`. Check the import cycle (the §12 assumption).
3. Test fixtures move to documentation addresses:
   - 192.0.2.2 wherever an address is only a value (`test_monitor.py`, `test_gates_evidence.py`,
     the phase-3 plan);
   - 198.51.100.4 wherever the address must be refused (`test_contract_guard.py`,
     `test_cli_ledger.py`);
   - 203.0.113.10:8080 for the private LAN example in `test_contract_guard.py:45`.
   - Every assertion keeps its meaning.
4. `tests/test_no_machine_address.py` (new): `git ls-files`, receipts included. Every
   dotted-quad that parses as IPv4 must be in 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24,
   127.0.0.0/8 or be 0.0.0.0. Use the same lookarounds as `contract_guard._DOTTED`, so that
   `10.x.y.z.5` and versions are not addresses. The failure lists `path:line` without echoing a
   second copy of the address.
5. Docs:
   - `docs/specs/2026-09-23-sites-on-the-host.md`: one added decision, "the monitor is a site
     too";
   - `CLAUDE.md`: the `monitor.py` / `observe.visible` bullet names `observe.monitor_site`;
   - `skills/rail-deploy/SKILL.md`: one line saying the host's sites file declares
     `red-monitor`.

## 7. Implementation Sequence

1. TDD in `tests/test_gates_evidence.py`, then implement §6.1 and §6.2. Update the
   `test_policy.py:131` assertion in the same step.
   - Note: the tree is coherent after this step, even though fixture addresses remain.
2. Move the fixtures to documentation addresses (§6.3). Tests only; the suite stays green.
3. Docs (§6.5) and the phase-3 plan's addresses.
4. Add the regression guard (§6.4). It passes only once steps 1-3 have landed, which is why it
   is last.
5. Host, AFTER merge and BEFORE reinstalling the global rail (operator's consent): add
   `red-monitor: {address: "…"}` to `~/.config/red-rail/sites.yaml`, keeping mode 0600. Then
   `rail check observe` in red-alerts must stay 2/2.

## 8. Test Strategy

- `tests/test_gates_evidence.py`, updated `test_visible_needs_a_running_container…`: set
  `RAIL_SITES_FILE` to a 0600 tmp file declaring `red-monitor: {address: "192.0.2.2"}` and
  assert `seen[-1] == ("http://192.0.2.2:8081", "vps")`.
- New scenarios in the same file:
  - no sites file → `visible` fails; the details contain `site red-monitor` and the file path;
    `read_agent` is never called.
  - sites file without `red-monitor` → fails; the details list the known sites.
  - `read_agent` raises `MonitorError("http://192.0.2.2:8081/api/latest: HTTP 503")` → the
    details contain `red-monitor` and NOT `192.0.2.2`.
  - `rail.yaml` `gates: observe.monitor_url` set to a literal `http://192.0.2.9:8081` (with a
    reason) → no sites file needed, and `read_agent` gets that URL.
- `tests/test_policy.py`: the new defaults, and `monitor_site` matches `SITE_PATTERN`.
- `tests/test_no_machine_address.py`: green on the final tree. Checked by hand: a temporary
  mesh address in a scratch tracked file makes it fail.
- Commands: `make ci` (lint + test + `rail check`); `uv run pytest -q` for the quick loop.

## 9. Risk and Impact Analysis

- Behaviour change on the host: until the host step, `observe.visible` is red for every project
  (red-alerts, red-probe) with a clear message. It fails closed, never hidden. CI is unaffected
  (workstation scope).
- `load_site`'s message wording talks about a site, which fits.
- Cost: a second reader of `sites.yaml`, the same file and the same validation; no new secret
  surface.
- Import cycle: `rail.deploy.sites` imports `rail.deploy`, `rail.model` and `rail.private`. If
  `rail.gates.evidence` were already imported by one of them, the cycle would break at import
  time (§12).
- The history of the public repository keeps the old address; that is the operator's decision.

## 10. Files Expected to Change

| File | Symbols | Reason |
| ---- | ------- | ------ |
| src/rail/policy.py | GATE_DEFAULTS | tokenised URL + monitor_site |
| src/rail/gates/evidence.py | visible | resolve the site, redact |
| tests/test_gates_evidence.py | visible tests | fixture + 4 scenarios |
| tests/test_policy.py | defaults test | new defaults |
| tests/test_monitor.py | read_agent tests | documentation address |
| tests/test_contract_guard.py | guard tests | refused documentation addresses |
| tests/test_cli_ledger.py | contract set test | refused documentation address |
| tests/test_no_machine_address.py | new | regression guard |
| docs/plans/2026-09-19-phase-3-red-probe.md | — | documentation addresses |
| docs/specs/2026-09-23-sites-on-the-host.md | — | the monitor is a site |
| CLAUDE.md, skills/rail-deploy/SKILL.md | — | name observe.monitor_site |

## 11. Reusable Implementation Context

```yaml
implementation_context:
  task_summary: >-
    Replace the literal mesh address default of observe.monitor_url with
    "http://${BIND_ADDRESS}:8081" + observe.monitor_site "red-monitor" resolved through
    deploy/sites.load_site; move every tracked IPv4 literal to RFC 5737; add a regression guard.
  acceptance_criteria:
    - git grep finds no IPv4 literal outside 192.0.2/24, 198.51.100/24, 203.0.113/24, 127/8, 0.0.0.0
    - observe.visible without a host declaration fails naming the site and the file, never raises
    - no MonitorError detail echoes the resolved address
    - make ci green; rail check passes on red-rail
  evidence_provenance: {
      "schema_version": 2,
      "head_commit": "56195358d8e6d315c4e4b813c4fad6ec046d14e4",
      "generated_plan_path": "docs/plans/2026-09-23-gitnexus-plan-monitor-url-on-host.md",
      "global_dirty_digest": {
        "algorithm": "sha256",
        "canonicalization": "gitnexus-evidence-provenance-v2 NUL-framed UTF-8 records",
        "value": "0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd"
      },
      "cited_path_manifest": [
        {
          "path": "Makefile",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:26456c5db711d303ef20a24fca646737dae173e811437e0a27b77ad4808701ea",
          "index_digest": "sha256:26456c5db711d303ef20a24fca646737dae173e811437e0a27b77ad4808701ea",
          "worktree_digest": "sha256:26456c5db711d303ef20a24fca646737dae173e811437e0a27b77ad4808701ea",
          "untracked_digest": "absent"
        },
        {
          "path": "docs/plans/2026-09-19-phase-3-red-probe.md",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:fff3d7f3c4701fa88c08d898185597be730d08211b961cc89fe83df72585bb5f",
          "index_digest": "sha256:fff3d7f3c4701fa88c08d898185597be730d08211b961cc89fe83df72585bb5f",
          "worktree_digest": "sha256:fff3d7f3c4701fa88c08d898185597be730d08211b961cc89fe83df72585bb5f",
          "untracked_digest": "absent"
        },
        {
          "path": "docs/specs/2026-09-23-sites-on-the-host.md",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:3a36ca1752b56fe2dcc51572f612e009ba99984606c81015e4544707a3e018f2",
          "index_digest": "sha256:3a36ca1752b56fe2dcc51572f612e009ba99984606c81015e4544707a3e018f2",
          "worktree_digest": "sha256:3a36ca1752b56fe2dcc51572f612e009ba99984606c81015e4544707a3e018f2",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/contract_guard.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:0a87ff42b37ba4ee6fcb1450b7bfee75844ff03c01df617a3df9acd749ab6c02",
          "index_digest": "sha256:0a87ff42b37ba4ee6fcb1450b7bfee75844ff03c01df617a3df9acd749ab6c02",
          "worktree_digest": "sha256:0a87ff42b37ba4ee6fcb1450b7bfee75844ff03c01df617a3df9acd749ab6c02",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/deploy/private_compose.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:a1ad0d62e3fbcc4c0d06e6ccd5764b858b467174cf9c87efbef83efccf76787e",
          "index_digest": "sha256:a1ad0d62e3fbcc4c0d06e6ccd5764b858b467174cf9c87efbef83efccf76787e",
          "worktree_digest": "sha256:a1ad0d62e3fbcc4c0d06e6ccd5764b858b467174cf9c87efbef83efccf76787e",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/deploy/sites.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:53bfb1065924f091827dc64245a9e6805d3d29a87cea66b7b11b9d9b48d9ab59",
          "index_digest": "sha256:53bfb1065924f091827dc64245a9e6805d3d29a87cea66b7b11b9d9b48d9ab59",
          "worktree_digest": "sha256:53bfb1065924f091827dc64245a9e6805d3d29a87cea66b7b11b9d9b48d9ab59",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/gates/evidence.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:9761dfeffb4c016245f0ddea7fcf68e0bbb2b7b7416417bce05646e3abf027d7",
          "index_digest": "sha256:9761dfeffb4c016245f0ddea7fcf68e0bbb2b7b7416417bce05646e3abf027d7",
          "worktree_digest": "sha256:9761dfeffb4c016245f0ddea7fcf68e0bbb2b7b7416417bce05646e3abf027d7",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/http.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:4d82d2f91d40e1f3d9ac528fbfab05ae4f2fb1342ef551032ffe5a6a1e51888c",
          "index_digest": "sha256:4d82d2f91d40e1f3d9ac528fbfab05ae4f2fb1342ef551032ffe5a6a1e51888c",
          "worktree_digest": "sha256:4d82d2f91d40e1f3d9ac528fbfab05ae4f2fb1342ef551032ffe5a6a1e51888c",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/model.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:3494d0f153b82cbe8b25102ce24da940c202567ed722d4ab43ff4c6e3e3e2c2d",
          "index_digest": "sha256:3494d0f153b82cbe8b25102ce24da940c202567ed722d4ab43ff4c6e3e3e2c2d",
          "worktree_digest": "sha256:3494d0f153b82cbe8b25102ce24da940c202567ed722d4ab43ff4c6e3e3e2c2d",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/monitor.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:fcf2fd3470f64f5acf46e7b7b0cb50a54da865ec7036ee0b33c5c55e9654047f",
          "index_digest": "sha256:fcf2fd3470f64f5acf46e7b7b0cb50a54da865ec7036ee0b33c5c55e9654047f",
          "worktree_digest": "sha256:fcf2fd3470f64f5acf46e7b7b0cb50a54da865ec7036ee0b33c5c55e9654047f",
          "untracked_digest": "absent"
        },
        {
          "path": "src/rail/policy.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:a1ab717d9b28f80f6ce8d26c57eed5406bf98240ce2906ba84415682e41f6c22",
          "index_digest": "sha256:a1ab717d9b28f80f6ce8d26c57eed5406bf98240ce2906ba84415682e41f6c22",
          "worktree_digest": "sha256:a1ab717d9b28f80f6ce8d26c57eed5406bf98240ce2906ba84415682e41f6c22",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/test_cli_ledger.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:8fe9ac397de2e12f6d88c7de89b87318591cf29c46a4bb9824d6d24dbb718591",
          "index_digest": "sha256:8fe9ac397de2e12f6d88c7de89b87318591cf29c46a4bb9824d6d24dbb718591",
          "worktree_digest": "sha256:8fe9ac397de2e12f6d88c7de89b87318591cf29c46a4bb9824d6d24dbb718591",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/test_contract_guard.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:ba08a0269381778e6d7c0438dab31e2bb8ae0b3008f720487c487f8b9f43dfa3",
          "index_digest": "sha256:ba08a0269381778e6d7c0438dab31e2bb8ae0b3008f720487c487f8b9f43dfa3",
          "worktree_digest": "sha256:ba08a0269381778e6d7c0438dab31e2bb8ae0b3008f720487c487f8b9f43dfa3",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/test_gates_evidence.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:9226b2d18ff6efda47c427c51f72c6f9d5217f6aa5b809ba7b4c42485bbed1e3",
          "index_digest": "sha256:9226b2d18ff6efda47c427c51f72c6f9d5217f6aa5b809ba7b4c42485bbed1e3",
          "worktree_digest": "sha256:9226b2d18ff6efda47c427c51f72c6f9d5217f6aa5b809ba7b4c42485bbed1e3",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/test_monitor.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:82b79d1f108fc6a3437d621ac02332ec0a90e34ca7653a181cccb43c26d703e9",
          "index_digest": "sha256:82b79d1f108fc6a3437d621ac02332ec0a90e34ca7653a181cccb43c26d703e9",
          "worktree_digest": "sha256:82b79d1f108fc6a3437d621ac02332ec0a90e34ca7653a181cccb43c26d703e9",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/test_policy.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:3a21e7b4621414af5745b41f776d3894bde6ab5be323ccf774894958c8d9e5aa",
          "index_digest": "sha256:3a21e7b4621414af5745b41f776d3894bde6ab5be323ccf774894958c8d9e5aa",
          "worktree_digest": "sha256:3a21e7b4621414af5745b41f776d3894bde6ab5be323ccf774894958c8d9e5aa",
          "untracked_digest": "absent"
        }
      ]
    }
  primary_symbols:
    - {symbol: visible, file: src/rail/gates/evidence.py, lines: "190-254", role: the only reader of observe.monitor_url}
    - {symbol: GATE_DEFAULTS, file: src/rail/policy.py, lines: "91-93", role: versioned defaults}
    - {symbol: load_site, file: src/rail/deploy/sites.py, lines: "80-97", role: host address lookup}
  related_symbols:
    - {symbol: Parameters.read, relationship: pattern, relevance: "src/rail/deploy/private_compose.py:117-121 site -> address"}
    - {symbol: PrivateCompose.redact, relationship: pattern, relevance: "src/rail/deploy/private_compose.py:150-153"}
    - {symbol: _addresses, relationship: constrains fixtures, relevance: "src/rail/contract_guard.py:46-59 exempts only 192.0.2.0/24"}
    - {symbol: read_agent, relationship: CALLS, relevance: "src/rail/monitor.py:46-59 errors carry the URL"}
  files_to_modify:
    - {file: src/rail/policy.py, symbols: [GATE_DEFAULTS], intended_change: tokenised monitor_url + monitor_site}
    - {file: src/rail/gates/evidence.py, symbols: [visible], intended_change: resolve site, fail closed, redact}
    - {file: tests/test_no_machine_address.py, symbols: [], intended_change: new regression guard}
  tests:
    - file: tests/test_gates_evidence.py
      scenarios:
        - sites file declares red-monitor 192.0.2.2 -> visible -> read_agent called with http://192.0.2.2:8081
        - no sites file -> visible -> fails, details name site red-monitor and the file, read_agent not called
        - site missing -> visible -> fails, details list known sites
        - read_agent raises MonitorError with the address -> visible -> details say red-monitor, not the address
        - rail.yaml overrides monitor_url with a literal URL -> visible -> no sites file needed
    - file: tests/test_policy.py
      scenarios: [defaults are the tokenised URL and red-monitor; monitor_site matches SITE_PATTERN]
    - file: tests/test_no_machine_address.py
      scenarios: [tracked tree -> scan -> only documentation/loopback addresses]
  verification_commands:
    - uv run pytest -q
    - make ci
  assumptions:
    - "No import cycle: check `uv run python -c 'import rail.gates.evidence'` after adding the rail.deploy.sites import"
    - "read_private_file refuses a mode other than 0600: fixtures must chmod 0600 (see tests/test_sites.py:18)"
  open_questions:
    - Rewrite the public history or not: the operator's decision, out of scope
  avoid:
    - Do not repeat full repository discovery
    - Do not replace established patterns without evidence
    - Never write a real machine address anywhere, including tests, commits and PR text
    - Do not make observe.visible pass when the host declares nothing
    - Do not touch the host sites file before the operator's consent (sequence step 5)
```

## 12. Assumptions and Open Questions

- [assumed] `rail.gates.evidence` can import `rail.deploy.sites` without a cycle. Check it at
  step 1.
- [assumed] `read_private_file` requires 0600. `tests/test_sites.py:18` chmods, so the new
  fixtures do the same.
- Open: rewriting the public history (the old address stays in `git log`). Operator's decision.
- Deferred: agent names such as `vps` and `red-base` are identifiers, not addresses. The
  operator's rule forbids infrastructure identifiers too, but red-alerts already names its
  site. Out of scope; to raise separately.
- Deferred: ticket e3278ea3 (same-version redeploy never recreates the container).

## 13. Definition of Done

- `git grep` of IPv4 literals outside RFC 5737 / loopback / 0.0.0.0 is empty, and the new guard
  enforces it.
- `observe.visible` resolves `red-monitor` from the host's sites file, fails closed with the
  file and the fix when it is absent, and never prints the address.
- `make ci` green, summary line read; `rail check` passes on red-rail.
- After merge and the host step: `rail check observe` in red-alerts is 2/2 with the reinstalled
  rail.
