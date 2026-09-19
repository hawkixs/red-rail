"""RailConfig: the per-project manifest. Everything not in it is a tier default."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.model import LedgerBackend, RailConfig, Stack, Tier, load_rail_config

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
