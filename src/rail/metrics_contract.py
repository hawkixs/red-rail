"""Strict models and serialization for the published rail metrics contract."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from rail.flow import SEGMENTS, Segment

MAX_BYTES = 32 * 1024
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
Count = Annotated[int, Field(ge=0)]
NonNegative = Annotated[float, Field(ge=0)]
Ratio = Annotated[float, Field(ge=0, le=1)]
Name = Annotated[str, Field(pattern=NAME_PATTERN.pattern)]
RepositorySlug = Annotated[str, Field(pattern="^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=201)]
PositiveCount = Annotated[int, Field(gt=0)]


def _require_utc(value: datetime) -> datetime:
    """Normalize aware zero-offset zones so serialized timestamps always use UTC."""
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)


UtcDatetime = Annotated[
    datetime,
    AfterValidator(_require_utc),
    PlainSerializer(datetime.isoformat, return_type=str),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TooLarge(ValueError):
    """Raised when an encoded answer exceeds the published size budget."""

    def __init__(self, size: int) -> None:
        self.size = size
        super().__init__(f"metrics answer is {size} bytes; maximum is {MAX_BYTES}")


class DeliveryWindowModel(StrictModel):
    deployments: Count
    deployment_frequency_per_week: NonNegative | None
    lead_time_commit_to_deploy_hours: NonNegative | None
    change_failure_rate: Ratio | None
    recovery_time_hours: NonNegative | None
    drill_recovery_time_minutes: NonNegative | None


class DeliveryProject(StrictModel):
    project: Name
    target: Name
    d7: DeliveryWindowModel
    d30: DeliveryWindowModel


class DeliveryBlock(StrictModel):
    version: Literal["rail-delivery/v1"]
    generated_at: UtcDatetime
    windows_days: tuple[Literal[7], Literal[30]]
    projects: tuple[DeliveryProject, ...]

    @model_validator(mode="after")
    def sorted_projects(self) -> DeliveryBlock:
        project_names = tuple(p.project for p in self.projects)
        if project_names != tuple(sorted(project_names)):
            raise ValueError("projects must be sorted by project name")
        return self


class DeliveryWindowV2Model(StrictModel):
    changes: Count
    redeployments: Count
    deployment_frequency_per_week: NonNegative
    lead_time_for_changes_hours: NonNegative | None
    release_to_deploy_hours: NonNegative | None
    change_failure_rate: Ratio | None
    failed_changes: Count
    time_to_restore_minutes: NonNegative | None
    open_incidents: Count
    drill_recovery_time_minutes: NonNegative | None


class DeliveryProjectV2(StrictModel):
    project: Name
    status: Literal["measured", "no_deployment"]
    target: Name | None
    d7: DeliveryWindowV2Model | None
    d30: DeliveryWindowV2Model | None

    @model_validator(mode="after")
    def consistent_deployment_status(self) -> DeliveryProjectV2:
        """A targetless repository has no figures; measured repositories need both windows."""
        absent = self.status == "no_deployment"
        if any((value is None) != absent for value in (self.target, self.d7, self.d30)):
            raise ValueError("deployment status must match target and both windows")
        return self


class DeliveryBlockV2(StrictModel):
    version: Literal["rail-delivery/v2"]
    generated_at: UtcDatetime
    windows_days: tuple[Literal[7], Literal[30]]
    projects: tuple[DeliveryProjectV2, ...]

    @model_validator(mode="after")
    def sorted_projects(self) -> DeliveryBlockV2:
        project_names = tuple(p.project for p in self.projects)
        if project_names != tuple(sorted(project_names)):
            raise ValueError("projects must be sorted by project name")
        return self


class AutonomyWindow(StrictModel):
    window_start: UtcDatetime
    releases: Count
    operator_gestures: Count
    operator_gestures_per_release: NonNegative | None
    agent_gesture_ratio: Ratio | None
    merged_prs: Count
    reviewed_prs: Count
    rulings_per_reviewed_pr: NonNegative | None
    agent_rulings: Count
    review_rounds_median: NonNegative | None
    review_rounds_p90: NonNegative | None
    first_pass_approval_ratio: Ratio | None
    awaiting_ruling_passes: Count
    ready_to_merged_hours_median: NonNegative | None
    rollbacks: Count
    reverts: Count
    incidents: Count


class Coverage(StrictModel):
    expected: Count
    read: Count
    missing: tuple[Name, ...]


class AutonomyTotal(StrictModel):
    d7: AutonomyWindow
    d30: AutonomyWindow


class AutonomyProject(StrictModel):
    project: Name
    d30: AutonomyWindow


class NotMeasured(StrictModel):
    what: str
    why: str


class AutonomyBlock(StrictModel):
    version: Literal["rail-autonomy/v1"]
    generated_at: UtcDatetime
    experimental: Literal[True]
    windows_days: tuple[Literal[7], Literal[30]]
    since: UtcDatetime
    coverage: Coverage
    total: AutonomyTotal | None
    projects: tuple[AutonomyProject, ...]
    not_measured: tuple[NotMeasured, ...]

    @model_validator(mode="after")
    def consistent_coverage_and_order(self) -> AutonomyBlock:
        if bool(self.coverage.missing) != (self.total is None):
            raise ValueError("total must be null exactly when coverage has missing projects")
        project_names = tuple(p.project for p in self.projects)
        if project_names != tuple(sorted(project_names)):
            raise ValueError("projects must be sorted by project name")
        return self


class _FlowModel(StrictModel):
    """Refuse nonfinite measurements without changing the existing block contracts."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class FlowPercentiles(_FlowModel):
    """An empty sample has no percentiles; a measured sample requires both."""

    median: NonNegative | None
    p90: NonNegative | None
    n: Count

    @model_validator(mode="after")
    def consistent_sample(self) -> FlowPercentiles:
        """Keep absence distinct from a measured zero."""
        if any((value is None) != (self.n == 0) for value in (self.median, self.p90)):
            raise ValueError("percentiles must be null exactly when n is zero")
        return self


class FlowSegments(_FlowModel):
    """Publish every segment in minutes, retaining nulls for unknown evidence."""

    flow: NonNegative | None
    ci_start: NonNegative | None
    ci: NonNegative | None
    ci_longest: NonNegative | None
    review_start: NonNegative | None
    review: NonNegative | None
    judge_wait: NonNegative | None
    judge: NonNegative | None
    idle: NonNegative | None
    holder: NonNegative | None


class FlowRepository(_FlowModel):
    """Keep distributions separate by repository and by segment sample."""

    repository: RepositorySlug
    merged_prs: Count
    carry_rate: Ratio | None
    segments: dict[Segment, FlowPercentiles]

    @model_validator(mode="after")
    def complete_segments(self) -> FlowRepository:
        """Require all ten keys so consumers can distinguish null from omission."""
        if set(self.segments) != set(SEGMENTS):
            raise ValueError("segments must contain exactly all ten segment keys")
        return self


class FlowRecent(_FlowModel):
    """Expose only the bounded public identifiers of a final-head timeline."""

    repository: RepositorySlug
    number: PositiveCount
    head_sha: Annotated[str, Field(pattern=r"\A[0-9a-f]{12}\z")]
    merged_at: UtcDatetime
    segments: FlowSegments
    rounds: Count | None
    carried: bool | None
    judge_carried: bool | None
    judge_provider: Annotated[str, Field(pattern=r"\A[A-Za-z0-9_-]{1,32}\z")] | None
    holder_approx: bool | None


class FlowCoverage(_FlowModel):
    """Count unread evidence without exposing source diagnostics or private inputs."""

    missing: tuple[RepositorySlug, ...]
    unread_prs: dict[RepositorySlug, Count]
    unread_segments: dict[RepositorySlug, dict[Segment | Literal["ledger"], Count]]


class FlowBlock(_FlowModel):
    """Describe one flow window, including empty read repositories and partial evidence."""

    version: Literal["rail-flow/v1"]
    generated_at: UtcDatetime
    window_days: PositiveCount
    window_start: UtcDatetime
    repositories: tuple[FlowRepository, ...]
    recent: tuple[FlowRecent, ...]
    coverage: FlowCoverage

    @model_validator(mode="after")
    def consistent_population_and_order(self) -> FlowBlock:
        """Require deterministic ordering and keep coverage within the read population."""
        names = tuple(repository.repository for repository in self.repositories)
        population = set(names)
        if names != tuple(sorted(population)):
            raise ValueError("repositories must be unique and sorted by repository")
        missing = self.coverage.missing
        if missing != tuple(sorted(set(missing))) or population.intersection(missing):
            raise ValueError("missing must be unique, sorted and disjoint from repositories")
        if not (
            self.coverage.unread_prs.keys() <= population
            and self.coverage.unread_segments.keys() <= population
        ):
            raise ValueError("coverage maps must name read repositories")
        identities = {(entry.repository, entry.number) for entry in self.recent}
        if len(self.recent) > 20 or len(identities) != len(self.recent):
            raise ValueError("recent must contain at most twenty distinct pull requests")
        if any(entry.repository not in population for entry in self.recent):
            raise ValueError("recent repositories must belong to the read population")
        ties = sorted(self.recent, key=lambda entry: (entry.repository, entry.number))
        ordered = tuple(sorted(ties, key=lambda entry: entry.merged_at, reverse=True))
        if self.recent != ordered:
            raise ValueError(
                "recent must sort by merge descending, then repository and PR ascending"
            )
        return self


class Answer(StrictModel):
    delivery: DeliveryBlock | None = None
    autonomy: AutonomyBlock | None = None
    delivery_v2: DeliveryBlockV2 | None = None
    flow: FlowBlock | None = None


def encode(answer: Answer) -> bytes:
    """Serialize compactly, omitting absent blocks and enforcing the whole-answer cap."""
    data = answer.model_dump(mode="json")
    payload = json.dumps(
        {key: value for key, value in data.items() if value is not None},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise TooLarge(len(payload))
    return payload
