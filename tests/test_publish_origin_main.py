"""Publication reads main without moving an old reviewer checkout."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from rail import gitrepo, publish
from rail.brain.settings import DEFAULT_REVIEWER_TOKEN_FILE
from rail.ledger import (
    RECEIPTS_DIR,
    AttestationKind,
    Contract,
    Deliverable,
    Ledger,
    LedgerError,
    PullRequestRef,
    RecordKind,
    open_ledger,
)
from rail.ledger.committed import MAIN_REF, CommittedLedger
from rail.ledger.file import FileLedger
from rail.metrics_contract import Answer
from rail.model import load_rail_config
from rail.reviewer.config import RepositoryConfig, ReviewerConfig
from tests.helpers import FIXED_DATE, git
from tests.test_cli_metrics_publish import NOW, Clock, ReadGitHub, ReadLedger, manifest, released


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "--bare", "-b", "main")
    writer = tmp_path / "writer"
    manifest(writer, "red-probe")
    git(writer, "init", "-b", "main")
    git(writer, "config", "commit.gpgsign", "false")
    git(writer, "remote", "add", "origin", str(origin))
    git(writer, "add", "rail.yaml")
    git(writer, "commit", "-m", "Initial manifest")
    git(writer, "push", "origin", "main")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(origin), str(checkout))
    # A fresh commit and its receipts exist only on main, beyond the reviewer's HEAD.
    git(writer, "commit", "--allow-empty", "-m", "Released change")
    sha = git(writer, "rev-parse", "HEAD")
    ledger = FileLedger(writer / RECEIPTS_DIR, clock=lambda: NOW)
    ledger.mirror(released("red-probe"))
    ledger.attest(
        "red-probe",
        AttestationKind.DEPLOYED,
        {"sha": sha, "digest": "sha256:a", "target": "private-compose"},
        issuer="operator",
        idempotency_key="deployment:1",
    )
    git(writer, "add", RECEIPTS_DIR)
    git(writer, "commit", "-m", "Record release and deployment")
    git(writer, "push", "origin", "main")
    return origin, writer, checkout


def collect_paths(*paths: Path, opener: Callable[..., Ledger] = open_ledger) -> Answer:
    clock = Clock()
    return publish.collect(
        ReviewerConfig(
            app_id=1,
            installation_id=2,
            private_key_file=paths[0].parent / "unused.pem",
            repositories=[
                RepositoryConfig(slug=f"example/red-{index}", path=path)
                for index, path in enumerate(paths)
            ],
        ),
        now=NOW,
        clock=clock,
        open_ledger=opener,
        github=ReadGitHub(clock),
    )


def test_publish_counts_deployment_on_main_beyond_checkout(
    history: tuple[Path, Path, Path],
) -> None:
    _, _, checkout = history
    old_head = git(checkout, "rev-parse", "HEAD")
    old_manifest = (checkout / "rail.yaml").read_bytes()
    answer = collect_paths(checkout)
    assert answer.delivery is not None
    assert answer.delivery.projects[0].d7.deployments == 1
    assert answer.delivery.projects[0].d30.deployments == 1
    assert answer.delivery.projects[0].d7.lead_time_commit_to_deploy_hours == round(
        (NOW - datetime.fromisoformat(FIXED_DATE)).total_seconds() / 3600, 2
    )
    assert git(checkout, "rev-parse", "HEAD") == old_head
    assert (checkout / "rail.yaml").read_bytes() == old_manifest
    assert not (checkout / RECEIPTS_DIR).exists()


def test_v2_reads_commit_range_on_main_beyond_checkout(
    history: tuple[Path, Path, Path],
) -> None:
    _, writer, checkout = history
    old_head = git(checkout, "rev-parse", "HEAD")
    previous_sha = git(writer, "rev-parse", "HEAD")
    FileLedger(writer / RECEIPTS_DIR, clock=lambda: NOW.replace(day=10)).attest(
        "red-probe",
        AttestationKind.DEPLOYED,
        {"sha": previous_sha, "digest": "sha256:previous", "target": "private-compose"},
        issuer="operator",
        idempotency_key="deployment:previous",
    )
    git(writer, "commit", "--allow-empty", "-m", "Included change beyond checkout")
    current_sha = git(writer, "rev-parse", "HEAD")
    FileLedger(writer / RECEIPTS_DIR, clock=lambda: NOW.replace(day=12)).attest(
        "red-probe",
        AttestationKind.DEPLOYED,
        {"sha": current_sha, "digest": "sha256:current", "target": "private-compose"},
        issuer="operator",
        idempotency_key="deployment:current",
    )
    push_changes(writer)
    answer = collect_paths(checkout)
    assert answer.delivery_v2 is not None
    measured = answer.delivery_v2.projects[0]
    assert measured.d7 is not None
    assert measured.d7.changes == 3
    assert measured.d7.lead_time_for_changes_hours == round(
        (NOW.replace(day=12) - datetime.fromisoformat(FIXED_DATE)).total_seconds() / 3600, 2
    )
    assert git(checkout, "rev-parse", "HEAD") == old_head
    assert not (checkout / RECEIPTS_DIR).exists()


@pytest.fixture
def healthy(tmp_path: Path) -> Path:
    writer = tmp_path / "healthy-writer"
    manifest(writer, "red-healthy", tier="dev")
    git(writer, "init", "-b", "main")
    git(writer, "config", "commit.gpgsign", "false")
    git(writer, "add", "rail.yaml")
    git(writer, "commit", "-m", "Healthy manifest")
    origin = tmp_path / "healthy.git"
    git(tmp_path, "clone", "--bare", str(writer), str(origin))
    checkout = tmp_path / "healthy-checkout"
    git(tmp_path, "clone", str(origin), str(checkout))
    return checkout


def push_changes(writer: Path) -> None:
    git(writer, "add", "-A")
    git(writer, "commit", "-m", "Update main evidence")
    git(writer, "push", "origin", "main")


def test_publish_uses_main_manifest_for_delivery(history: tuple[Path, Path, Path]) -> None:
    _, writer, checkout = history
    path = writer / "rail.yaml"
    path.write_text(path.read_text().replace("tier: prod", "tier: dev"))
    push_changes(writer)
    assert load_rail_config(checkout).tier.value == "prod"
    answer = collect_paths(checkout)
    assert answer.delivery is not None and not answer.delivery.projects
    assert answer.autonomy is not None and answer.autonomy.coverage.read == 1


def test_publish_uses_main_target_for_delivery(history: tuple[Path, Path, Path]) -> None:
    _, writer, checkout = history
    path = checkout / "rail.yaml"
    path.write_text(path.read_text().replace("private-compose", "vps-traefik"))
    answer = collect_paths(checkout)
    assert answer.delivery is not None
    assert answer.delivery.projects[0].target == "private-compose"
    assert answer.delivery.projects[0].d7.deployments == 1


def test_untracked_verdict_is_read_alongside_main_receipts(
    history: tuple[Path, Path, Path],
) -> None:
    _, _, checkout = history
    verdict = FileLedger(checkout / RECEIPTS_DIR, clock=lambda: NOW).attest(
        "red-probe",
        AttestationKind.REVIEW_VERDICT,
        {"sha": git(checkout, "rev-parse", "HEAD"), "independent": True, "verdict": "approve"},
        issuer="red-rail-reviewer",
        idempotency_key="review:1",
    )
    collect_paths(checkout)
    ledger = CommittedLedger(checkout)
    assert ledger.list("red-probe", attestation=AttestationKind.REVIEW_VERDICT) == [verdict]
    assert ledger.get("red-probe", verdict.digest) == verdict
    assert ledger.get("red-other", verdict.digest) is None
    assert len(ledger.list("red-probe", kind=RecordKind.ATTESTATION)) == 3
    assert "?? docs/" in git(checkout, "status", "--porcelain")


def test_main_and_local_duplicate_counts_once(history: tuple[Path, Path, Path]) -> None:
    _, writer, checkout = history
    local = FileLedger(checkout / RECEIPTS_DIR)
    for record in FileLedger(writer / RECEIPTS_DIR).list("red-probe"):
        local.mirror(record)
    answer = collect_paths(checkout)
    assert answer.delivery is not None and answer.delivery.projects[0].d7.deployments == 1
    assert len(CommittedLedger(checkout).list("red-probe")) == 2


def test_fetch_failure_is_missing_despite_cached_main(
    history: tuple[Path, Path, Path], healthy: Path
) -> None:
    _, _, checkout = history
    assert gitrepo.fetch_main(checkout)
    git(checkout, "remote", "set-url", "origin", str(checkout.parent / "absent.git"))
    answer = collect_paths(checkout, healthy)
    assert answer.autonomy is not None and answer.delivery is not None
    assert answer.autonomy.coverage.read == 1
    assert answer.autonomy.coverage.missing == ("red-0",)
    assert answer.autonomy.total is None
    assert not answer.delivery.projects


@pytest.mark.parametrize("content", [b"rail: [\n", b"rail: 1\nproject: invalid\n", b"\xff", None])
def test_invalid_main_manifest_is_missing(
    history: tuple[Path, Path, Path], healthy: Path, content: bytes | None
) -> None:
    _, writer, checkout = history
    path = writer / "rail.yaml"
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)
    push_changes(writer)
    answer = collect_paths(checkout, healthy)
    assert answer.autonomy is not None and answer.delivery is not None
    assert answer.autonomy.coverage.missing == ("red-0",)
    assert answer.autonomy.coverage.read == 1
    assert not answer.delivery.projects


@pytest.mark.parametrize("source", ["main", "local"])
@pytest.mark.parametrize("corruption", ["digest", "json", "utf8"])
def test_unreadable_receipt_is_missing(
    history: tuple[Path, Path, Path], healthy: Path, source: str, corruption: str
) -> None:
    _, writer, checkout = history
    record = released("red-probe")
    ledger = FileLedger((writer if source == "main" else checkout) / RECEIPTS_DIR)
    ledger.mirror(record)
    path = ledger.path_of(record)
    if corruption == "digest":
        path.write_text(path.read_text().replace('"operator"', '"changed"'))
    elif corruption == "json":
        path.write_text("{")
    else:
        path.write_bytes(b"\xff")
    if source == "main":
        push_changes(writer)
    answer = collect_paths(checkout, healthy)
    assert answer.autonomy is not None and answer.delivery is not None
    assert answer.autonomy.coverage.missing == ("red-probe",)
    assert answer.autonomy.coverage.read == 1
    assert not answer.delivery.projects


def test_brain_uses_working_manifest_and_reviewer_credentials(
    history: tuple[Path, Path, Path],
) -> None:
    _, writer, checkout = history
    path = writer / "rail.yaml"
    path.write_text(
        path.read_text() + "ledger: brain\nticket: 00000000-0000-0000-0000-000000000001\n"
    )
    push_changes(writer)
    local_path = checkout / "rail.yaml"
    local_path.write_text(
        path.read_text().replace("red-probe", "red-local").replace("tier: prod", "tier: dev")
    )
    calls: list[Path] = []

    def opener(repo: Path, *, agent: str, token_file: str) -> Ledger:
        assert repo == checkout
        assert agent == "red-rail-reviewer" and token_file == DEFAULT_REVIEWER_TOKEN_FILE
        assert load_rail_config(repo).project == "red-local"
        calls.append(repo)
        return ReadLedger([released("red-local")])

    answer = collect_paths(checkout, opener=opener)
    assert calls == [checkout]
    assert answer.autonomy is not None and answer.delivery is not None
    assert answer.autonomy.projects[0].project == "red-local"
    assert not answer.delivery.projects


@pytest.mark.parametrize("method", ["contract_set", "bind", "attest", "accept", "mirror"])
def test_committed_ledger_refuses_writes(history: tuple[Path, Path, Path], method: str) -> None:
    _, _, checkout = history
    assert gitrepo.fetch_main(checkout)
    ledger = CommittedLedger(checkout)
    record = released("red-probe")
    with pytest.raises(LedgerError, match="read-only"):
        if method == "mirror":
            ledger.mirror(record)
        elif method == "accept":
            ledger.accept("red-probe", rationale="done", issuer="operator")
        elif method == "attest":
            ledger.attest(
                "red-probe", AttestationKind.RELEASED, {}, issuer="operator", idempotency_key="r"
            )
        elif method == "bind":
            ledger.bind(
                "red-probe",
                PullRequestRef(repository="example/red-probe", number=1, head_sha="a" * 40),
                issuer="operator",
                idempotency_key="b",
            )
        else:
            ledger.contract_set(
                "red-probe",
                Contract(
                    objective="test",
                    deliverables=[
                        Deliverable(
                            key="main",
                            repository="example/red-probe",
                            no_checks_reason="fixture: no check declared",
                        )
                    ],
                ),
                reason="test",
                issuer="operator",
                idempotency_key="c",
            )
    assert not (checkout / RECEIPTS_DIR).exists()


def test_fully_qualified_main_ignores_local_branch(history: tuple[Path, Path, Path]) -> None:
    _, _, checkout = history
    git(checkout, "branch", "origin/main", "HEAD")
    answer = collect_paths(checkout)
    assert answer.delivery is not None and answer.delivery.projects[0].d7.deployments == 1
    assert gitrepo.files_at(checkout, "origin/main", RECEIPTS_DIR) == {}
    assert len(gitrepo.files_at(checkout, MAIN_REF, RECEIPTS_DIR) or {}) == 2
