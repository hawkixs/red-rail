"""The template's answer sets, shared by every test that renders "every combination" (spec
2026-09-24-template-alignment, decisions 10, 14 and 15; spec 2026-09-24-rust-stack, decision 1).
One list, one exclusion: rust at tier prod is not templated yet."""

from typing import NamedTuple

from rail.model import DeployTarget, LedgerBackend, Stack, Tier

TARGET_FAMILIES = (DeployTarget.VPS_TRAEFIK.value, DeployTarget.PRIVATE_COMPOSE.value)
EXCLUDED = frozenset({(Stack.RUST, Tier.PROD)})


class Combo(NamedTuple):
    """One answer set of the template. Every guidance test reads the same renders."""

    stack: Stack
    tier: Tier
    ledger: LedgerBackend
    target: str | None = None  # prod only: one target per family, public and private
    brain_key: str = "red-probe"

    @property
    def label(self) -> str:
        parts = [self.stack.value, self.tier.value, self.ledger.value]
        parts += [self.target] if self.target else []
        parts += [self.brain_key] if self.brain_key != "red-probe" else []
        return "-".join(parts)


COMBINATIONS = [
    Combo(stack, tier, ledger, target)
    for stack in Stack
    for tier in Tier
    if (stack, tier) not in EXCLUDED
    for ledger in LedgerBackend
    for target in (TARGET_FAMILIES if tier is Tier.PROD else (None,))
] + [Combo(Stack.PYTHON, Tier.BOOTSTRAP, LedgerBackend.FILE, brain_key="red_probe")]
