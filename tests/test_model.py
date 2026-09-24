"""RailConfig: the per-project manifest. Everything not in it is a tier default."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.model import DeployTarget, LedgerBackend, RailConfig, Stack, Tier, load_rail_config

MINIMAL = {
    "rail": 1,
    "project": "red-probe",
    "brain_key": "red-probe",
    "tier": "dev",
    "stack": "python",
}


def test_minimal_dev_config_is_valid() -> None:
    cfg = RailConfig.model_validate(MINIMAL)
    assert cfg.tier is Tier.DEV
    assert cfg.stack is Stack.PYTHON
    assert cfg.deploy is None
    assert cfg.gates == {}


def test_project_slug_must_be_red_kebab_case() -> None:
    with pytest.raises(ValidationError, match="project"):
        RailConfig.model_validate({**MINIMAL, "project": "Probe_1"})


def test_prod_tier_requires_a_deploy_target() -> None:
    with pytest.raises(ValidationError, match="deploy"):
        RailConfig.model_validate({**MINIMAL, "tier": "prod"})


def test_prod_tier_with_deploy_target_is_valid() -> None:
    cfg = RailConfig.model_validate(
        {
            **MINIMAL,
            "tier": "prod",
            "deploy": {"target": "vps-traefik", "healthcheck": "https://probe.hawkixs.com/healthz"},
        }
    )
    assert cfg.deploy is not None
    assert cfg.deploy.target.value == "vps-traefik"


def test_gate_override_requires_a_reason() -> None:
    with pytest.raises(ValidationError, match="reason"):
        RailConfig.model_validate({**MINIMAL, "gates": {"review.human_required": {"value": False}}})


def test_gate_override_with_reason_is_a_declared_exception() -> None:
    cfg = RailConfig.model_validate(
        {**MINIMAL, "gates": {"review.human_required": {"value": False, "reason": "solo spike"}}}
    )
    assert cfg.gates["review.human_required"].reason == "solo spike"


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        RailConfig.model_validate({**MINIMAL, "colour": "red"})


def test_load_rail_config_reads_yaml(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(
        "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: bootstrap\nstack: docs\n"
    )
    cfg = load_rail_config(tmp_path)
    assert cfg.tier is Tier.BOOTSTRAP


def test_load_rail_config_missing_file_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="rail.yaml"):
        load_rail_config(tmp_path)


def test_ledger_defaults_to_file_so_rail_works_without_brain() -> None:
    cfg = RailConfig.model_validate(MINIMAL)
    assert cfg.ledger is LedgerBackend.FILE


def test_ledger_accepts_brain_as_the_shared_backend() -> None:
    cfg = RailConfig.model_validate(
        {**MINIMAL, "ledger": "brain", "ticket": "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"}
    )
    assert cfg.ledger is LedgerBackend.BRAIN


def test_ledger_rejects_unknown_backends() -> None:
    with pytest.raises(ValidationError, match="ledger"):
        RailConfig.model_validate({**MINIMAL, "ledger": "sqlite"})


def test_brain_ledger_requires_a_ticket(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(
        "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"
        "ledger: brain\n"
    )
    with pytest.raises(ValidationError, match="ticket"):
        load_rail_config(tmp_path)


def test_ticket_is_a_uuid_and_only_with_the_brain_ledger(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(
        "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"
        "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
    )
    cfg = load_rail_config(tmp_path)
    assert str(cfg.ticket) == "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"
    (tmp_path / "rail.yaml").write_text(
        "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"
        "ledger: file\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
    )
    with pytest.raises(ValidationError, match="ticket"):
        load_rail_config(tmp_path)
    (tmp_path / "rail.yaml").write_text(
        "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"
        "ledger: brain\nticket: not-a-uuid\n"
    )
    with pytest.raises(ValidationError):
        load_rail_config(tmp_path)


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
    with pytest.raises(ValidationError, match="a private target"):
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
        # the token followed by `:9204@other.example` reads as userinfo, not a port: the URL's
        # real host is `other.example`, so the checks would go there instead (review finding)
        "http://${BIND_ADDRESS}:9204@other.example/healthz",
    ],
)
def test_behind_a_site_the_healthcheck_host_is_the_token(healthcheck: str) -> None:
    with pytest.raises(ValidationError, match="healthcheck host"):
        RailConfig.model_validate(_prod({**SITE_DEPLOY, "healthcheck": healthcheck}))


@pytest.mark.parametrize(
    "healthcheck",
    [
        "http://${BIND_ADDRESS}:9204/healthz",
        "http://${BIND_ADDRESS}/healthz",
        "http://${BIND_ADDRESS}",
    ],
)
def test_behind_a_site_the_token_alone_as_the_host_is_accepted(healthcheck: str) -> None:
    cfg = RailConfig.model_validate(_prod({**SITE_DEPLOY, "healthcheck": healthcheck}))
    assert cfg.deploy is not None and cfg.deploy.healthcheck == healthcheck


def test_a_site_and_a_declared_bind_address_are_refused_together() -> None:
    gates = {"deploy.bind_address": {"value": "192.0.2.10", "reason": "declared in the manifest"}}
    with pytest.raises(ValidationError, match="two sources for one address"):
        RailConfig.model_validate(_prod(SITE_DEPLOY, gates))


def test_without_a_site_a_declared_bind_address_still_works() -> None:
    gates = {"deploy.bind_address": {"value": "192.0.2.10", "reason": "declared in the manifest"}}
    declared = {"target": "private-compose", "healthcheck": "http://192.0.2.10:9204/healthz"}
    cfg = RailConfig.model_validate(_prod(declared, gates))
    assert cfg.deploy is not None and cfg.deploy.site is None


# -- private-systemd (spec 2026-09-24-private-systemd-target) ----------------------------

SYSTEMD_DEPLOY = {
    "target": "private-systemd",
    "site": "private-1",
    "healthcheck": "http://${BIND_ADDRESS}:9100/health",
    "unit": "deploy/red-agent.service",
    "binary": "/usr/local/bin/red",
}


def test_a_systemd_target_names_its_unit_and_its_binary() -> None:
    cfg = RailConfig.model_validate(_prod(SYSTEMD_DEPLOY))
    assert cfg.deploy is not None and cfg.deploy.target is DeployTarget.PRIVATE_SYSTEMD
    assert cfg.deploy.unit == "deploy/red-agent.service"
    assert cfg.deploy.binary == "/usr/local/bin/red"


@pytest.mark.parametrize("missing", ["unit", "binary"])
def test_a_systemd_target_without_its_unit_or_binary_is_refused(missing: str) -> None:
    deploy = {key: value for key, value in SYSTEMD_DEPLOY.items() if key != missing}
    with pytest.raises(ValidationError, match=f"deploy.{missing} is required"):
        RailConfig.model_validate(_prod(deploy))


@pytest.mark.parametrize("field", ["unit", "binary"])
def test_a_compose_target_refuses_a_unit_or_a_binary(field: str) -> None:
    with pytest.raises(ValidationError, match=f"deploy.{field} applies"):
        RailConfig.model_validate(_prod({**SITE_DEPLOY, field: SYSTEMD_DEPLOY[field]}))


@pytest.mark.parametrize(
    "unit",
    [
        "/etc/systemd/system/red-agent.service",  # absolute: outside the repository
        "deploy/../red-agent.service",  # escapes through `..`
        "deploy/red-agent.timer",  # not a service
        "deploy/red_agent.service",  # not a plain unit name
        "deploy/red-agent@.service",  # a template
        "deploy/red agent.service",  # a space would reach the remote script
    ],
)
def test_the_unit_is_a_plain_service_file_inside_the_repository(unit: str) -> None:
    with pytest.raises(ValidationError, match="deploy.unit must be"):
        RailConfig.model_validate(_prod({**SYSTEMD_DEPLOY, "unit": unit}))


@pytest.mark.parametrize(
    "binary",
    [
        "usr/local/bin/red",  # relative
        "/usr/local/bin/../red",  # a `..` component
        "/usr/local/bin/",  # no file name
        "/usr/local/bin/r d",  # a space would reach the remote script
        "/opt/$(id)",  # a substitution would too
    ],
)
def test_the_binary_is_an_absolute_path_of_safe_characters(binary: str) -> None:
    with pytest.raises(ValidationError, match="deploy.binary must be"):
        RailConfig.model_validate(_prod({**SYSTEMD_DEPLOY, "binary": binary}))


@pytest.mark.parametrize(
    "binary",
    ["/usr/local/bin/red-agent.service", "/usr/local/bin/release.env"],
    ids=["the-unit", "release-env"],
)
def test_the_binary_never_takes_the_name_of_a_file_the_release_holds(binary: str) -> None:
    """The release directory holds the binary, the unit and `release.env` side by side, by
    file name: a binary named like either would overwrite the file the rail checked or wrote."""
    with pytest.raises(ValidationError, match="would overwrite"):
        RailConfig.model_validate(_prod({**SYSTEMD_DEPLOY, "binary": binary}))


def test_a_site_is_accepted_on_the_systemd_target_too() -> None:
    cfg = RailConfig.model_validate(_prod(SYSTEMD_DEPLOY))
    assert cfg.deploy is not None and cfg.deploy.site == "private-1"


def test_the_retired_target_name_no_longer_loads() -> None:
    with pytest.raises(ValidationError, match="pc-server-systemd"):
        RailConfig.model_validate(_prod({**SITE_DEPLOY, "target": "pc-server-systemd"}))
