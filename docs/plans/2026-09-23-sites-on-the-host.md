# Sites on the Host — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `private-compose` project names a site in `rail.yaml`. The host that deploys gives
the site's address from a private file. No machine address ever enters the project's
repository: not the manifest, and not a receipt.

**Architecture:** The manifest gains a label (`deploy.site`) and uses the rail's own
`${BIND_ADDRESS}` token in its healthcheck; the rules tying them are pure manifest validation.
The address lives in `~/.config/red-rail/sites.yaml`, read with the rail's private-file rules
by the `private-compose` target alone. The target resolves the site once, feeds the address to
everything that used the declared one, names the site in its `domain`, and redacts the address
from every string the flows attest.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML, Click, pytest, ruff (`uv run …`).

**Spec:** `docs/specs/2026-09-23-sites-on-the-host.md`

## Global Constraints

- No real machine address anywhere in this public repository: code, tests, docs and commit
  messages use documentation addresses only, `192.0.2.0/24`, `198.51.100.0/24` or
  `203.0.113.0/24` (RFC 5737) and `2001:db8::/32` (RFC 3849).
- Site pattern, in the manifest and in the host file: `^[a-z0-9]+(-[a-z0-9]+)*$`.
- The token is `${BIND_ADDRESS}`, braced form only, and only as the healthcheck's host.
- Host file: `~/.config/red-rail/sites.yaml`, or the path `RAIL_SITES_FILE` names, read with
  `rail.private.read_private_file`.
- An address is an IP literal, v4 or v6, written as a string; `0.0.0.0` and `::` are refused.
- Only the `private-compose` target reads the host file. No gate, no `rail check`, no CI job
  ever does.
- Without `deploy.site`, behaviour is unchanged: every existing test passes untouched, except
  the `FakeTarget` of `tests/test_cli_deploy.py`, which gains the identity `redact` (Task 4).
- Everything written is English. Commit through `/git-commit`: an emoji conventional subject,
  ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `unset VIRTUAL_ENV` before `uv run`: the shell may inherit another project's venv.
- Mutation counter-proof: commit the fix BEFORE applying a mutant, restore the file from a
  copy, and check `git diff --quiet` afterwards. Never `git checkout --` on uncommitted work.

## Review Focus

1. **An IPv6 address written unquoted, with all-digit groups** (`address: 2001:0:0:0:0:0:0:1`).
   YAML 1.1 reads it as a base-60 integer. Expected: an error asking for a quoted string, never
   an integer taken as an address. Test in Task 2.
2. **An empty sites file, a `sites:` with nothing under it, a key that is not a label, or an
   unknown field.** Expected: "not a valid sites file", naming the file, never a crash or a
   partial read. Test in Task 2.
3. **`RAIL_SITES_FILE` set to a relative path.** Expected: refused as not absolute, never read
   relative to the current directory. Test in Task 2.
4. **A healthcheck with a query string and a port** (`http://${BIND_ADDRESS}:9100/healthz?full=1`).
   Expected: only the token changes, and `/version` keeps scheme, address and port. Test in
   Task 3.
5. **An error text naming the address in its bracketed IPv6 form, in uppercase.** Expected: the
   redacted text carries the site's name without brackets, and a longer address containing the
   site's (`2001:db8::100`) is left alone. Test in Task 2.

---

### Task 1: The manifest names a site

**Files:**
- Modify: `src/rail/model.py` (imports, two constants, `DeployConfig`, one `RailConfig` validator)
- Test: `tests/test_model.py`

**Interfaces:**
- Produces:
  - `rail.model.SITE_PATTERN: str = r"^[a-z0-9]+(-[a-z0-9]+)*$"`;
  - `rail.model.ADDRESS_TOKEN: str = "${BIND_ADDRESS}"`;
  - `rail.model.DeployConfig.site: str | None`, default `None`, validated against `SITE_PATTERN`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_model.py`:

```python
# -- sites (spec 2026-09-23-sites-on-the-host) ------------------------------------------

SITE_DEPLOY = {
    "target": "private-compose",
    "site": "private-1",
    "healthcheck": "http://${BIND_ADDRESS}:9204/healthz",
}


def _prod(deploy: dict, gates: dict | None = None) -> dict:
    return {**MINIMAL, "tier": "prod", "deploy": deploy, **({"gates": gates} if gates else {})}


def test_a_private_target_names_a_site_and_the_address_token() -> None:
    cfg = RailConfig.model_validate(_prod(SITE_DEPLOY))
    assert cfg.deploy is not None and cfg.deploy.site == "private-1"


@pytest.mark.parametrize(
    "site", ["192.0.2.10", "2001:db8::10", "private.example", "Private-1", "-private", "private_1"]
)
def test_a_site_is_a_label_that_cannot_hold_an_address(site: str) -> None:
    with pytest.raises(ValidationError, match="site"):
        RailConfig.model_validate(_prod({**SITE_DEPLOY, "site": site}))


def test_a_site_is_refused_on_a_public_target() -> None:
    public = {
        "target": "vps-traefik",
        "site": "private-1",
        "healthcheck": "https://probe.hawkixs.com/healthz",
    }
    with pytest.raises(ValidationError, match="private-compose only"):
        RailConfig.model_validate(_prod(public))


def test_the_address_token_without_a_site_is_refused() -> None:
    tokenised = {"target": "private-compose", "healthcheck": "http://${BIND_ADDRESS}:9204/healthz"}
    with pytest.raises(ValidationError, match=r"no deploy\.site"):
        RailConfig.model_validate(_prod(tokenised))


@pytest.mark.parametrize(
    "healthcheck",
    [
        "http://192.0.2.10:9204/healthz",
        "http://private-1:9204/healthz",
        "http://$BIND_ADDRESS:9204/healthz",
        "http://example.invalid/${BIND_ADDRESS}/healthz",
    ],
)
def test_behind_a_site_the_healthcheck_host_is_the_token(healthcheck: str) -> None:
    with pytest.raises(ValidationError, match="healthcheck host"):
        RailConfig.model_validate(_prod({**SITE_DEPLOY, "healthcheck": healthcheck}))


def test_a_site_and_a_declared_bind_address_are_refused_together() -> None:
    gates = {"deploy.bind_address": {"value": "192.0.2.10", "reason": "declared in the manifest"}}
    with pytest.raises(ValidationError, match="two sources for one address"):
        RailConfig.model_validate(_prod(SITE_DEPLOY, gates))


def test_without_a_site_a_declared_bind_address_still_works() -> None:
    gates = {"deploy.bind_address": {"value": "192.0.2.10", "reason": "declared in the manifest"}}
    declared = {"target": "private-compose", "healthcheck": "http://192.0.2.10:9204/healthz"}
    cfg = RailConfig.model_validate(_prod(declared, gates))
    assert cfg.deploy is not None and cfg.deploy.site is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_model.py`
Expected: FAIL. `site` is an extra field today (`Extra inputs are not permitted`), so every new
test except the last fails.

- [ ] **Step 3: Implement** — in `src/rail/model.py`:

Add `import re` to the standard-library imports, then place right after `MANIFEST_NAME`:

```python
# A site is a label the repository may carry; its address lives on the host that deploys
# (spec 2026-09-23-sites-on-the-host). No dot and no colon: no address, no domain fits.
SITE_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
# the rail's own variable: in a compose file (`${BIND_ADDRESS}:9204:9204`) and, behind a site,
# as the healthcheck's host
ADDRESS_TOKEN = "${BIND_ADDRESS}"
_TOKEN_HOST = re.compile(r"^https?://\$\{BIND_ADDRESS\}(?=[:/?#]|$)")
```

Replace `DeployConfig` with:

```python
class DeployConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: DeployTarget
    healthcheck: str = Field(pattern=r"^https?://")
    site: str | None = Field(default=None, pattern=SITE_PATTERN)

    @model_validator(mode="after")
    def _a_site_and_its_token_go_together(self) -> DeployConfig:
        if self.site is not None and self.target is not DeployTarget.PRIVATE_COMPOSE:
            raise ValueError("deploy.site applies to target private-compose only")
        if self.site is None and ADDRESS_TOKEN in self.healthcheck:
            raise ValueError(
                f"deploy.healthcheck uses {ADDRESS_TOKEN} but no deploy.site says whose "
                "address it is"
            )
        if self.site is not None and not _TOKEN_HOST.match(self.healthcheck):
            raise ValueError(
                f"behind deploy.site the healthcheck host is {ADDRESS_TOKEN}, filled from the "
                f"host's sites file (got {self.healthcheck})"
            )
        return self
```

Add to `RailConfig`, after `_ticket_follows_the_ledger`:

```python
    @model_validator(mode="after")
    def _one_source_for_the_address(self) -> RailConfig:
        if self.deploy is not None and self.deploy.site is not None:
            if "deploy.bind_address" in self.gates:
                raise ValueError(
                    "deploy.site and a gates override of deploy.bind_address are two sources "
                    "for one address: the site's comes from the host, drop the override"
                )
        return self
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_model.py && uv run pytest -q`
Expected: PASS, and the full suite stays green (no existing manifest declares a site).

- [ ] **Step 5: Commit**

```bash
git add src/rail/model.py tests/test_model.py
git commit -F - <<'EOF'
✨ feat(model): a private target names a site, never an address

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The host's sites file

**Files:**
- Create: `src/rail/deploy/sites.py`
- Test: `tests/test_sites.py` (new)

**Interfaces:**
- Consumes: `rail.model.SITE_PATTERN`, `rail.model.ADDRESS_TOKEN` (Task 1);
  `rail.private.read_private_file(path: Path) -> bytes`, `rail.private.PrivateFileError`;
  `rail.deploy.DeployError`.
- Produces:
  - `Address = ipaddress.IPv4Address | ipaddress.IPv6Address`;
  - `class Site(BaseModel)` with `address: Address`;
  - `sites_file(environ: Mapping[str, str] | None = None) -> Path`;
  - `load_site(name: str, path: Path | None = None) -> Site`, where every failure is a `DeployError`;
  - `substitute_address(url: str, address: Address) -> str`;
  - `redact_address(text: str, address: Address, label: str) -> str`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_sites.py`:

```python
"""The host's sites file: where a private target's machine is, never in a repository."""

from ipaddress import ip_address
from pathlib import Path

import pytest

from rail.deploy import DeployError
from rail.deploy.sites import load_site, redact_address, sites_file, substitute_address

V4 = "192.0.2.10"  # RFC 5737 and RFC 3849: documentation addresses only
V6 = "2001:db8::10"


def _sites(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_a_private_file_gives_the_site_its_address(tmp_path: Path) -> None:
    path = _sites(tmp_path, f"sites:\n  private-1:\n    address: {V4}\n")
    assert str(load_site("private-1", path).address) == V4


def test_an_ipv6_address_is_accepted(tmp_path: Path) -> None:
    path = _sites(tmp_path, f'sites:\n  private-6:\n    address: "{V6}"\n')
    assert load_site("private-6", path).address.version == 6


def test_a_file_others_can_read_is_refused(tmp_path: Path) -> None:
    path = _sites(tmp_path, f"sites:\n  private-1:\n    address: {V4}\n", mode=0o644)
    with pytest.raises(DeployError, match=r"site private-1: .*mode 644"):
        load_site("private-1", path)


def test_a_symlinked_file_is_refused(tmp_path: Path) -> None:
    real = _sites(tmp_path, f"sites:\n  private-1:\n    address: {V4}\n")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    with pytest.raises(DeployError, match="symlink"):
        load_site("private-1", link)


def test_a_missing_file_names_the_site_the_path_and_the_fix(tmp_path: Path) -> None:
    where = tmp_path / "absent.yaml"
    with pytest.raises(DeployError) as caught:
        load_site("private-1", where)
    message = str(caught.value)
    assert "site private-1" in message and str(where) in message and "0600" in message


def test_an_unknown_site_lists_the_known_ones(tmp_path: Path) -> None:
    text = f"sites:\n  private-1:\n    address: {V4}\n  private-2:\n    address: 192.0.2.20\n"
    path = _sites(tmp_path, text)
    with pytest.raises(DeployError, match=r"private-3 is not declared .*known: private-1, private-2"):
        load_site("private-3", path)


@pytest.mark.parametrize("address", ["private-1.example", "0.0.0.0", "'::'", "192.0.2.300"])
def test_an_address_is_a_literal_that_does_not_publish_everywhere(
    tmp_path: Path, address: str
) -> None:
    path = _sites(tmp_path, f"sites:\n  private-1:\n    address: {address}\n")
    with pytest.raises(DeployError, match="not a valid sites file"):
        load_site("private-1", path)


def test_an_unquoted_all_digit_ipv6_asks_to_be_quoted(tmp_path: Path) -> None:
    """Review focus 1: YAML 1.1 reads 2001:0:0:0:0:0:0:1 as a base-60 integer."""
    path = _sites(tmp_path, "sites:\n  private-1:\n    address: 2001:0:0:0:0:0:0:1\n")
    with pytest.raises(DeployError, match="quoted string"):
        load_site("private-1", path)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "sites:\n",
        f"sites:\n  Private_1:\n    address: {V4}\n",
        f"sites:\n  private-1:\n    address: {V4}\n    port: 22\n",
    ],
)
def test_an_empty_or_malformed_file_is_refused_by_name(tmp_path: Path, text: str) -> None:
    """Review focus 2."""
    path = _sites(tmp_path, text)
    with pytest.raises(DeployError, match=r"sites\.yaml is not a valid sites file"):
        load_site("private-1", path)


def test_the_environment_names_the_file_never_the_address(tmp_path: Path) -> None:
    assert sites_file({}) == Path("~/.config/red-rail/sites.yaml").expanduser()
    assert sites_file({"RAIL_SITES_FILE": str(tmp_path / "x.yaml")}) == tmp_path / "x.yaml"


def test_a_relative_file_is_refused() -> None:
    """Review focus 3: never read relative to the current directory."""
    with pytest.raises(DeployError, match="absolute"):
        load_site("private-1", Path("sites.yaml"))


def test_the_token_is_replaced_and_ipv6_is_bracketed() -> None:
    url = "http://${BIND_ADDRESS}:9204/healthz?full=1"
    assert substitute_address(url, ip_address(V4)) == f"http://{V4}:9204/healthz?full=1"
    assert substitute_address(url, ip_address(V6)) == f"http://[{V6}]:9204/healthz?full=1"


def test_redaction_respects_number_boundaries() -> None:
    text = (
        "connect to host 192.0.2.1 port 22; 192.0.2.10 and 198.51.100.1.192.0.2.1 differ; "
        "192.0.2.1."
    )
    assert redact_address(text, ip_address("192.0.2.1"), "private-1") == (
        "connect to host private-1 port 22; 192.0.2.10 and 198.51.100.1.192.0.2.1 differ; "
        "private-1."
    )


def test_ipv6_redaction_takes_the_brackets_and_ignores_case() -> None:
    """Review focus 5."""
    text = (
        f"GET http://[{V6.upper()}]:9204/healthz failed; "
        f"ssh: connect to host {V6} port 22; 2001:db8::100 is another host"
    )
    assert redact_address(text, ip_address(V6), "private-6") == (
        "GET http://private-6:9204/healthz failed; "
        "ssh: connect to host private-6 port 22; 2001:db8::100 is another host"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_sites.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'rail.deploy.sites'`.

- [ ] **Step 3: Implement** — create `src/rail/deploy/sites.py`:

```python
"""Where a private target's machine is, known only to the host that deploys (spec
`2026-09-23-sites-on-the-host`). A repository names a site; this private file on the host says
where it is, so no machine address is ever committed: not in `rail.yaml`, not in a receipt."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from rail.deploy import DeployError
from rail.model import ADDRESS_TOKEN, SITE_PATTERN
from rail.private import PrivateFileError, read_private_file

DEFAULT_SITES_FILE = "~/.config/red-rail/sites.yaml"
SITES_FILE_VARIABLE = "RAIL_SITES_FILE"  # names the path; the environment never holds an address

Address = IPv4Address | IPv6Address


class Site(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    address: Address

    @field_validator("address", mode="before")
    @classmethod
    def _written_as_text(cls, value: object) -> object:
        # YAML 1.1 reads an all-digit IPv6 such as 2001:0:0:0:0:0:0:1 as a base-60 integer, and
        # an integer would pass for an IPv4 address: only a string is an address here
        if not isinstance(value, str):
            raise ValueError(f"write the address as a quoted string, got {value!r}")
        return value

    @field_validator("address")
    @classmethod
    def _not_everywhere(cls, value: Address) -> Address:
        if value.is_unspecified:
            raise ValueError(f"{value} publishes on every interface, which a private target refuses")
        return value


class SitesFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sites: dict[str, Site]

    @field_validator("sites")
    @classmethod
    def _labels(cls, value: dict[str, Site]) -> dict[str, Site]:
        bad = sorted(name for name in value if not re.fullmatch(SITE_PATTERN, name))
        if bad:
            raise ValueError(f"site names are labels ({SITE_PATTERN}): {', '.join(bad)}")
        return value


def sites_file(environ: Mapping[str, str] | None = None) -> Path:
    """`RAIL_SITES_FILE` when set, else `~/.config/red-rail/sites.yaml`, user-expanded."""
    env = os.environ if environ is None else environ
    return Path(env.get(SITES_FILE_VARIABLE) or DEFAULT_SITES_FILE).expanduser()


def load_site(name: str, path: Path | None = None) -> Site:
    """The site `name` from the host's private sites file. Every failure is a `DeployError`
    naming the site, the file and the fix; the target calls this before any step is planned."""
    where = sites_file() if path is None else path
    fix = f'declare it on this host: `sites: {{{name}: {{address: "…"}}}}` in {where}, mode 0600'
    try:
        raw = read_private_file(where)
    except PrivateFileError as exc:
        raise DeployError(f"site {name}: {exc} — {fix}") from exc
    try:
        document = SitesFile.model_validate(yaml.safe_load(raw) or {})
    except (yaml.YAMLError, ValidationError) as exc:
        raise DeployError(f"site {name}: {where} is not a valid sites file: {exc}") from exc
    site = document.sites.get(name)
    if site is None:
        known = ", ".join(sorted(document.sites)) or "none"
        raise DeployError(f"site {name} is not declared in {where} (known: {known}) — {fix}")
    return site


def substitute_address(url: str, address: Address) -> str:
    """The healthcheck with the rail's token replaced; an IPv6 host is bracketed in a URL."""
    host = f"[{address}]" if address.version == 6 else str(address)
    return url.replace(ADDRESS_TOKEN, host)


def redact_address(text: str, address: Address, label: str) -> str:
    """`text` with every occurrence of `address` replaced by `label`. Number boundaries are
    respected (`192.0.2.1` never matches inside `192.0.2.10`); an IPv6 address is matched
    case-insensitively, and its bracketed form is replaced brackets included."""
    literal = re.escape(str(address))
    if address.version == 4:
        return re.sub(rf"(?<![\d.]){literal}(?!\.?\d)", label, text)
    text = re.sub(rf"\[{literal}\]", label, text, flags=re.IGNORECASE)
    return re.sub(
        rf"(?<![0-9A-Fa-f:]){literal}(?![0-9A-Fa-f:])", label, text, flags=re.IGNORECASE
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_sites.py && uv run ruff check src/ tests/`
Expected: PASS, and ruff clean. If ruff reformats a line, run `uv run ruff format src/rail/deploy/sites.py tests/test_sites.py`
and rerun the tests.

- [ ] **Step 5: Commit**

```bash
git add src/rail/deploy/sites.py tests/test_sites.py
git commit -F - <<'EOF'
✨ feat(deploy): a private sites file on the host says where a site is

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: The private target resolves the site

**Files:**
- Modify: `src/rail/deploy/compose.py` (`ComposeTarget.redact`, the identity)
- Modify: `src/rail/deploy/private_compose.py` (`Parameters.read(repo, cfg, sites=…)`,
  `PrivateCompose.__init__`, `PrivateCompose.redact`)
- Test: `tests/test_deploy_private_compose.py`

**Interfaces:**
- Consumes: `DeployConfig.site` (Task 1); `load_site`, `substitute_address`, `redact_address`,
  `Address` (Task 2).
- Produces:
  - `ComposeTarget.redact(self, text: str) -> str`, the identity, inherited by `VpsTraefik`;
  - `PrivateCompose(repo, cfg, *, sites: Path | None = None, **kwargs)`;
  - `PrivateCompose.domain`: the site's name behind a site, the healthcheck's host otherwise;
  - `PrivateCompose.redact(text) -> str`: the address replaced by the site's name, and the
    identity without a site.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_deploy_private_compose.py`:

```python
# -- behind a site (spec 2026-09-23-sites-on-the-host) -------------------------------------

SITE_SAFE = 'services:\n  app:\n    image: x\n    ports:\n      - "${BIND_ADDRESS}:9100:9100"\n'


def _site_repo(
    tmp_path: Path,
    compose: str,
    *,
    site: str = "private-1",
    healthcheck: str = "http://${BIND_ADDRESS}:9100/healthz",
) -> Path:
    repo = conforming_tree(tmp_path, "red-alerts", "prod")
    write_manifest(
        repo,
        project="red-alerts",
        tier="prod",
        gates={"deploy.ssh_host": ("private-1-deploy", "the host's ssh alias for the site")},
        deploy=True,
    )
    manifest = (
        (repo / "rail.yaml")
        .read_text()
        .replace("  target: vps-traefik\n", f"  target: private-compose\n  site: {site}\n")
    )
    manifest = "\n".join(
        f'  healthcheck: "{healthcheck}"' if line.strip().startswith("healthcheck:") else line
        for line in manifest.splitlines()
    )
    (repo / "rail.yaml").write_text(manifest + "\n")
    (repo / "deploy").mkdir(exist_ok=True)
    (repo / "deploy" / "compose.yaml").write_text(compose)
    commit_all(repo, "feat: the stack")
    return repo


def _host(tmp_path: Path, address: str = BIND) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(f'sites:\n  private-1:\n    address: "{address}"\n')
    path.chmod(0o600)
    return path


def test_behind_a_site_the_host_gives_the_address_the_manifest_never_holds(
    tmp_path: Path,
) -> None:
    repo = _site_repo(tmp_path / "repo", SITE_SAFE)
    assert BIND not in (repo / "rail.yaml").read_text()
    target = PrivateCompose(
        repo, load_rail_config(repo), sites=_host(tmp_path), run=RecordingHost()
    )
    steps = target.steps(_artefact(repo))
    assert "private-1-deploy" in steps[0].title
    assert steps[1].argv == ("GET", f"http://{BIND}:9100/healthz")
    assert steps[2].argv == ("GET", f"http://{BIND}:9100/version")
    assert f"BIND_ADDRESS={BIND}\n" in target.env_file(_artefact(repo))
    assert target.domain == "private-1"


def test_an_ipv6_site_is_bracketed_in_the_urls(tmp_path: Path) -> None:
    repo = _site_repo(tmp_path / "repo", SITE_SAFE)
    target = PrivateCompose(
        repo, load_rail_config(repo), sites=_host(tmp_path, "2001:db8::10"), run=RecordingHost()
    )
    steps = target.steps(_artefact(repo))
    assert steps[1].argv == ("GET", "http://[2001:db8::10]:9100/healthz")
    assert steps[2].argv == ("GET", "http://[2001:db8::10]:9100/version")


def test_a_query_and_a_port_survive_the_substitution(tmp_path: Path) -> None:
    """Review focus 4: only the token changes; `/version` keeps scheme, address and port."""
    repo = _site_repo(
        tmp_path / "repo", SITE_SAFE, healthcheck="http://${BIND_ADDRESS}:9100/healthz?full=1"
    )
    target = PrivateCompose(
        repo, load_rail_config(repo), sites=_host(tmp_path), run=RecordingHost()
    )
    steps = target.steps(_artefact(repo))
    assert steps[1].argv == ("GET", f"http://{BIND}:9100/healthz?full=1")
    assert steps[2].argv == ("GET", f"http://{BIND}:9100/version")


def test_the_site_address_guards_the_compose_file_before_the_first_ssh(tmp_path: Path) -> None:
    other = 'services:\n  app:\n    image: x\n    ports:\n      - "192.0.2.99:9100:9100"\n'
    repo = _site_repo(tmp_path / "repo", other)
    host = RecordingHost()
    target = PrivateCompose(repo, load_rail_config(repo), sites=_host(tmp_path), run=host)
    with pytest.raises(DeployError, match="192.0.2.99:9100:9100"):
        target.steps(_artefact(repo))
    assert [a for a in host.argv if a[0] == "ssh"] == []


def test_a_site_this_host_does_not_know_fails_before_anything_is_planned(tmp_path: Path) -> None:
    repo = _site_repo(tmp_path / "repo", SITE_SAFE, site="private-9")
    with pytest.raises(DeployError, match="private-9 is not declared"):
        PrivateCompose(repo, load_rail_config(repo), sites=_host(tmp_path), run=RecordingHost())


def test_the_target_redacts_its_address_behind_a_site_and_nothing_without_one(
    tmp_path: Path,
) -> None:
    repo = _site_repo(tmp_path / "site", SITE_SAFE)
    target = PrivateCompose(
        repo, load_rail_config(repo), sites=_host(tmp_path), run=RecordingHost()
    )
    assert target.redact(f"connect to host {BIND} port 22") == "connect to host private-1 port 22"
    declared = _private_repo(tmp_path / "declared", SAFE)
    plain = PrivateCompose(declared, load_rail_config(declared), run=RecordingHost())
    assert plain.redact(f"connect to host {BIND}") == f"connect to host {BIND}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_deploy_private_compose.py`
Expected: FAIL with `TypeError: … unexpected keyword argument 'sites'` on the new tests. The
existing ones still pass.

- [ ] **Step 3: Implement**

In `src/rail/deploy/compose.py`, add to `ComposeTarget`, right after `precheck`:

```python
    def redact(self, text: str) -> str:
        """What a record may say about this target. The default hides nothing."""
        return text
```

In `src/rail/deploy/private_compose.py`, add the import:

```python
from rail.deploy.sites import Address, load_site, redact_address, substitute_address
```

Replace the module's `Parameters` class (the private one, not `compose.Parameters`) with:

```python
@dataclass(frozen=True, slots=True)
class Parameters:
    bind_address: str
    site: str | None = None
    address: Address | None = None  # behind a site: the parsed address, for redaction

    @classmethod
    def read(cls, repo: Path, cfg: RailConfig, *, sites: Path | None = None) -> Parameters:
        site = cfg.deploy.site if cfg.deploy is not None else None
        if site is not None:
            address = load_site(site, sites).address
            return cls(bind_address=str(address), site=site, address=address)
        value = parameter(repo, "deploy.bind_address")
        if not value:
            raise DeployError(
                "deploy.bind_address is not set: a private target must say which address it "
                "publishes on — name a `deploy.site` this host declares in its sites file, or "
                "declare the address in rail.yaml under `gates:` with its reason"
            )
        return cls(bind_address=str(value))
```

Replace `PrivateCompose.__init__` with the version below, and add `redact` after `origin`:

```python
class PrivateCompose(ComposeTarget):
    def __init__(
        self, repo: Path, cfg: RailConfig, *, sites: Path | None = None, **kwargs: Any
    ) -> None:
        super().__init__(repo, cfg, **kwargs)
        self.private = Parameters.read(repo, cfg, sites=sites)
        if self.private.address is not None:
            # the token becomes the site's address here and nowhere earlier: the manifest
            # carries a label, the host carries the address
            self.healthcheck = substitute_address(self.healthcheck, self.private.address)
        # `Field(pattern=r"^https?://")` is match-at-start, so `https://` passes validation:
        # the host has to be checked here, exactly as `vps-traefik` checks it. Without this
        # the failure surfaces only after the healthcheck timeout, once ssh has already
        # changed the machine, and an empty domain reaches the attestation.
        self.domain = domain_of(self.healthcheck)
        if self.private.site is not None:
            self.domain = self.private.site  # what records and prompts name: never the address

    def redact(self, text: str) -> str:
        if self.private.address is None or self.private.site is None:
            return text
        return redact_address(text, self.private.address, self.private.site)
```

Check with `grep -n "Parameters.read(repo)" src/rail/deploy/private_compose.py` that no call
to the old one-argument form remains.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_deploy_private_compose.py tests/test_deploy_vps_traefik.py && uv run pytest -q`
Expected: PASS. The existing private-compose tests (a declared `deploy.bind_address`) pass
unchanged, including `test_a_missing_bind_address_is_an_error_not_a_fallback`, since its
message still names `deploy.bind_address`.

- [ ] **Step 5: Commit**

```bash
git add src/rail/deploy/compose.py src/rail/deploy/private_compose.py tests/test_deploy_private_compose.py
git commit -F - <<'EOF'
✨ feat(deploy): the private target takes its address from the site

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Every attested string names the site

**Files:**
- Modify: `src/rail/deploy/flow.py` (`Target.redact`, `_unchanged`, `_redacted`, the
  `Attester.redact` field, and the three `Attester(…)` constructions)
- Modify: `tests/test_cli_deploy.py` (the `FakeTarget` identity `redact`, and two new tests)

**Interfaces:**
- Consumes: `Target.redact(text) -> str` on every target (Task 3 gives it to both real ones).
- Produces: `flow.Attester(ledger, project, target, issuer, redact=…)`. Every string of every
  payload is redacted BEFORE the idempotency key, the receipt mirror and the ledger see it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli_deploy.py`, add to `FakeTarget`:

```python
    def redact(self, text: str) -> str:
        return text
```

Append to `tests/test_cli_deploy.py`:

```python
# -- behind a site, records name the site (spec 2026-09-23-sites-on-the-host) ---------------


class SiteTarget(FakeTarget):
    """A private target behind a site: its errors name the address, its records must not."""

    ADDRESS = "192.0.2.10"  # RFC 5737 documentation address

    def __init__(self) -> None:
        super().__init__()
        self.domain = "private-1"

    def apply(self, artefact: Artefact) -> LiveVersion:
        if artefact.digest in self.broken:
            self.applied.append(artefact.digest)
            raise DeployError(
                f"http://{self.ADDRESS}:9204/healthz: not healthy within 120s; "
                f"ssh: connect to host {self.ADDRESS} port 22"
            )
        return super().apply(artefact)

    def redact(self, text: str) -> str:
        return text.replace(self.ADDRESS, "private-1")


def test_what_the_flows_attest_names_the_site_never_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SiteTarget()
    monkeypatch.setattr(flow, "make_target", lambda repo, cfg, **kwargs: fake)
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    first = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"])
    assert first.exit_code == 0, first.output
    fake.broken.add(D2)
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 1, out.output
    receipts = "".join(p.read_text() for p in (repo / RECEIPTS_DIR).glob("*.json"))
    assert SiteTarget.ADDRESS not in receipts
    incident = next(d for k, d in _kinds(repo) if k == "incident_detected")
    assert "connect to host private-1 port 22" in incident["reason"]
    assert [d["domain"] for k, d in _kinds(repo) if k == "deployed"] == ["private-1", "private-1"]


def test_the_attester_redacts_every_string_before_the_ledger_sees_it() -> None:
    """The file ledger stores what it is given; brain receives the same payload after the
    mirror. Recording what `attest` is handed covers both."""
    handed: list[dict] = []

    class RecordingLedger:
        def list(self, project: str, **kwargs: object) -> list:
            return []

        def attest(self, project, kind, payload, *, issuer, idempotency_key, emitted_at):
            handed.append(payload)
            return None

    attester = flow.Attester(
        RecordingLedger(),  # type: ignore[arg-type]
        "red-alerts",
        "private-compose",
        "operator",
        redact=lambda text: text.replace("192.0.2.10", "private-1"),
    )
    attester.attest(
        AttestationKind.INCIDENT_DETECTED,
        {
            "reason": "connect to host 192.0.2.10",
            "nested": {"why": ["192.0.2.10", 3]},
            "drill": False,
            "version": "0.1.0",
        },
    )
    assert handed[0]["reason"] == "connect to host private-1"
    assert handed[0]["nested"] == {"why": ["private-1", 3]}
    assert handed[0]["drill"] is False and handed[0]["target"] == "private-compose"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_cli_deploy.py`
Expected: FAIL. The first new test finds `192.0.2.10` in the incident reason of the receipts,
since the attester does not redact yet. The second fails with
`TypeError: … unexpected keyword argument 'redact'`.

- [ ] **Step 3: Implement** — in `src/rail/deploy/flow.py`:

Give the `Target` protocol its third method:

```python
class Target(Protocol):
    domain: str

    def steps(self, artefact: Artefact) -> list[Step]: ...
    def apply(self, artefact: Artefact) -> LiveVersion: ...
    def redact(self, text: str) -> str: ...
```

Above `Attester`, add:

```python
def _unchanged(text: str) -> str:
    return text


def _redacted(value: Any, redact: Callable[[str], str]) -> Any:
    """Every string of an attestation payload, as the target allows it to be recorded."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redacted(item, redact) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redacted(item, redact) for item in value]
    return value
```

Give `Attester` a field right after `issuer`:

```python
    # a private target behind a site replaces its address with the site's name: every string
    # of every record passes here before the key, the mirror and the ledger see it
    redact: Callable[[str], str] = field(default=_unchanged)
```

In `Attester.attest`, replace the first line with:

```python
        payload = _redacted({"target": self.target, **data}, self.redact)
```

In `forward`, `rollback` and `drill`, the target already exists when the attester is built.
Make each of the three constructions:

```python
    attester = Attester(ledger, cfg.project, cfg.deploy.target.value, issuer, redact=target.redact)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_cli_deploy.py && uv run pytest -q`
Expected: PASS. The existing flow tests are unchanged, since the identity redaction leaves
every record as it was.

- [ ] **Step 5: Commit**

```bash
git add src/rail/deploy/flow.py tests/test_cli_deploy.py
git commit -F - <<'EOF'
✨ feat(deploy): every attested string names the site, never its address

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The operator's path, end to end, and the counter-proof

**Files:**
- Modify: `CLAUDE.md` (the `private_compose.py` entry of the architecture section)
- Modify: `skills/rail-deploy/SKILL.md` (the host prerequisite of a private target)
- Test: `tests/test_deploy_private_compose.py` (three CLI acceptance tests)

**Interfaces:**
- Consumes: everything above, through the CLI: `rail check`, and `rail deploy --plan` with
  `RAIL_SITES_FILE`.

- [ ] **Step 1: Write the acceptance tests** — append to `tests/test_deploy_private_compose.py`:

```python
# -- acceptance through the CLI (spec 2026-09-23-sites-on-the-host, criteria 1 and 3) --------

from click.testing import CliRunner  # noqa: E402

from rail.cli import main  # noqa: E402
from rail.ledger import RECEIPTS_DIR, AttestationKind  # noqa: E402
from rail.ledger.file import FileLedger  # noqa: E402


def _released(repo: Path) -> None:
    artefact = _artefact(repo)
    FileLedger(repo / RECEIPTS_DIR).attest(
        "red-alerts",
        AttestationKind.RELEASED,
        {
            "version": artefact.version,
            "sha": artefact.sha,
            "digest": artefact.digest,
            "image": artefact.image,
            "tag": "v0.1.0",
        },
        issuer="op",
        idempotency_key="released:0.1.0",
    )


@pytest.mark.parametrize("ci", [False, True])
def test_a_manifest_with_a_site_passes_rail_check_where_no_host_file_exists(
    tmp_path: Path, ci: bool
) -> None:
    repo = _site_repo(tmp_path / "repo", SITE_SAFE)
    env = {"RAIL_SITES_FILE": str(tmp_path / "nowhere.yaml")}
    args = ["check", "hygiene", "--repo", str(repo), *(["--ci"] if ci else [])]
    out = CliRunner().invoke(main, args, env=env)
    assert "PASS  hygiene.rail_config" in out.output, out.output


def test_plan_names_the_site_and_prints_the_resolved_steps(tmp_path: Path) -> None:
    repo = _site_repo(tmp_path / "repo", SITE_SAFE)
    _released(repo)
    env = {"RAIL_SITES_FILE": str(_host(tmp_path))}
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--plan"], env=env)
    assert out.exit_code == 0, out.output
    assert "on private-1" in out.output
    assert "private-1-deploy" in out.output
    assert f"GET http://{BIND}:9100/healthz" in out.output


def test_plan_without_the_host_file_fails_before_printing_a_step(tmp_path: Path) -> None:
    repo = _site_repo(tmp_path / "repo", SITE_SAFE)
    _released(repo)
    env = {"RAIL_SITES_FILE": str(tmp_path / "nowhere.yaml")}
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--plan"], env=env)
    assert out.exit_code == 1
    assert "site private-1" in out.output and "nowhere.yaml" in out.output
    assert "GET " not in out.output and "ssh " not in out.output
```

- [ ] **Step 2: Run the acceptance tests**

Run: `unset VIRTUAL_ENV && uv run pytest -q tests/test_deploy_private_compose.py -k "rail_check or plan"`
Expected: PASS. Tasks 1 to 4 already implement the behaviour; these tests pin it at the CLI,
where the operator meets it. If one fails, fix the implementation, not the test.

- [ ] **Step 3: Update the documentation**

In `CLAUDE.md`, replace

```
  (a machine with no public route: verification over the address of `deploy.healthcheck`,
  `deploy.bind_address` with no default, and a refusal — before the first ssh — of a
```

with

```
  (a machine with no public route: verification over the address of `deploy.healthcheck`,
  `deploy.bind_address` with no default — or, behind a `deploy.site`, the address this host
  declares in `~/.config/red-rail/sites.yaml` (`sites.py`, a private file: the manifest and
  every attestation carry the site's name, never the address) — and a refusal — before the first ssh — of a
```

In `skills/rail-deploy/SKILL.md`, insert between the paragraph "Stages 8 and 9 run from the
host…" and step 1:

```markdown
A private target behind a site (`deploy.site` in `rail.yaml`) needs its address on this host,
never in the repository: `~/.config/red-rail/sites.yaml`, mode 0600, holding
`sites: {<site>: {address: "<ip>"}}` (`RAIL_SITES_FILE` names another path). Without it,
`rail deploy --plan` stops before printing a step, naming the site and the file.
```

- [ ] **Step 4: Run the full verification**

Run: `unset VIRTUAL_ENV && make ci`
Expected: exit 0. ruff clean, every test passes, and `rail check` passes 18/18 on this
repository.

Run: `git diff origin/main -- . | grep -E '^\+' | grep -o -E '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' | grep -v -E '^(192\.0\.2|198\.51\.100|203\.0\.113)\.[0-9]+$|^127\.0\.0\.1$|^0\.0\.0\.0$' || echo "no real address added"`
Expected: `no real address added`. Only lines this branch adds are checked. A site's name is a
label and may appear anywhere; an address outside the documentation range may not.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md skills/rail-deploy/SKILL.md tests/test_deploy_private_compose.py
git commit -F - <<'EOF'
📝 docs(deploy): a private target's address is a host file, pinned at the CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

- [ ] **Step 6: Mutation counter-proof** (spec, success criterion 5)

Everything is committed, so the tree is clean. Run each mutant, check that it is killed, then
restore from the copy:

```bash
unset VIRTUAL_ENV
mutate() {  # file, old, new: fails loudly when the pattern is absent
  python3 - "$1" "$2" "$3" <<'PY'
import sys
path, old, new = sys.argv[1:]
text = open(path).read()
assert old in text, f"pattern not found in {path}: {old!r}"
open(path, "w").write(text.replace(old, new, 1))
PY
}
check() {  # a mutant is killed when at least one test fails
  if uv run pytest -q -x "$@" >/dev/null 2>&1; then echo "SURVIVED"; else echo "killed"; fi
}

cp src/rail/deploy/flow.py /tmp/flow.py.orig
mutate src/rail/deploy/flow.py "        return redact(value)" "        return value"
check tests/test_cli_deploy.py                 # M1, no redaction. Expected: killed
cp /tmp/flow.py.orig src/rail/deploy/flow.py

cp src/rail/model.py /tmp/model.py.orig
mutate src/rail/model.py '            if "deploy.bind_address" in self.gates:' '            if False:'
check tests/test_model.py                      # M2, two sources allowed. Expected: killed
cp /tmp/model.py.orig src/rail/model.py

cp src/rail/deploy/sites.py /tmp/sites.py.orig
mutate src/rail/deploy/sites.py 'host = f"[{address}]" if address.version == 6 else str(address)' 'host = str(address)'
check tests/test_sites.py tests/test_deploy_private_compose.py   # M3, no IPv6 brackets. Expected: killed
cp /tmp/sites.py.orig src/rail/deploy/sites.py

git diff --quiet && echo "tree restored"
```

Expected: `killed` three times, then `tree restored`. A mutant that survives means a test is
missing. Add the test, commit it, and rerun this step.

---

## After the merge (outside this branch)

Criterion 6 of the spec is proven on the real host, not in this repository:
1. Reinstall the global rail from `origin/main` (snippet `efdec356`).
2. red-alerts merges its prod manifest (its `deploy.site`) and runs `rail release`,
   `rail deploy --plan`, `rail deploy` and `rail check observe`.
3. `git grep` for the site's address in red-alerts, receipts included, finds nothing.
