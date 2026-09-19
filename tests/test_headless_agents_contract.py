"""What red-rail uses of `headless-agents` at the pinned tag — frozen here so a bump of the
tag that moves the API fails before the reviewer does (spec §8)."""

import dataclasses
import importlib.metadata
import inspect

from headless_agents import capability, chain, envelope
from headless_agents.profile import CapabilityProfile, Credentials, McpServer, ToolGuard
from headless_agents.protocol import AgentProvider
from headless_agents.providers.agy import AgyProvider
from headless_agents.providers.claude import ClaudeProvider
from headless_agents.providers.codex import CodexProvider
from headless_agents.result import RunResult, TokenUsage
from headless_agents.sandbox import build_toolless_home, ephemeral_root, sandbox_environment
from headless_agents.spec import RunSpec

from rail.contracts.pins import HEADLESS_AGENTS_TAG


def test_the_installed_distribution_is_the_pinned_tag() -> None:
    assert HEADLESS_AGENTS_TAG == "headless-agents-v0.2.0"
    assert importlib.metadata.version("headless-agents") == "0.2.0"


def test_run_spec_fields() -> None:
    names = {f.name for f in dataclasses.fields(RunSpec)}
    assert {
        "prompt",
        "name",
        "model",
        "profile",
        "reasoning_effort",
        "max_turns",
        "timeout_seconds",
        "deadline",
        "report_log",
        "events_log",
        "stderr_log",
        "raw_log",
        "workspace",
        "executable",
        "environment",
        "extra",
    } <= names
    assert RunSpec(prompt="x").max_turns == 1


def test_capability_profile_and_its_parts() -> None:
    assert set(CapabilityProfile.model_fields) >= {"mcp", "guard", "credentials"}
    assert set(McpServer.model_fields) >= {
        "name",
        "url",
        "bearer",
        "bearer_env_var",
        "headers",
        "tools",
    }
    assert set(ToolGuard.model_fields) >= {"path", "hook_name", "timeout_seconds"}
    assert set(Credentials.model_fields) >= {"paths", "mode"}
    assert CapabilityProfile().mcp is None


def test_providers_implement_the_protocol() -> None:
    for provider in (AgyProvider, ClaudeProvider, CodexProvider):
        for method in (
            "build_command",
            "child_environment",
            "prepare_home",
            "tool_call_completed",
            "run",
        ):
            assert callable(getattr(provider, method)), (provider, method)
    assert {m for m in dir(AgentProvider) if not m.startswith("_")} >= {
        "build_command",
        "child_environment",
        "prepare_home",
        "tool_call_completed",
        "run",
    }


def test_results_chain_envelope_and_exit_codes() -> None:
    assert {f.name for f in dataclasses.fields(RunResult)} >= {
        "exit_code",
        "provider",
        "model",
        "report_path",
        "events_log",
        "tokens",
        "duration_seconds",
        "tool_call_completed",
        "model_reported",
        "cost_usd",
    }
    assert {f.name for f in dataclasses.fields(TokenUsage)} >= {"input", "output", "cached"}
    assert list(inspect.signature(chain.run_chain).parameters)[:2] == ["providers", "run_one"]
    assert list(inspect.signature(envelope.unwrap).parameters)[:2] == ["provider", "stdout"]
    assert capability.PROVIDER_FALLBACK_EXIT_CODE == 3
    assert capability.TIMEOUT_EXIT_CODE == 124
    assert (
        callable(build_toolless_home) and callable(sandbox_environment) and callable(ephemeral_root)
    )
