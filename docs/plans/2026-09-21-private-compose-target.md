# Implementation plan — `private-compose`

Spec: `docs/specs/2026-09-21-private-compose-target.md`

Order matters: task 1 must leave `vps-traefik` provably unchanged before anything new is
built on top of it. Each task states how its verification is shown to fail, not only to pass —
a check never seen failing guards nothing.

### Task 1 — Extract the shared compose mechanics into `deploy/compose.py`

Move what both shapes do identically into a `ComposeTarget` base: `Parameters` common to both,
`compose_at(sha)` (git show at the released commit), `remote_script` (lock, heredocs, digest
pull, `up --detach --wait`, `current` symlink), `ssh_argv`, `apply` with its LOCKED / timeout /
failure handling, `_wait_healthy`, `live_version` and `verify`. The base exposes two seams: the
`.env` a target writes, and the origin its verification talks to. `vps_traefik.VpsTraefik`
becomes a subclass supplying `DOMAIN`, `TRAEFIK_NETWORK`, `TRAEFIK_CERT_RESOLVER` and
`https://<domain>`.

**Verification**: `uv run pytest tests/test_deploy_vps_traefik.py -q` must pass with the test
file byte-identical to its state at `main` — assert with `git diff --exit-code
tests/test_deploy_vps_traefik.py` returning exit code 0. Shown to fail: change one `.env` key
name in the extracted base and expect the suite to go red before reverting.

### Task 2 — `deploy.bind_address` and the published-port check

Add `deploy.bind_address` to `GATE_DEFAULTS` with `None` as its value, so a target that needs
it and does not have it fails loudly rather than falling back. Add
`published_ports_are_private(compose_text, bind_address)` returning the offending
`(service, port)` pairs: every entry under a service's `ports:` — short form
`"[HOST_IP:]HOST:CONTAINER[/proto]"` and long form with `host_ip` — must name a host address
equal to `bind_address` or a loopback (`127.0.0.1`, `::1`, `localhost`). `expose:` is not
publishing and is ignored; a service with no `ports:` is fine.

**Verification**: assert that a compose declaring `"8080:8080"` yields exactly one offender
naming the service and the port, that the same file bound to the target's address and to
`127.0.0.1` yields none, and that the long form with `host_ip` is read as well as the short
one. Shown to fail: drop the loopback from the accepted set and expect the `127.0.0.1` case to
be reported as an offender.

### Task 3 — The `private-compose` target

Add `PRIVATE_COMPOSE = "private-compose"` to `DeployTarget`, and
`deploy/private_compose.py` with a `PrivateCompose(ComposeTarget)` that writes
`BIND_ADDRESS` instead of the Traefik trio, verifies against the origin of
`deploy.healthcheck` (scheme, host and port kept verbatim), and refuses the deployment from
task 2's check before the first ssh. `Target.domain` carries the private authority
(`host:port`) so the `deployed` attestation keeps one shape for both targets.

**Verification**: assert that `steps()` on a `private-compose` manifest names the private
address in all three steps, that no rendered `.env` line matches `TRAEFIK` or `DOMAIN=`, and
that `apply()` raises `DeployError` naming the service and port for a publicly published
compose **without invoking the ssh runner at all** — assert the injected runner recorded zero
calls. Shown to fail: skip the check in `apply()` and expect the runner to record one call.

### Task 4 — Dispatch both targets from the flows

Replace the single-target guard in `deploy/flow.py:make_target` with a mapping from
`DeployTarget` to its class, keeping the same error for a target with no module
(`pc-server-systemd`). The forward, rollback and drill sequences must not branch on the target.

**Verification**: assert `make_target` returns the right class for each implemented target and
still raises `DeployError` naming `pc-server-systemd` as unimplemented; assert the drill flow
writes the same attestation kinds against a fake `private-compose` target as it does against
the existing fake. Shown to fail: point the mapping at the wrong class and expect the type
assertion to go red.

### Task 5 — Manifest, template and documentation

Accept `private-compose` in `copier.yml`'s `deploy_target` choices, and let the scaffold's
prod healthcheck follow the target (the public domain for `vps-traefik`, the private address
for `private-compose`). Record the new parameter and the refusal rule in `CLAUDE.md`'s
architecture section.

**Verification**: assert a rendered `tier: prod` + `private-compose` project carries a manifest
whose `deploy.target` is `private-compose` and whose healthcheck is not an `hawkixs.com` URL,
and that `rail check` on this repository still reports 18/18 — capture the exit code, it is the
verdict. Shown to fail: leave the healthcheck on the public default and expect the rendered
manifest assertion to go red.
