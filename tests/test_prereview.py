"""`pre-review.js` is a pre-review, never the gate: every agent() carries an explicit tier
(never an implicit Fable agent), the script passes the operator's tiering gate when that
hook is present, and it says what it is."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "workflows" / "pre-review.js"
TIERING_GATE = Path.home() / ".claude" / "hooks" / "tiering" / "tiering_gate.py"
AGENT_CALL = re.compile(r"(?<![\w$.])agent\s*\(")


def test_every_agent_call_names_a_tier_or_a_pinned_role() -> None:
    text = SCRIPT.read_text()
    calls = list(AGENT_CALL.finditer(text))
    assert len(calls) == 3
    for match in calls:
        window = text[match.start() : match.start() + 900]
        assert re.search(r"agentType:\s*'(wf-scan|red-reviewer|wf-judge)'", window), window[:120]
    assert "fable" not in text.lower()
    assert "agentType: 'red-reviewer', model: 'sonnet'" in text  # fan-out on sonnet, never opus


def test_meta_declares_the_three_phases_with_their_models() -> None:
    text = SCRIPT.read_text()
    assert text.lstrip().startswith("export const meta = {")
    for phase, model in (("Scan", "haiku"), ("Review", "sonnet"), ("Verify", "opus")):
        assert re.search(rf"title: '{phase}'.*?{model}", text, re.DOTALL), phase


def test_the_script_says_it_is_a_pre_review_not_the_gate() -> None:
    text = SCRIPT.read_text()
    assert "pre-review" in text and "never satisfies the review gate" in text


@pytest.mark.skipif(
    not TIERING_GATE.is_file() or shutil.which("python3") is None,
    reason="no tiering gate on this host",
)
def test_the_tiering_gate_accepts_the_script() -> None:
    done = subprocess.run(
        ["python3", str(TIERING_GATE), "--check", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "OK" in done.stdout and "3 agent() site(s)" in done.stdout
