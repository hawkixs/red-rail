"""`rail release` (stage 7, spec §6 step 6): tag + immutable artefact + attestation, from the
host. The artefact is an OCI image pushed to the project's repository (tier default
`ghcr.io/hawkixs/<project>`, decision 8faab5a3) and named by its manifest digest; the tag
`v<version>` is annotated with the changelog and pushed to both remotes. Every subprocess
goes through `run` so the flow is testable without docker, gh or a network."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rail import gitrepo
from rail.ledger import AttestationKind, Ledger, Record, idempotency_key_for, open_ledger
from rail.model import Tier, load_rail_config
from rail.policy import parameter

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
RECEIPTS = "docs/receipts/"
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ReleaseError(Exception):
    """A precondition failed or a step failed; the message says what was done and what not to do."""


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    project: str
    version: str
    sha: str
    image_repository: str
    platform: str
    previous_tag: str | None
    changelog: tuple[str, ...]

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def image_tag(self) -> str:
        return f"{self.image_repository}:{self.version}"

    @property
    def registry(self) -> str:
        return self.image_repository.split("/", 1)[0]

    def steps(self) -> list[str]:
        """The commands `rail release --plan` prints, in order."""
        lines = []
        if self.registry == "ghcr.io":
            lines.append(
                f"gh auth token | docker login {self.registry} "
                f"-u {self.image_repository.split('/')[1]} --password-stdin"
            )
        lines += [
            f"docker build --platform {self.platform} --build-arg VERSION={self.version} "
            f"--build-arg GIT_SHA={self.sha} --tag {self.image_tag} .",
            f"docker push {self.image_tag}",
            f"docker image inspect {self.image_tag} --format '{{{{json .RepoDigests}}}}'",
            f"git tag -a {self.tag} -F - {self.sha}  # message: {len(self.changelog)} "
            "changelog line(s)",
            f"git push origin refs/tags/{self.tag}",
            f"git push gitlab refs/tags/{self.tag}",
            f"rail attest released --data version={self.version} --data digest=<digest> …",
        ]
        return lines


@dataclass(frozen=True, slots=True)
class ReleaseOutcome:
    plan: ReleasePlan
    digest: str
    record: Record


def _run(
    args: list[str], *, run: Runner, cwd: Path | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "check": False}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if stdin is not None:
        kwargs["input"] = stdin
    try:
        return run(args, **kwargs)
    except (FileNotFoundError, OSError) as exc:
        raise ReleaseError(f"{args[0]} is not available on this host: {exc}") from exc


def _ok(
    args: list[str], *, run: Runner, what: str, cwd: Path | None = None, stdin: str | None = None
) -> str:
    done = _run(args, run=run, cwd=cwd, stdin=stdin)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise ReleaseError(f"{what} failed (exit {done.returncode}): {detail}")
    return done.stdout


def preflight(repo: Path, version: str, *, ledger: Ledger, run: Runner) -> ReleasePlan:
    """Everything measured before anything is built: the tier, the branch, a tree clean
    outside the ledger mirrors, HEAD published, the tag free, an integration on history."""
    if not SEMVER.match(version):
        raise ReleaseError(f"{version!r} is not a semantic version (X.Y.Z)")
    cfg = load_rail_config(repo)
    if cfg.tier is not Tier.PROD:
        raise ReleaseError(f"release is a prod stage; {cfg.project} declares tier {cfg.tier.value}")
    branch = _ok(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], run=run, cwd=repo, what="git rev-parse"
    ).strip()
    if branch != "main":
        raise ReleaseError(f"release from main, not {branch}")
    status = _ok(["git", "status", "--porcelain"], run=run, cwd=repo, what="git status")
    dirty = [
        line
        for line in status.splitlines()
        # `R  old -> new`: both names must be mirrors for the line to be ignored
        if not all(path.startswith(RECEIPTS) for path in line[3:].split(" -> "))
    ]
    if dirty:
        raise ReleaseError(
            "the working tree has changes outside docs/receipts/: commit or stash them"
        )
    _ok(["git", "fetch", "-q", "origin", "main"], run=run, cwd=repo, what="git fetch origin main")
    head = _ok(["git", "rev-parse", "HEAD"], run=run, cwd=repo, what="git rev-parse HEAD").strip()
    upstream = _ok(
        ["git", "rev-parse", "origin/main"], run=run, cwd=repo, what="git rev-parse origin/main"
    ).strip()
    if head != upstream:
        raise ReleaseError(
            f"HEAD {head[:12]} is not origin/main {upstream[:12]}: push or pull first"
        )
    tag = f"v{version}"
    if (
        _run(
            ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], run=run, cwd=repo
        ).returncode
        == 0
    ):
        raise ReleaseError(f"tag {tag} already exists locally")
    if _ok(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
        run=run,
        cwd=repo,
        what="git ls-remote",
    ).strip():
        raise ReleaseError(f"tag {tag} already exists on origin")
    integrated = [
        r
        for r in ledger.list(cfg.project, attestation=AttestationKind.INTEGRATED)
        if r.data.get("sha") and gitrepo.is_ancestor(repo, str(r.data["sha"]), head)
    ]
    if not integrated:
        raise ReleaseError(
            "no integration receipt on HEAD's history: merge through the rail before releasing"
        )
    previous = gitrepo.latest_tag(repo)
    span = f"{previous}..HEAD" if previous else "HEAD"
    subjects = _ok(
        ["git", "log", "--no-merges", "--format=%s", span], run=run, cwd=repo, what="git log"
    )
    return ReleasePlan(
        project=cfg.project,
        version=version,
        sha=head,
        image_repository=str(parameter(repo, "deploy.image_repository", project=cfg.project)),
        platform=str(parameter(repo, "deploy.platform")),
        previous_tag=previous,
        changelog=tuple(s for s in subjects.splitlines() if s),
    )


def login(plan: ReleasePlan, *, run: Runner) -> None:
    """GHCR: the operator's `gh` token travels on stdin — never on argv, never in the
    environment, never printed. Another registry is expected to be logged in already."""
    if plan.registry != "ghcr.io":
        return
    parts = plan.image_repository.split("/")
    if len(parts) < 3 or not parts[1]:
        raise ReleaseError(
            f"deploy.image_repository {plan.image_repository!r}: expected ghcr.io/<owner>/<name>"
        )
    owner = parts[1]
    token = _ok(["gh", "auth", "token"], run=run, what="gh auth token").strip()
    _ok(
        ["docker", "login", plan.registry, "-u", owner, "--password-stdin"],
        run=run,
        what="docker login",
        stdin=token + "\n",
    )


def build_and_push(plan: ReleasePlan, repo: Path, *, run: Runner) -> str:
    """Build from the repository root, push the version tag, read the manifest digest back."""
    _ok(
        [
            "docker",
            "build",
            "--platform",
            plan.platform,
            "--build-arg",
            f"VERSION={plan.version}",
            "--build-arg",
            f"GIT_SHA={plan.sha}",
            "--tag",
            plan.image_tag,
            str(repo),
        ],
        run=run,
        what="docker build",
    )
    _ok(["docker", "push", plan.image_tag], run=run, what="docker push")
    out = _ok(
        ["docker", "image", "inspect", plan.image_tag, "--format", "{{json .RepoDigests}}"],
        run=run,
        what="docker image inspect",
    )
    try:
        digests = json.loads(out or "[]")
    except ValueError as exc:
        raise ReleaseError(f"docker image inspect: not JSON: {out!r}") from exc
    prefix = plan.image_repository + "@"
    for ref in digests or []:
        if str(ref).startswith(prefix):
            return str(ref)[len(prefix) :]
    raise ReleaseError(
        f"no repository digest for {plan.image_repository} after the push: {digests!r}"
    )


def tag_and_push(plan: ReleasePlan, repo: Path, *, run: Runner) -> None:
    message = (
        f"{plan.project} {plan.version}\n\n" + "\n".join(f"- {s}" for s in plan.changelog) + "\n"
    )
    _ok(
        ["git", "tag", "-a", plan.tag, "-F", "-", plan.sha],
        run=run,
        cwd=repo,
        what="git tag",
        stdin=message,
    )
    _ok(
        ["git", "push", "origin", f"refs/tags/{plan.tag}"],
        run=run,
        cwd=repo,
        what="git push origin",
    )
    done = _run(["git", "push", "gitlab", f"refs/tags/{plan.tag}"], run=run, cwd=repo)
    if done.returncode != 0:
        raise ReleaseError(
            f"git push gitlab {plan.tag} failed after GitHub succeeded — do not delete the "
            f"GitHub tag; fix the mirror and run `git push gitlab refs/tags/{plan.tag}`: "
            f"{(done.stderr or done.stdout).strip()}"
        )


def attestation_data(plan: ReleasePlan, digest: str) -> dict[str, Any]:
    return {
        "version": plan.version,
        "sha": plan.sha,
        "digest": digest,
        "image": f"{plan.image_repository}@{digest}",
        "tag": plan.tag,
        "platform": plan.platform,
        "changelog": list(plan.changelog),
    }


def attest(ledger: Ledger, plan: ReleasePlan, digest: str, *, issuer: str) -> Record:
    data = attestation_data(plan, digest)
    return ledger.attest(
        plan.project,
        AttestationKind.RELEASED,
        data,
        issuer=issuer,
        idempotency_key=idempotency_key_for(AttestationKind.RELEASED, data),
    )


def release(
    repo: Path, version: str, *, run: Runner = subprocess.run, issuer: str = "operator"
) -> ReleaseOutcome:
    ledger = open_ledger(repo)
    plan = preflight(repo, version, ledger=ledger, run=run)
    login(plan, run=run)
    digest = build_and_push(plan, repo, run=run)
    tag_and_push(plan, repo, run=run)
    return ReleaseOutcome(plan, digest, attest(ledger, plan, digest, issuer=issuer))
