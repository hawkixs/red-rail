"""Read the reviewer's repositories and publish bounded metrics through an atomic replace."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from yaml import YAMLError, safe_load

from rail import gitrepo
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
from rail.flow_cache import FlowCache, default_cache_root
from rail.flow_publish import collect_flow, flow_block
from rail.ledger import Ledger, LedgerError, Record
from rail.ledger.committed import MAIN_REF, CommittedLedger
from rail.metrics import delivery_window, delivery_window_v2
from rail.metrics_contract import (
    MAX_BYTES,
    Answer,
    AutonomyBlock,
    AutonomyProject,
    AutonomyTotal,
    AutonomyWindow,
    Coverage,
    DeliveryBlock,
    DeliveryBlockV2,
    DeliveryProject,
    DeliveryProjectV2,
    DeliveryWindowModel,
    DeliveryWindowV2Model,
    NotMeasured,
    TooLarge,
    encode,
)
from rail.model import LedgerBackend, RailConfig, Tier, load_rail_config
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
    window_days: int = 30,
    flow_cache: FlowCache | None = None,
) -> Answer:
    """Read failures make coverage partial; computed contract violations fail the entire run."""
    missing: list[str] = []
    projects: list[AutonomyProject] = []
    delivery_projects: list[DeliveryProject] = []
    delivery_projects_v2: list[DeliveryProjectV2] = []
    samples: dict[int, list[Samples]] = {7: [], 30: []}
    snapshots: dict[str, list[Record] | None] = {}
    for repository in config.repositories:
        snapshots[repository.slug] = None
        tail = repository.slug.rsplit("/", 1)[-1].lower()
        name = re.sub(r"[^a-z0-9]+", "-", tail).strip("-")[:64].rstrip("-") or "unknown"
        step = "fetch"
        try:
            if not gitrepo.fetch_main(repository.path):
                raise OSError("origin/main fetch failed")
            step = "manifest"
            manifest = gitrepo.file_at(repository.path, MAIN_REF, "rail.yaml")
            if manifest is None:
                raise OSError("origin/main manifest is unreadable")
            cfg = RailConfig.model_validate(safe_load(manifest) or {})
            committed = cfg.ledger is LedgerBackend.FILE
            if not committed:
                cfg = load_rail_config(repository.path)
        except (OSError, UnicodeDecodeError, ValidationError, YAMLError) as error:
            missing.append(name)
            print(f"missing {name}: {step} {type(error).__name__}", file=sys.stderr)
            continue
        try:
            step = "ledger"
            ledger = (
                CommittedLedger(repository.path)
                if committed
                else open_ledger(
                    repository.path,
                    agent=REVIEWER_IDENTITY,
                    token_file=DEFAULT_REVIEWER_TOKEN_FILE,
                )
            )
            records = ledger.list(cfg.project)
            snapshots[repository.slug] = records
            step = "github"
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
            step = "ledger"
            delivery = (
                {
                    days: delivery_window(
                        ledger,
                        cfg.project,
                        repository.path,
                        now=now,
                        window_days=days,
                        config=cfg,
                    )
                    for days in (7, 30)
                }
                if cfg.tier is Tier.PROD and cfg.deploy is not None
                else None
            )
            delivery_v2 = (
                {
                    days: delivery_window_v2(
                        ledger,
                        cfg.project,
                        repository.path,
                        now=now,
                        window_days=days,
                        config=cfg,
                    )
                    for days in (7, 30)
                }
                if cfg.deploy is not None
                else None
            )
        except (LedgerError, GitHubError, OSError) as error:
            # past the manifest, coverage names the project as v1 always did
            missing.append(cfg.project)
            if step == "ledger":
                snapshots[repository.slug] = None
            print(f"missing {cfg.project}: {step} {type(error).__name__}", file=sys.stderr)
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
        delivery_projects_v2.append(
            DeliveryProjectV2(
                project=cfg.project,
                status="measured" if cfg.deploy is not None else "no_deployment",
                target=cfg.deploy.target.value if cfg.deploy is not None else None,
                d7=DeliveryWindowV2Model.model_validate(asdict(delivery_v2[7]))
                if delivery_v2 is not None
                else None,
                d30=DeliveryWindowV2Model.model_validate(asdict(delivery_v2[30]))
                if delivery_v2 is not None
                else None,
            )
        )
        for days in (7, 30):
            samples[days].append(observed[days])

    total = (
        AutonomyTotal(
            d7=AutonomyWindow.model_validate(
                window_values(merge(*samples[7]), start=window_start(now, 7))
            ),
            d30=AutonomyWindow.model_validate(
                window_values(merge(*samples[30]), start=window_start(now, 30))
            ),
        )
        if samples[30] and not missing
        else None
    )
    flow = None
    step = "pulls"
    try:
        collection = collect_flow(
            config,
            now=now,
            window_days=window_days,
            github=github,
            cache=flow_cache if flow_cache is not None else FlowCache(default_cache_root()),
            records_for=lambda repository: snapshots[repository.slug],
        )
        step = "checks"
        flow = flow_block(collection, generated_at=now, now=now, window_days=window_days)
    except Exception as error:
        # Flow is optional; ordinary assembly defects cannot suppress the existing blocks.
        print("flow", step, type(error).__name__, file=sys.stderr)
    if not samples[30] and (flow is None or not flow.repositories):
        raise ValueError("no repository could be read; metrics were not published")
    answer = Answer(
        delivery=DeliveryBlock(
            version="rail-delivery/v1",
            generated_at=now,
            windows_days=(7, 30),
            projects=tuple(sorted(delivery_projects, key=lambda project: project.project)),
        )
        if samples[30]
        else None,
        autonomy=AutonomyBlock(
            version="rail-autonomy/v1",
            generated_at=now,
            experimental=True,
            windows_days=(7, 30),
            since=SINCE,
            coverage=Coverage(
                expected=len(config.repositories), read=len(samples[30]), missing=tuple(missing)
            ),
            total=total,
            projects=tuple(sorted(projects, key=lambda project: project.project)),
            not_measured=NOT_MEASURED,
        )
        if samples[30]
        else None,
        delivery_v2=DeliveryBlockV2(
            version="rail-delivery/v2",
            generated_at=now,
            windows_days=(7, 30),
            projects=tuple(sorted(delivery_projects_v2, key=lambda project: project.project)),
        )
        if samples[30]
        else None,
        flow=flow,
    )
    generated_at = clock()
    return answer.model_copy(
        update={
            name: block.model_copy(update={"generated_at": generated_at})
            for name in ("delivery", "autonomy", "delivery_v2", "flow")
            if (block := getattr(answer, name)) is not None
        }
    )


def write(answer: Answer, out: Path, *, max_bytes: int = 16 * 1024) -> int:
    """Encode before touching the old file, then replace it only after a durable complete write."""
    if not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError(f"metrics budget must be between 1 and {MAX_BYTES}")
    base = answer.model_copy(update={"flow": None})
    # Revalidation also catches invalid non-flow values introduced through model_copy.
    base = Answer.model_validate_json(_compact(base), strict=True)
    try:
        payload = encode(base)
    except TooLarge as error:
        raise ValueError(f"metrics answer is {error.size} bytes; budget is {max_bytes}") from None
    if len(payload) > max_bytes:
        raise ValueError(f"metrics answer is {len(payload)} bytes; budget is {max_bytes}")
    if answer.flow is not None:
        try:
            candidate = _compact(answer)
            if len(candidate) > max_bytes:
                print("flow_size", len(payload), len(candidate), max_bytes, file=sys.stderr)
            else:
                payload = encode(Answer.model_validate_json(candidate, strict=True))
        except Exception as error:
            print("flow", "checks", type(error).__name__, file=sys.stderr)
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


def _compact(answer: Answer) -> bytes:
    """Measure the complete candidate before applying the consumer or contract ceiling."""
    return json.dumps(
        {key: value for key, value in answer.model_dump(mode="json").items() if value is not None},
        separators=(",", ":"),
    ).encode("utf-8")
