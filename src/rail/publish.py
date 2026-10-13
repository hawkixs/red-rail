"""Read the reviewer's repositories and publish bounded metrics through an atomic replace."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from yaml import YAMLError

from rail.autonomy import (
    SINCE,
    ProjectFacts,
    Samples,
    active,
    merge,
    window_samples,
    window_start,
    window_values,
)
from rail.brain.settings import DEFAULT_REVIEWER_TOKEN_FILE
from rail.ledger import Ledger, LedgerError
from rail.metrics import delivery_window
from rail.metrics_contract import (
    Answer,
    AutonomyBlock,
    AutonomyProject,
    AutonomyTotal,
    AutonomyWindow,
    Coverage,
    DeliveryBlock,
    DeliveryProject,
    DeliveryWindowModel,
    NotMeasured,
    encode,
)
from rail.model import Tier, load_rail_config
from rail.reviewer.config import ReviewerConfig
from rail.reviewer.github import GitHubApp, GitHubError
from rail.reviewer.service import REVIEWER_IDENTITY

NOT_MEASURED = (
    NotMeasured(what="operator_questions_and_decisions", why="not recorded anywhere yet (T1)"),
    NotMeasured(what="human_active_and_waiting_time", why="no source"),
    NotMeasured(
        what="justified_vs_avoidable_classification",
        why="classification not decided (T1, Q1)",
    ),
)


def default_output() -> Path:
    """Resolve the user state directory at invocation time so the environment stays effective."""
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state else Path("~/.local/state").expanduser()
    return root / "red-rail" / "metrics.json"


def collect(
    config: ReviewerConfig,
    *,
    now: datetime,
    clock: Callable[[], datetime],
    open_ledger: Callable[..., Ledger],
    github: GitHubApp,
) -> Answer:
    """Read failures make coverage partial; computed contract violations fail the entire run."""
    missing: list[str] = []
    projects: list[AutonomyProject] = []
    delivery_projects: list[DeliveryProject] = []
    samples: dict[int, list[Samples]] = {7: [], 30: []}
    for repository in config.repositories:
        try:
            cfg = load_rail_config(repository.path)
        except (OSError, UnicodeDecodeError, ValidationError, YAMLError):
            tail = repository.slug.rsplit("/", 1)[-1].lower()
            name = re.sub(r"[^a-z0-9]+", "-", tail).strip("-")[:64].rstrip("-")
            missing.append(name or "unknown")
            continue
        try:
            ledger = open_ledger(
                repository.path,
                agent=REVIEWER_IDENTITY,
                token_file=DEFAULT_REVIEWER_TOKEN_FILE,
            )
            records = ledger.list(cfg.project)
            branch = github.default_branch(repository.slug)
            merged = github.merged_pulls(repository.slug, base=branch, since=SINCE)
            commits = github.commits_since(repository.slug, branch=branch, since=SINCE)
            facts = ProjectFacts(
                project=cfg.project,
                repository=repository.slug,
                records=records,
                merged=merged,
                reverts=[at for at, message in commits if message.startswith('Revert "')],
            )
            observed = {days: window_samples(facts, now=now, days=days) for days in (7, 30)}
            delivery = (
                {
                    days: delivery_window(
                        ledger, cfg.project, repository.path, now=now, window_days=days
                    )
                    for days in (7, 30)
                }
                if cfg.tier is Tier.PROD and cfg.deploy is not None
                else None
            )
        except (LedgerError, GitHubError, FileNotFoundError):
            missing.append(cfg.project)
            continue

        # Validation is outside the read guards: invalid computed data must never be published.
        if active(observed[30]):
            projects.append(
                AutonomyProject(
                    project=cfg.project,
                    d30=AutonomyWindow.model_validate(
                        window_values(observed[30], start=window_start(now, 30))
                    ),
                )
            )
        if delivery is not None:
            assert cfg.deploy is not None
            delivery_projects.append(
                DeliveryProject(
                    project=cfg.project,
                    target=cfg.deploy.target.value,
                    d7=DeliveryWindowModel.model_validate(asdict(delivery[7])),
                    d30=DeliveryWindowModel.model_validate(asdict(delivery[30])),
                )
            )
        for days in (7, 30):
            samples[days].append(observed[days])

    if not samples[30]:
        raise ValueError("no repository could be read; metrics were not published")
    total = (
        AutonomyTotal(
            d7=AutonomyWindow.model_validate(
                window_values(merge(*samples[7]), start=window_start(now, 7))
            ),
            d30=AutonomyWindow.model_validate(
                window_values(merge(*samples[30]), start=window_start(now, 30))
            ),
        )
        if not missing
        else None
    )
    generated_at = clock()
    return Answer(
        delivery=DeliveryBlock(
            version="rail-delivery/v1",
            generated_at=generated_at,
            windows_days=(7, 30),
            projects=tuple(sorted(delivery_projects, key=lambda project: project.project)),
        ),
        autonomy=AutonomyBlock(
            version="rail-autonomy/v1",
            generated_at=generated_at,
            experimental=True,
            windows_days=(7, 30),
            since=SINCE,
            coverage=Coverage(
                expected=len(config.repositories), read=len(samples[30]), missing=tuple(missing)
            ),
            total=total,
            projects=tuple(sorted(projects, key=lambda project: project.project)),
            not_measured=NOT_MEASURED,
        ),
    )


def write(answer: Answer, out: Path) -> int:
    """Encode before touching the old file, then replace it only after a durable complete write."""
    payload = encode(answer)
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=out.parent, prefix=".metrics-", delete=False) as file:
            temporary = Path(file.name)
            file.write(payload)
            file.flush()
            os.fchmod(file.fileno(), 0o644)
            os.fsync(file.fileno())
        os.replace(temporary, out)
        directory = os.open(out.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(payload)
