# `private-systemd`: delivering a binary that systemd runs

Status: proposed — 2026-09-24

## Problem

red-monitor asks for it (ticket `f91396ef`). Its agent on the private service host was installed
by hand on 2026-09-21. That is a known, accepted deviation from the rule that everything deployed
there goes through the rail (decision `8d4959fc`, ticket `de1f3be0`). Closing it means
`rail deploy` delivers the agent, attests `deployed`, and `rail drill` measures a rollback of it.

No target can do that. `vps-traefik` and `private-compose` deliver compose stacks only.
`pc-server-systemd` is an enum value with no module behind it: spec
`2026-09-21-private-compose-target` left it there until something needed systemd, and this is
that something.

What has to be delivered, measured on 2026-09-24:

- A static Go binary of about 19 MB. A hardened unit, `red-agent.service`, runs it as a dedicated
  system user whose supplementary group gives it the docker socket. No image runs on the machine.
- Its health answers on `/health` of the agent's private listener. It has no `/version`.
- About 9 MiB resident, with a peak of 12 MiB over two and a half days. The unit sets no
  `MemoryMax`, on a machine that has no swap.
- The agent stops only at `TimeoutStopSec` (15 s): it does not honour SIGTERM yet.
- The deploy account holds no sudo right at all today.

Containerising the agent was considered and set aside. It measures the host (processes, disks,
the docker socket, systemd units), so moving it into a container changes what it measures, and
the ticket asks for systemd.

## Decisions

1. **A target named for its shape: `private-systemd` replaces `pc-server-systemd`.** The name
   passes the test of decision 1 of the private-compose spec: it describes the shape ("a binary
   that systemd runs, no public route"), not a machine. The old value names a machine and has
   never been declared: no sibling `rail.yaml` carries it (checked 2026-09-24). It is removed
   rather than kept as an alias.

2. **The carrier is an OCI image, and `rail release` does not change.** The release builds the
   project's image, pushes it by digest and attests `released`, exactly as today; `Artefact` keeps
   its four fields. The target pulls that image by digest and copies the binary out of it. systemd
   runs the binary, never a container. `deploy.binary` names the binary's absolute path inside the
   image. The manifest check makes it an absolute path made only of safe characters, with no `..`
   component. One release path serves every target, and the registry login the deploy account
   already holds for `private-compose` serves this one too.

3. **The unit file is versioned in the project and delivered by the rail.** `deploy.unit` names it
   (for example `deploy/red-agent.service`), and it is read at the released commit, like the
   compose file. The file's name is the unit's name. systemd loads it through a link that points
   at the live release (decision 6), so a rollback restores the previous unit along with the
   previous binary. `MemoryMax`, `TimeoutStopSec` and the hardening therefore never drift outside
   the rail. `deploy.unit` is a relative path inside the repository, with no `..`
   component. `private-systemd` requires both `unit` and `binary`, and every other target refuses
   them when the manifest loads.

4. **The layout mirrors compose.** A release lives in `<stack_root>/<project>/releases/<version>/`:
   the binary, the unit, and `release.env`. The `current` symlink points at the live release, and
   `.deploy.lock` is the lock. `release.env` carries `VERSION`, `GIT_SHA`, `IMAGE_DIGEST` and
   `IMAGE_REFERENCE`, never a secret: a secret stays in a host file that the unit names itself.

5. **One remote script, run as the deploy account under the lock.** In order:
   1. Write the unit and `release.env` into the release directory.
   2. `docker pull <repository>@<digest>`.
   3. `docker create` with an explicit command, so a carrier image without `CMD` works too.
   4. `docker cp` the binary into the release directory, under a temporary name, then `chmod 0755`
      and rename it. The container is removed on every exit path.
   5. Point `current` at the release.
   6. Through sudo: `systemctl daemon-reload`, then `systemctl restart` the unit.
   7. `systemctl is-active` the unit, unprivileged.

   The restart is unconditional. Redeploying the same version restarts the service and so applies
   a changed host file, which the compose targets do not do (ticket `e3278ea3`).

6. **The unit is linked, never copied: two privileged commands, with fixed arguments.**
   `/etc/systemd/system/<unit>` is a symbolic link to `<stack_root>/<project>/current/<unit>`,
   created once at migration. Moving `current` therefore moves the unit with the binary. What
   systemd reads, at a `daemon-reload` as after a reboot, is always the live release's unit, and
   there is no copy that could disagree with it.

   The only privileged actions left are the reload and the restart. red-watcher installs them per
   unit; the rail never edits sudoers. For red-monitor's agent, in `/etc/sudoers.d/`, mode 0440,
   checked with `visudo -cf`:

   ```
   <deploy-user> ALL=(root) NOPASSWD: /usr/bin/systemctl daemon-reload
   <deploy-user> ALL=(root) NOPASSWD: /usr/bin/systemctl restart red-agent.service
   ```

   Copying the unit with `sudo install` was considered and set aside, for two reasons found on the
   host on 2026-09-24. `sudo` there is sudo-rs, and `/usr/bin/install` is a symbolic link into
   uutils' coreutils; sudo-rs does not document how it matches a command reached through a link.
   A copy could also disagree with `current` after a partial run.

   To be plain about what the rule buys: the deploy account is already in the docker group, which
   is equivalent to root. The rule adds no power it does not already have. It makes each
   privileged action bounded, named and logged by sudo, which red-watcher can audit.

7. **The unit is refused before the first ssh when it would run as root, run unbounded, or run
   something other than the release.** Each refusal names the unit and the rule:
   - `User=` is absent, `root` or `0`;
   - `MemoryMax=` is absent or `infinity`;
   - `TimeoutStopSec=` is absent or `infinity`;
   - there is not exactly one `ExecStart=`, or its program is not
     `<stack_root>/<project>/current/<binary name>`;
   - `EnvironmentFile=` does not name `<stack_root>/<project>/current/release.env`, with no `-`
     prefix, because the file must exist;
   - an `Exec*=` line carries the `+`, `!` or `!!` prefix (full privileges), or
     `PermissionsStartOnly=` is set;
   - the file name is not a plain unit name (`[a-z0-9]+(-[a-z0-9]+)*\.service`).

   The parser reads sections, `key=value` lines, comments and line continuations, and ignores the
   keys it does not check. Like decision 6, this guards against a mistake, not against a
   compromised deploy account.

8. **Verification is unchanged.** The rail polls `deploy.healthcheck` until it answers 200, then
   reads `/version` on the same origin and compares it with the artefact: `version`, `git_sha`,
   and `image_digest`, the carrier's digest. The binary reads the three values from `release.env`,
   through the unit's `EnvironmentFile=`.

9. **A site works as it does for `private-compose`.** `deploy.site` is accepted on
   `private-systemd`, with the same rule: the token is the healthcheck's host, and only the host.
   The address comes from the host's sites file, records name the site, and redaction is the same.
   The code that reads a site is shared by both private targets, not copied.

10. **The shared mechanics move to `deploy/remote.py`.** That covers the ssh step, the lock's exit
    code, the timeout, the tail of the remote output, reading a file at the released commit, and
    the whole verification (`/health`, then `/version`). `compose.py` keeps compose's remote
    script; `private_systemd.py` holds its own script and the unit refusals. The existing targets'
    tests pass unchanged, and that is the proof the extraction is refactoring.

11. **`observe.visible` reads the unit on a systemd target.** red-monitor's snapshot lists each
    agent's systemd units with their `active_state` and `sub_state`. On `private-systemd` the gate
    requires the unit that `deploy.unit` names to be `active`/`running` on `observe.monitor_agent`.
    Otherwise it fails, naming the unit and the state it saw. `monitor.py` learns to read those
    rows. The digest is proven at deployment by `/version`; the observation proves the service
    stayed up. Compose targets are unchanged.

12. **Rollback and drill are the existing flows.** Re-applying the previous artefact points
    `current` back at its release, which brings back its unit and its binary together, then reloads
    and restarts. On a first delivery the ledger has no previous artefact. A failure then leaves
    the incident open, as it does today, and the way back is manual: migration step 1 keeps the
    hand-installed unit.

## What the first project provides (red-monitor)

- **A `Dockerfile` at the repository root.** `rail release` builds from the root. The image red-monitor
  already builds carries the binary at `/usr/local/bin/red`, and can be the carrier as it is.
- **`/version` on the listener of `/health`.** HTTP 200 with a JSON object: `project`, `version`,
  `git_sha`, `image_digest`, read from `VERSION`, `GIT_SHA` and `IMAGE_DIGEST`. `IMAGE_DIGEST` cannot be
  compiled in, because it exists only once the image is pushed.
- **`deploy/red-agent.service`.** It is the unit running today, with its hardening kept and three
  changes:
  - `ExecStart=/opt/red-monitor/current/red agent -config /etc/red-monitor/agent.yaml`;
  - `EnvironmentFile=/opt/red-monitor/current/release.env`;
  - `MemoryMax=64M`, about five times the measured peak.
- **The manifest.**

  ```yaml
  deploy:
    target: private-systemd
    site: <site>
    healthcheck: "http://${BIND_ADDRESS}:9100/health"
    unit: deploy/red-agent.service
    binary: /usr/local/bin/red
  ```

- **Honouring SIGTERM.** This is not blocking: the restart waits for `TimeoutStopSec` inside the
  remote timeout.

## Migration on the host (once, by the operator and red-watcher)

1. Keep the hand-installed unit as `red-agent.service.pre-rail`: it is the way back if the first
   delivery fails.
2. Move `agent.yaml` to `/etc/red-monitor/agent.yaml` (`root:red-monitor`, 0640). Host
   configuration lives in `/etc/<project>`, as it does for red-alerts, and `/opt/<project>` belongs
   to the deploy account.
3. Hand `/opt/red-monitor` to the deploy account (decision `5c874978`).
4. Install the sudoers file of decision 6.
5. As the deploy account, check that `sudo -n -l` lists exactly those two commands.
6. **Immediately before the first `rail deploy`, in the same sitting**, replace
   `/etc/systemd/system/red-agent.service` with the link of decision 6. The existing
   `multi-user.target.wants` link already points at that path, so the unit stays enabled. Until
   the first delivery creates `current`, the link points at nothing. The running agent is not
   affected, because systemd keeps the unit it loaded, but a reboot in that window would leave the
   agent stopped.

The first drill needs two red-monitor releases, because a drill rolls back to a previous one.

## Non-goals

- **Pruning old releases and carrier images.** They are about 19 MB each. Measure after a month.
- **red-monitor's server, and other machines.** The target is machine-agnostic through
  `deploy.ssh_host` and `deploy.site`, but it is proven on the private service host only.
- **Unit templates (`name@.service`), timers and oneshot units.** The target delivers one
  long-running service per project.
- **Secrets in `release.env`.**
- **Editing sudoers from the rail**, or running any part of the rail as root.
- **A second release path**, such as GitHub release assets.
- **Containerising the agent.**
- **Ticket `e3278ea3` for the compose targets**, which stays open.

## Success criteria

1. `rail deploy --plan` against a `private-systemd` manifest prints "on <site>", the ssh alias,
   and the resolved healthcheck and `/version` URLs. The manifest carries no address.
2. Each refusal of decision 7 fails before the first ssh, naming the unit and the rule.
3. The remote script runs the steps of decision 5 in that order. It removes the container on
   failure, and it calls the two commands of decision 6 with exactly the arguments the sudoers
   file allows. It never writes outside `<stack_root>/<project>`.
4. `tests/test_deploy_vps_traefik.py` and `tests/test_deploy_private_compose.py` pass unchanged,
   except the test that used `pc-server-systemd` as its example of an unimplemented target.
5. `deploy/flow.py` builds all three targets from the manifest. Forward, rollback and drill write
   the same attestation sequences for all three, with no branch on the target inside the flows.
6. `observe.visible` passes for a systemd target whose unit is `active`/`running`, and otherwise
   fails naming the unit and its state. The compose targets behave exactly as before.
7. On the private service host, after the migration and red-monitor's changes:
   - `rail deploy` replaces the hand-installed agent with a delivered and attested one;
   - `rail drill` measures a rollback between two releases;
   - `rail check` is green at tier `prod` on red-monitor.

   This closes `f91396ef` and the deviation of `8d4959fc`.
8. `make ci` is green, `rail check` passes on this repository, and
   `tests/test_no_machine_address.py` stays green.
