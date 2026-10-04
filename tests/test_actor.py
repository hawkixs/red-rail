import io

import pytest

from rail.actor import Actor, ActorKind, ActorRefused, current_actor, resolve_actor, stdin_is_tty

SESSION = "e7fb11aa-215b-5ca3-82df-65e4c7a9650d"


def resolve(env: dict[str, str], tty: bool = False) -> Actor:
    return resolve_actor(env, tty)


def test_claude_code_marker_gives_an_agent_with_its_session() -> None:
    actor = resolve({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": SESSION})
    assert actor == Actor(ActorKind.AGENT, f"agent:claude-code:{SESSION}")


def test_a_marker_without_session_gives_the_bare_harness() -> None:
    assert resolve({"CLAUDECODE": "1"}).label == "agent:claude-code"


def test_the_marker_wins_over_the_terminal() -> None:
    assert resolve({"CLAUDECODE": "1"}, tty=True).kind is ActorKind.AGENT


def test_a_terminal_without_marker_is_the_operator() -> None:
    assert resolve({}, tty=True) == Actor(ActorKind.OPERATOR, "operator")


def test_neither_marker_nor_terminal_is_refused_with_the_fix_named() -> None:
    with pytest.raises(ActorRefused, match="RAIL_ACTOR=agent:<name>"):
        resolve({})


@pytest.mark.parametrize(
    "value", ["operator", "agent:codex", "agent:codex:s-1.2_3", "service:cron-backup"]
)
def test_a_valid_declared_actor_is_taken_as_is(value: str) -> None:
    assert resolve({"RAIL_ACTOR": value}).label == value


@pytest.mark.parametrize(
    "value",
    ["root", "agent:", "agent:Codex", "service:a/b", "service:${X}", "agent:x:" + "s" * 64, "a b"],
)
def test_an_invalid_declared_actor_is_refused(value: str) -> None:
    with pytest.raises(ActorRefused):
        resolve({"RAIL_ACTOR": value})


def test_a_declared_label_of_64_characters_is_accepted_and_65_refused() -> None:
    ok = "agent:x:" + "s" * (64 - len("agent:x:"))
    assert resolve({"RAIL_ACTOR": ok}).label == ok
    with pytest.raises(ActorRefused):
        resolve({"RAIL_ACTOR": ok + "s"})


def test_operator_declared_while_an_agent_marker_is_present_is_refused() -> None:
    with pytest.raises(ActorRefused, match="inherited"):
        resolve({"RAIL_ACTOR": "operator", "CLAUDECODE": "1"}, tty=True)


def test_an_empty_declaration_is_read_as_unset() -> None:
    assert resolve({"RAIL_ACTOR": ""}, tty=True).label == "operator"


def test_an_over_long_session_id_is_truncated_and_stays_valid() -> None:
    label = resolve({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s" * 200}).label
    assert len(label) == 64 and label.startswith("agent:claude-code:")


def test_ai_agent_alone_is_reduced_to_the_grammar() -> None:
    assert resolve({"AI_AGENT": "Some Tool/2.1"}).label == "agent:some-tool-2-1"


def test_ai_agent_of_nothing_usable_is_unknown() -> None:
    assert resolve({"AI_AGENT": "///"}).label == "agent:unknown"


@pytest.fixture
def bare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RAIL_ACTOR", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "AI_AGENT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("bare_env")
def test_a_detached_process_has_no_terminal_and_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("rail.actor.sys.stdin", None)
    with pytest.raises(ActorRefused):
        current_actor()


@pytest.mark.usefixtures("bare_env")
def test_a_detached_process_can_still_declare_a_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("rail.actor.sys.stdin", None)
    monkeypatch.setenv("RAIL_ACTOR", "service:x")
    assert current_actor() == Actor(ActorKind.SERVICE, "service:x")


def test_a_specific_marker_wins_over_the_generic_agent_variable() -> None:
    env = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_SESSION_ID": SESSION,
        "AI_AGENT": "claude-code_2-1-286_agent",
    }
    assert resolve(env).label == f"agent:claude-code:{SESSION}"


def test_a_session_outside_the_grammar_is_made_safe() -> None:
    env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "a b/c"}
    assert resolve(env).label == "agent:claude-code:a-b-c"


def test_a_closed_stdin_is_no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr("sys.stdin", closed)
    assert stdin_is_tty() is False
