"""`rail reviewer once`: private config, explicit errors, no network in tests."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.reviewer.verdict import Finding, ReviewVerdict
from tests.helpers import conforming_tree


def _config(tmp_path: Path, mode: int = 0o600) -> Path:
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o600)
    path = tmp_path / "reviewer.yaml"
    path.write_text(
        f"app_id: 1\ninstallation_id: 2\nprivate_key_file: {key}\n"
        f"repositories:\n  - slug: hawkixs/red-alpha\n    path: {tmp_path / 'red-alpha'}\n"
    )
    path.chmod(mode)
    return path


def test_once_refuses_a_world_readable_config(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["reviewer", "once", "--config", str(_config(tmp_path, 0o644))])
    assert out.exit_code == 1 and "mode 644" in out.output


def test_pr_needs_repository(tmp_path: Path) -> None:
    out = CliRunner().invoke(
        main, ["reviewer", "once", "--config", str(_config(tmp_path)), "--pr", "7"]
    )
    assert out.exit_code == 2 and "--pr needs --repository" in out.output


def test_once_reports_the_error_of_an_unreadable_checkout(tmp_path: Path, monkeypatch) -> None:
    class NoGitHub:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rail.reviewer.github.GitHubApp", NoGitHub)
    out = CliRunner().invoke(main, ["reviewer", "once", "--config", str(_config(tmp_path))])
    assert out.exit_code != 0 and "rail.yaml" in out.output


GLOBS = (
    "gates:\n  review.ignored_globs:\n    value: ['internal/web/static/*']\n"
    "    reason: committed bundle, proven equal to its build by CI\n"
)


def _policy_seen(
    tmp_path: Path,
    monkeypatch,
    *,
    merged: str = "",
    working: str = "",
    fetched: bool = True,
    decoy: str = "",
) -> tuple[object, str]:
    """The policy `rail reviewer once` hands to `review_pull` for red-alpha's open PR, and its
    output. `merged` is appended to the rail.yaml committed on origin/main; `decoy` to one
    committed on a LOCAL branch named `origin/main`; `working` is then appended to the working
    tree only, never committed."""
    from tests.helpers import commit_all, git

    class OnePullGitHub:
        def __init__(self, **kwargs):
            pass

        def pull(self, repository, number):
            return number

        def close(self):
            pass

    seen = {}

    def review_pull(pull, *, policy, **kwargs):
        seen["policy"] = policy
        raise SystemExit(0)

    monkeypatch.setattr("rail.reviewer.github.GitHubApp", OnePullGitHub)
    monkeypatch.setattr("rail.reviewer.service.review_pull", review_pull)
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    manifest = repo / "rail.yaml"
    manifest.write_text(manifest.read_text() + merged)
    commit_all(repo, "chore: the reviewed state")
    if fetched:
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    if decoy:
        git(repo, "checkout", "-q", "-b", "origin/main")
        manifest.write_text(manifest.read_text() + decoy)
        commit_all(repo, "chore: never reviewed")
        git(repo, "checkout", "-q", "main")
    manifest.write_text(manifest.read_text() + working)
    config = _config(tmp_path)
    config.write_text(config.read_text().replace(str(tmp_path / "red-alpha"), str(repo)))
    out = CliRunner().invoke(
        main,
        ["reviewer", "once", "--config", str(config), "--repository", "hawkixs/red-alpha"]
        + ["--pr", "7"],
    )
    return seen["policy"], out.output


def test_a_repository_adds_its_declared_ignored_globs_to_the_defaults(
    tmp_path: Path, monkeypatch
) -> None:
    """Ticket f0aa9c29: the repository declares, in its own rail.yaml and with a reason, the
    generated files no judge reads. Its globs are ADDED to the reviewer's defaults, never
    replace them."""
    from rail.reviewer.policy import ReviewPolicy

    policy, _ = _policy_seen(tmp_path, monkeypatch, merged=GLOBS)
    assert policy.ignored_globs == (*ReviewPolicy().ignored_globs, "internal/web/static/*")


def test_without_a_declaration_the_reviewer_keeps_its_own_policy(
    tmp_path: Path, monkeypatch
) -> None:
    from rail.reviewer.policy import ReviewPolicy

    policy, output = _policy_seen(tmp_path, monkeypatch)
    assert policy.ignored_globs == ReviewPolicy().ignored_globs
    assert "ignored_globs" not in output


def test_an_uncommitted_ignored_glob_hides_nothing(tmp_path: Path, monkeypatch) -> None:
    """Found by the adversarial review: the checkout is a working tree. A glob edited there and
    never merged was never judged, yet it would hide code from every judge. Only the rail.yaml
    committed on origin/main counts; the difference is reported, never applied."""
    from rail.reviewer.policy import ReviewPolicy

    policy, output = _policy_seen(tmp_path, monkeypatch, working=GLOBS)
    assert policy.ignored_globs == ReviewPolicy().ignored_globs
    assert "review.ignored_globs" in output and "origin/main" in output


def test_a_local_branch_named_origin_main_hides_nothing(tmp_path: Path, monkeypatch) -> None:
    """Found by the adversarial review: git resolves a bare `origin/main` to a local branch of
    that name before the remote-tracking ref, and only warns on stderr. The globs are read at
    `refs/remotes/origin/main`, what was fetched from GitHub."""
    from rail.reviewer.policy import ReviewPolicy

    policy, _ = _policy_seen(tmp_path, monkeypatch, decoy=GLOBS)
    assert policy.ignored_globs == ReviewPolicy().ignored_globs


def test_without_origin_main_no_declared_glob_applies(tmp_path: Path, monkeypatch) -> None:
    from rail.reviewer.policy import ReviewPolicy

    policy, output = _policy_seen(tmp_path, monkeypatch, merged=GLOBS, fetched=False)
    assert policy.ignored_globs == ReviewPolicy().ignored_globs
    assert "review.ignored_globs" in output and "origin/main" in output


def test_a_checkout_without_a_manifest_names_the_onboarding_procedure(
    tmp_path: Path, monkeypatch
) -> None:
    """Ticket 98d8f8a8: the onboarding pull request is the one that ADDS rail.yaml, so the
    checkout reviewer.yaml declares has none yet. The manifest stays read from that trusted
    checkout, never from the pull request's head (it chooses where the verdict is attested):
    the refusal names the procedure instead of a bare "missing"."""

    class NoGitHub:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rail.reviewer.github.GitHubApp", NoGitHub)
    (tmp_path / "red-alpha").mkdir()
    out = CliRunner().invoke(main, ["reviewer", "once", "--config", str(_config(tmp_path))])
    assert out.exit_code == 1
    assert "hawkixs/red-alpha" in out.output and str(tmp_path / "red-alpha") in out.output
    assert "onboarding" in out.output and "worktree" in out.output
    assert "reviewer.yaml" in out.output


def test_once_refuses_a_repository_the_config_does_not_watch(tmp_path: Path, monkeypatch) -> None:
    """Reported by red-arena (c964c791): a slug missing from reviewer.yaml used to review
    nothing and exit 0, which reads as "nothing to review" rather than "not watched"."""

    class NoGitHub:
        def __init__(self, **kwargs):
            raise AssertionError("an unwatched repository must be refused before GitHub")

    monkeypatch.setattr("rail.reviewer.github.GitHubApp", NoGitHub)
    config = str(_config(tmp_path))
    unwatched = ["reviewer", "once", "--config", config, "--repository", "hawkixs/red-beta"]
    for extra in ([], ["--pr", "7"]):
        out = CliRunner().invoke(main, [*unwatched, *extra])
        assert out.exit_code == 2, out.output
        assert "not watched by" in out.output and "reviewer.yaml" in out.output
        assert "hawkixs/red-alpha" in out.output
        assert "reviewed 0" not in out.output


def _seed_three_verdicts(ledger) -> None:
    """Three judged rounds of the same open blocker: PR #7 lands on round 3, awaiting a
    ruling on F-7-1."""
    blocker = Finding.model_validate(
        {
            "severity": "blocking",
            "file": "a.py",
            "title": "t",
            "evidence": "e",
            "id": "F-7-1",
            "class": "blocker",
            "status": "still_open",
        }
    )
    for n in (1, 2, 3):
        data = ReviewVerdict(
            verdict="request_changes", summary="s", findings=[blocker], round=n, artifact="code"
        ).as_attestation_data(sha=str(n) * 40, check_run_id=n, repository="hawkixs/red-alpha", pr=7)
        ledger.attest(
            "red-alpha",
            AttestationKind.REVIEW_VERDICT,
            data,
            issuer="red-rail-reviewer",
            idempotency_key=f"review_verdict:{n}",
        )


def _awaiting_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    ledger = FileLedger(repo / RECEIPTS_DIR)
    _seed_three_verdicts(ledger)
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o600)
    config = tmp_path / "reviewer.yaml"
    config.write_text(
        f"app_id: 1\ninstallation_id: 2\nprivate_key_file: {key}\n"
        f"repositories:\n  - slug: hawkixs/red-alpha\n    path: {repo}\n"
    )
    config.chmod(0o600)
    return repo, config


def _rule(
    config: Path,
    finding: str = "F-7-1",
    decision: str = "rename it",
    confirm: str = "F-7-1",
    extra=(),
):
    args = [
        "reviewer",
        "rule",
        "--config",
        str(config),
        "--repository",
        "hawkixs/red-alpha",
        "--pr",
        "7",
        "--finding",
        finding,
        "--as",
        "fix",
        "--decision",
        decision,
        *extra,
    ]
    return CliRunner().invoke(main, args, input=f"{confirm}\n")


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ruling is typed by the operator: the terminal predicate says so."""
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: True)


@pytest.mark.usefixtures("at_a_terminal")
def test_rule_writes_one_ruling_after_typed_confirmation(tmp_path: Path, monkeypatch) -> None:
    repo, config = _awaiting_repo(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    out = _rule(config)
    assert out.exit_code == 0, out.output
    rulings = FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )
    assert len(rulings) == 1 and rulings[0].issuer == "operator"
    assert rulings[0].idempotency_key == "review_ruling:hawkixs/red-alpha#7:F-7-1:1"
    assert rulings[0].data["decision"] == "rename it"


@pytest.mark.parametrize(
    ("kwargs", "env_ci", "needle"),
    [
        ({"finding": "F-7-9"}, False, "not an open blocker awaiting a ruling"),
        ({"decision": " "}, False, "decision"),
        ({"confirm": "F-7-2"}, False, "confirmation"),
        ({}, True, "CI"),
    ],
)
@pytest.mark.usefixtures("at_a_terminal")
def test_rule_refuses_without_writing(tmp_path: Path, monkeypatch, kwargs, env_ci, needle) -> None:
    repo, config = _awaiting_repo(tmp_path)
    if env_ci:
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("CI", raising=False)
    out = _rule(config, **kwargs)
    assert out.exit_code == 2 and needle in out.output
    assert not FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )


@pytest.mark.usefixtures("at_a_terminal")
def test_rule_writes_a_review_ruling_in_brain_mode(tmp_path: Path, monkeypatch) -> None:
    """The command's ledger comes from `open_ledger`, brain included (spec 2026-09-25, D9):
    it never assumes the file ledger."""
    from rail.brain.client import BrainClient
    from rail.ledger import Contract, Deliverable, open_ledger
    from tests.fake_brain import FakeBrain

    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    ticket = "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml")
        .read_text()
        .replace("ledger: file\n", f"ledger: brain\nticket: {ticket}\n")
    )
    brain = FakeBrain(agent="operator")
    brain.add_ticket("red", "red-alpha", ticket)
    brain.register_repository("red-alpha", 4243, "hawkixs/red-alpha")
    ledger = open_ledger(repo, client=BrainClient.in_memory(brain, agent="operator"))
    ledger.contract_set(
        "red-alpha",
        Contract(
            objective="ship red-alpha",
            deliverables=[
                Deliverable(
                    key="main",
                    repository="hawkixs/red-alpha",
                    repository_id=4243,
                    no_checks_reason="fixture: no check declared",
                )
            ],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c0",
    )
    _seed_three_verdicts(ledger)

    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o600)
    config = tmp_path / "reviewer.yaml"
    config.write_text(
        f"app_id: 1\ninstallation_id: 2\nprivate_key_file: {key}\n"
        f"repositories:\n  - slug: hawkixs/red-alpha\n    path: {repo}\n"
    )
    config.chmod(0o600)

    monkeypatch.setattr("rail.commands.reviewer.open_ledger", lambda *_a, **_k: ledger)
    monkeypatch.delenv("CI", raising=False)
    out = CliRunner().invoke(
        main,
        [
            "reviewer",
            "rule",
            "--config",
            str(config),
            "--repository",
            "hawkixs/red-alpha",
            "--pr",
            "7",
            "--finding",
            "F-7-1",
            "--as",
            "fix",
            "--decision",
            "rename it",
        ],
        input="F-7-1\n",
    )
    assert out.exit_code == 0, out.output
    rulings = [a for a in brain.attestations if a["kind"] == "review_ruling"]
    assert len(rulings) == 1
    assert rulings[0]["payload"]["finding"] == "F-7-1"
    assert rulings[0]["issuer_identity"] == "operator"


def test_the_reviewer_reads_and_writes_its_ledger_as_itself(tmp_path: Path, monkeypatch) -> None:
    """Under brain credentials an issuer label is admitted only when it is the credential's
    client_id or one of its patterns: the reviewer's token admits `red-rail-reviewer` alone, so
    every call of its pass, the reads included, carries that label — not the rail's default
    `red-rail` (403 on brain_delivery_get at the red-base cutover, 2026-10-12)."""
    from rail.ledger import open_ledger as real_open_ledger
    from rail.reviewer.service import REVIEWER_IDENTITY

    agents = []

    def recording_open_ledger(repo, **kwargs):
        agents.append(kwargs.get("agent"))
        return real_open_ledger(repo, **kwargs)

    monkeypatch.setattr("rail.commands.reviewer.open_ledger", recording_open_ledger)
    _policy_seen(tmp_path, monkeypatch)
    assert agents == [REVIEWER_IDENTITY]


# -- an agent's fix ruling under an operator delegation (decision f38ce645) ----------------

DELEGATION = "d47ac557-7028-47fd-b409-7e22085dc01f"
ACTIVE_DELEGATION = (
    "## Decision: Operator delegation to brain_55 renewed under the permanent delegation rule "
    f"(id:{DELEGATION})\n**Status**: active\n**Created**: 2026-10-13\n"
)
SHA = "0123456789abcdef0123456789abcdef01234567"


def _trusting(config: Path, *ids: str) -> Path:
    """The host's reviewer.yaml naming the delegations agents may rule under."""
    config.write_text(config.read_text() + "delegations: [" + ", ".join(ids) + "]\n")
    return config


def _agent_rule(
    config: Path, monkeypatch, *, decision_text=ACTIVE_DELEGATION, ruling="fix", extra=None
):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("RAIL_ACTOR", "agent:claude-code:rail")
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: False)
    asked = []

    def fetch(decision_id: str, *, agent: str) -> str:
        asked.append((decision_id, agent))
        if isinstance(decision_text, Exception):
            raise decision_text
        return decision_text

    monkeypatch.setattr("rail.reviewer.delegation.fetch_decision", fetch)
    args = [
        "reviewer",
        "rule",
        "--config",
        str(config),
        "--repository",
        "hawkixs/red-alpha",
        "--pr",
        "7",
        "--finding",
        "F-7-1",
        "--as",
        ruling,
        "--decision",
        "fixed by the rename",
    ]
    if extra is None:
        extra = [
            "--delegation",
            DELEGATION,
            "--evidence-sha",
            SHA,
            "--evidence",
            "tests/test_a.py::test_renamed proves it",
        ]
    out = CliRunner().invoke(main, [*args, *extra], input="")
    return out, asked


def test_an_agent_records_a_fix_ruling_under_an_active_delegation(
    tmp_path: Path, monkeypatch
) -> None:
    """An agent, not at a terminal, records a fix ruling that names an active delegation decision
    and carries evidence; the ruling keeps the agent's identity, the decision id and the proof."""
    repo, config = _awaiting_repo(tmp_path)
    _trusting(config, "d47ac557")
    out, asked = _agent_rule(config, monkeypatch)
    assert out.exit_code == 0, out.output
    assert asked == [(DELEGATION, "agent:claude-code:rail")]
    (ruling,) = FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )
    assert ruling.issuer == "agent:claude-code:rail"
    assert ruling.data["ruling"] == "fix"
    assert ruling.data["delegation"] == DELEGATION
    assert ruling.data["evidence"] == {
        "sha": SHA,
        "proof": "tests/test_a.py::test_renamed proves it",
    }


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"ruling": "carry-forward"}, "carry-forward"),
        ({"extra": ["--evidence-sha", SHA, "--evidence", "t"]}, "--delegation"),
        ({"extra": ["--delegation", DELEGATION, "--evidence", "t"]}, "--evidence-sha"),
        ({"extra": ["--delegation", DELEGATION, "--evidence-sha", SHA]}, "--evidence"),
        (
            {"extra": ["--delegation", DELEGATION, "--evidence-sha", "main", "--evidence", "t"]},
            "commit sha",
        ),
        ({"decision_text": ACTIVE_DELEGATION.replace("active", "superseded")}, "not active"),
        (
            {"decision_text": ACTIVE_DELEGATION.replace("Operator delegation", "Pick a lib")},
            "not a delegation",
        ),
        ({"decision_text": "no such decision"}, "not a delegation"),
        (
            {
                "decision_text": ACTIVE_DELEGATION.replace(
                    "Operator delegation to brain_55 renewed",
                    "Operator go: an agent may rule under a delegation decision",
                )
            },
            "not a delegation",
        ),
        ({"decision_text": RuntimeError("brain unreachable")}, "cannot verify"),
        (
            {
                "extra": [
                    "--delegation",
                    DELEGATION,
                    "--evidence-sha",
                    SHA,
                    "--evidence",
                    "curl 127.0.0.1:8765",
                ]
            },
            "literal address",
        ),
    ],
)
def test_an_agent_ruling_is_refused_without_writing(
    tmp_path: Path, monkeypatch, kwargs, needle
) -> None:
    """carry-forward stays the operator's; a fix needs a verified active delegation and evidence."""
    repo, config = _awaiting_repo(tmp_path)
    _trusting(config, "d47ac557")
    out, _ = _agent_rule(config, monkeypatch, **kwargs)
    assert out.exit_code == 2 and needle in out.output, out.output
    assert not FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )


@pytest.mark.parametrize("trusted", [(), ("c5773695",)], ids=["none", "another"])
def test_an_agent_ruling_needs_a_delegation_the_host_trusts(
    tmp_path: Path, monkeypatch, trusted: tuple[str, ...]
) -> None:
    """A title in brain proves nothing: any writer can log "Operator delegation …". The host's
    reviewer.yaml names the delegations agents may rule under; another id is refused before
    brain is even asked, and nothing is written."""
    repo, config = _awaiting_repo(tmp_path)
    if trusted:
        _trusting(config, *trusted)
    out, asked = _agent_rule(config, monkeypatch)
    assert out.exit_code == 2 and "not one the host trusts" in out.output, out.output
    assert asked == []
    assert not FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )


@pytest.mark.parametrize(
    ("decision_text", "needle"),
    [
        # a delegation quoted inside the body of an unrelated decision
        (
            "## Decision: Pick a lib (id:"
            + DELEGATION
            + ")\n**Status**: active\n\n```\n"
            + ACTIVE_DELEGATION
            + "```\n",
            "not a delegation",
        ),
        # an injected status line ahead of the genuine one
        (
            ACTIVE_DELEGATION.replace("**Status**: active", "**Status**: superseded")
            + "**Status**: active\n",
            "not active",
        ),
        # brain answers for another decision than the one named
        (
            ACTIVE_DELEGATION.replace(DELEGATION, "c5773695-bfe6-4177-8f27-471ba5061917"),
            "another decision",
        ),
    ],
)
def test_a_decision_is_read_as_one_record_bound_to_the_named_id(
    tmp_path: Path, monkeypatch, decision_text: str, needle: str
) -> None:
    """The title, the id and the status are the record's own header lines, in order, and the id
    is the one the agent named: a quoted or injected line never authorises (codex, red-rail#96)."""
    repo, config = _awaiting_repo(tmp_path)
    _trusting(config, "d47ac557", "c5773695")
    out, _ = _agent_rule(config, monkeypatch, decision_text=decision_text)
    assert out.exit_code == 2 and needle in out.output, out.output
    assert not FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )


@pytest.mark.parametrize(
    "proof",
    [
        "curl http://[::1]:8765",
        "curl https://user:example-password@api.example.test",
        "see fe80::1 in the log",
    ],
)
def test_evidence_never_publishes_an_address_or_a_url(tmp_path: Path, monkeypatch, proof) -> None:
    repo, config = _awaiting_repo(tmp_path)
    _trusting(config, "d47ac557")
    out, _ = _agent_rule(
        config,
        monkeypatch,
        extra=["--delegation", DELEGATION, "--evidence-sha", SHA, "--evidence", proof],
    )
    assert out.exit_code == 2 and "--evidence" in out.output, out.output
    assert not FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )


def test_a_pytest_node_id_is_valid_evidence(tmp_path: Path, monkeypatch) -> None:
    """`::` in a test id is not an address."""
    repo, config = _awaiting_repo(tmp_path)
    _trusting(config, "d47ac557")
    out, _ = _agent_rule(
        config,
        monkeypatch,
        extra=[
            "--delegation",
            DELEGATION,
            "--evidence-sha",
            SHA,
            "--evidence",
            "tests/unit/test_tables.py::test_depends_on_keys_are_validated",
        ],
    )
    assert out.exit_code == 0, out.output
