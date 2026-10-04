import click
import pytest
from click.testing import CliRunner

from rail.commands._actor import no_issuer_option, resolve_or_exit


@click.command()
@no_issuer_option
def probe() -> None:
    click.echo(resolve_or_exit())


def test_the_resolved_label_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "service:probe")
    out = CliRunner().invoke(probe, [])
    assert out.exit_code == 0 and out.output.strip() == "service:probe"


def test_a_refusal_exits_2_and_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAIL_ACTOR")
    out = CliRunner().invoke(probe, [])
    assert out.exit_code == 2 and "RAIL_ACTOR=agent:<name>" in out.output


def test_issuer_is_a_usage_error_naming_the_variable() -> None:
    out = CliRunner().invoke(probe, ["--issuer", "operator"])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output
