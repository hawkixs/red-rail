import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "rail"
DEFAULTED = re.compile(r"issuer\s*(?::\s*str)?\s*=\s*[\"']operator[\"']")


def test_no_command_or_flow_defaults_an_issuer_to_the_operator() -> None:
    hits = [
        f"{p.relative_to(SRC)}:{n}"
        for folder in ("commands", "deploy")
        for p in (SRC / folder).rglob("*.py")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if DEFAULTED.search(line)
    ]
    assert hits == []


def test_issuer_survives_only_as_the_hidden_refusal() -> None:
    declared = [p.name for p in (SRC / "commands").glob("*.py") if '"--issuer"' in p.read_text()]
    assert declared == ["_actor.py"]
