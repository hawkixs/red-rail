# `private-timers`: delivering scheduled oneshot units that systemd runs

Status: approved — 2026-10-06 (operator, design validated in the red-rail session)

Motivation: unblocks a product delivery — red-backup moves to the private service host, and
brain-v42 does not move there before red-backup has taken and tested a first dump on it
(operator decision `a96d5d53`). It also removes a class of operator load (ticket `c5231125`): a
batch project can no longer be delivered only by hand.

## Problem

Everything deployed on the private service host goes through the rail (decision `5dd6da0e`).
red-backup has to run there, and the operator wants it in its real shape: systemd timers firing
oneshot units, with no long-running process and no artificial health endpoint ("je veux un truc
clean").

No target can deliver that. `private-systemd` delivers one long-running service built from a
single binary, and verifies it over HTTP (`/health`, then `/version`); its spec lists timers and
oneshot units as a non-goal. red-backup is a Python application, not a binary. It runs today
from a checkout's virtual environment, through 20 hand-installed units, and needs the Docker
socket (dumps through `docker exec`), systemd credentials (`LoadCredential=`) and strict
hardening.

The first delivery covers red-backup's core only: the run, the brain restore drill, the watchdog
(three timers) and the alert unit (a service started through `OnFailure=`).

## Decisions

1. **A sister target named for its shape: `private-timers`.** Oneshot units fired by systemd
   timers, on a host with no public route. `private-systemd` does not change: making its
   healthcheck optional and its unit a list would change the properties of a target already in
   production, and a target's contract must not depend on what a manifest happens to declare.
   `private-timers` joins `PRIVATE_TARGETS`.

2. **The carrier is an OCI image, and `rail release` does not change.** The image holds a
   relocatable payload directory, named by `deploy.payload` (an absolute path inside the image):
   for red-backup a self-contained Python and the application, started as `python -m <package>`
   with no absolute path baked in. The target copies that directory out of the image by digest
   into the release. systemd runs it natively: the hardening, `LoadCredential=` and
   `SupplementaryGroups=docker` work exactly as they do today. Running a container per execution
   (`docker run` in each unit) was set aside: it needs the Docker socket inside the container and
   translates every systemd protection into Docker options.

3. **The units are versioned in the project and delivered by the rail.** `deploy.units` lists
   them, relative paths read at the released commit, each a plain name ending in `.service` or
   `.timer` (`[a-z0-9]+(-[a-z0-9]+)*\.(service|timer)`). A timer `X.timer` triggers `X.service`,
   which must be in the list. `deploy.version_command` is the command, relative to the payload,
   that prints the release's identity (decision 8). `private-timers` requires `payload`, `units`
   and `version_command`, and refuses `healthcheck`, `unit` and `binary`; every other target
   refuses the three new keys. `deploy.site` works as on the other private targets.

   ```yaml
   deploy:
     target: private-timers
     site: <site>
     payload: /opt/red-backup
     units:
       - deploy/systemd/red-backup.service
       - deploy/systemd/red-backup.timer
       - deploy/systemd/red-backup-brain-drill.service
       - deploy/systemd/red-backup-brain-drill.timer
       - deploy/systemd/red-backup-watchdog.service
       - deploy/systemd/red-backup-watchdog.timer
       - deploy/systemd/red-backup-alert.service
     version_command: [bin/python3, -m, backup, version, --json]
   ```

4. **The layout mirrors `private-systemd`.** A release lives in
   `<stack_root>/<project>/releases/<version>/`: the payload under `app/`, the units, and
   `release.env` (`VERSION`, `GIT_SHA`, `IMAGE_DIGEST`, `IMAGE_REFERENCE`, never a secret). The
   `current` symlink points at the live release; `.deploy.lock` is the lock. Each unit is linked
   once at migration, `/etc/systemd/system/<unit>` → `<stack_root>/<project>/current/<unit>`, so
   moving `current` moves every unit with the payload, a rollback included.

5. **One remote script, run as the deploy account under the lock.** In order:
   1. If any declared unit is `activating` or `active`, stop before any change, naming it. A
      oneshot run uses the files of the release it started from, and a Python process loads its
      modules as it goes: switching `current` under it would mix two releases. The operator
      deploys again later; the script never waits, so a long dump never holds the lock.
   2. Write the units and `release.env` into the release directory.
   3. `docker pull <repository>@<digest>`, `docker create` with an explicit command, `docker cp`
      the payload directory under a temporary name, then rename it to `app/`. The container is
      removed on every exit path.
   4. Point `current` at the release.
   5. Through sudo, `systemctl daemon-reload`; then, unprivileged, `systemctl show` every
      declared unit and stop when a drop-in applies to it or when its `FragmentPath` is neither
      its link nor `current/<unit>`.
   6. Through sudo, `systemctl restart <timer>` for each declared timer. Services are never
      started by the rail: the timers fire them.
   7. Unprivileged, check that each timer is `active` and has a next elapse.

6. **The privileged commands are fixed lines, installed by red-watcher.** `systemctl
   daemon-reload` and one `systemctl restart <timer>` per declared timer, in
   `/etc/sudoers.d/`, mode 0440, checked with `visudo -cf`. The rail never edits sudoers and
   never runs as root. As for `private-systemd`, the deploy account is already in the docker
   group; the rule makes each privileged action bounded, named and auditable.

7. **Each unit is refused before the first ssh when it would run as root, run unbounded, or run
   something other than the release.** The parser and its allow-list stance are shared with
   `private-systemd`. Each refusal names the unit and the rule.
   - A service: `Type=oneshot`; `User=` set to a plain name other than root; `MemoryMax=` a byte
     count (no swap on the host); `TimeoutStartSec=` (or `TimeoutSec=`) a positive duration,
     since it bounds a oneshot run; every `Exec*=` program under `<stack_root>/<project>/current/app/`;
     `EnvironmentFile=<stack_root>/<project>/current/release.env` without the `-` prefix; no
     `+`, `!` or `!!` prefix and no `PermissionsStartOnly=`; no quoting, backslash or `;` that
     would make systemd read another command; no invisible character; `RemainAfterExit=`
     absent or false (a oneshot left `active` is never fired again by its timer, and would
     block every later deployment); no `AmbientCapabilities=`; in `[Unit]`, `OnFailure=` and
     `OnSuccess=` name only declared units, and `FailureAction=`, `SuccessAction=`,
     `StartLimitAction=` and `JobTimeoutAction=` are absent or `none` (PID 1 performs them as
     root).
   - A timer: a `[Timer]` section with at least one trigger (`OnCalendar=`, `OnBootSec=`,
     `OnUnitActiveSec=`, `OnUnitInactiveSec=`, `OnActiveSec=`, `OnStartupSec=`); no `Unit=` key,
     so the timer triggers the service of the same name; that service is declared; no key in
     `[Unit]` that starts, stops or acts on another unit (`Wants=`, `Requires=`, `Requisite=`,
     `BindsTo=`, `PartOf=`, `Upholds=`, `Conflicts=`, `OnFailure=`, `OnSuccess=` and the
     propagation keys), since the rail restarts every timer through sudo; no invisible
     character.
   - `deploy.units` declares at least one timer.

8. **Verification without HTTP: what systemd loaded, and which release runs.** After the
   script, the rail runs, over ssh and unprivileged, the release's `version_command` from
   `current/app/`. It prints a JSON object (`project`, `version`, `git_sha`, `image_digest`, read
   from `release.env`) that the rail compares with the artefact, field by field, exactly as it
   compares `/version` today. A mismatch fails the deployment.

9. **`observe.visible` proves that the delivered timers ran.** red-monitor already collects, for
   each unit it watches, `ActiveState`, `SubState`, `Result`, `ExecMainStatus`,
   `ExecMainExitTimestamp`, `LastTriggerUSec` and the next elapse. On `private-timers` the gate
   requires, on `observe.monitor_agent`:
   - each declared timer `active`/`waiting`, with a next elapse;
   - the service each timer triggers has exited with `Result=success` and status 0 **after** the
     newest `deployed` attestation.

   Until then the gate fails, naming the service still waiting and its next elapse. For
   red-backup it turns green after the first night: run, brain drill, watchdog. That is the
   tested first dump brain-v42 waits for. The alert service, fired by `OnFailure=`, is not
   required to have run.

10. **Rollback and drill are the existing flows.** Re-applying the previous artefact points
    `current` back at its release, reloads systemd and restarts the timers; the verification of
    decision 8 proves the previous release is back. `rail drill` measures that recovery, then
    rolls forward. It exercises the rail's way back, not the backups: red-backup's own restore
    drill is a delivered unit, and decision 9 already requires it to succeed.

11. **The shared mechanics stay in `deploy/remote.py`.** The ssh step, the lock, the timeout,
    reading a file at the released commit, the copy out of an image by digest (a binary or a
    directory), the release environment and the unit parser are shared, not copied.
    `private_timers.py` holds its script, its unit rules and its verification. The existing
    targets' tests pass unchanged.

## What the first project provides (red-backup)

- **A `Dockerfile` whose image holds `/opt/red-backup`**: a standalone Python (for example
  python-build-standalone) and the application installed without absolute paths, so that
  `bin/python3 -m backup …` runs from any directory.
- **`red-backup version --json`**: `project`, `version`, `git_sha`, `image_digest`, read from
  `VERSION`, `GIT_SHA` and `IMAGE_DIGEST`.
- **The seven units, rewritten for the release**: `ExecStart=<stack_root>/red-backup/current/app/bin/python3 -m backup …`,
  `EnvironmentFile=<stack_root>/red-backup/current/release.env`, a `MemoryMax=` from a measured
  peak, the `ExecStartPre=docker image inspect …` checks moved into the application (no program
  outside the release), configuration and secrets in `/etc/red-backup`, the hardening kept.
- **Sources reached through export accounts** (red-watcher's work), since the run moves away from
  the machine it used to dump locally.
- **The manifest** of decision 3, with `deploy.ssh_host` declared for the site.

## Migration on the host (once, by the operator and red-watcher)

1. Hand `<stack_root>/red-backup` to the deploy account; configuration and credentials in
   `/etc/red-backup`.
2. Install the sudoers file of decision 6 and check, as the deploy account, that `sudo -n -l`
   lists exactly those commands.
3. Immediately before the first `rail deploy`, link the seven units (decision 4) and
   `systemctl enable` the three timers. Until the first delivery creates `current`, the links
   point at nothing.
4. Add the seven units to red-monitor's agent configuration on that host (decision 9).

The first drill needs two red-backup releases.

## Non-goals

- **The PITR units, the red-writer secrets units and the PITR tunnel.** They stay where they are
  for now; their shape (continuous WAL, a long-running tunnel) is decided separately.
- **Long-running services in this target**, unit templates (`name@.service`) and socket units.
- **Starting a run from the rail**, in a deployment or in a drill.
- **Waiting for a running unit to finish**: decision 5.1 refuses and the operator retries.
- **A data restore drill in the rail**: red-backup's drill is a delivered unit.
- **Several targets in one manifest**: red-backup runs on one host.
- **Exposing the spool depth to red-monitor** (suggested by red-watcher): later.
- **Editing sudoers from the rail**, running any part of the rail as root, secrets in
  `release.env`.

## Success criteria

1. A `private-timers` manifest loads; one that lacks `payload`, `units` or `version_command`,
   or that declares `healthcheck`, `unit` or `binary`, is refused with the key named; every other
   target refuses the three new keys.
2. Each refusal of decision 7 fails before the first ssh, naming the unit and the rule; a timer
   whose service is not declared is refused.
3. The remote script runs the steps of decision 5 in that order, stops before any change when a
   declared unit is running, removes the container on every exit path, calls exactly the
   commands of decision 6, and never writes outside `<stack_root>/<project>`.
4. The verification of decision 8 accepts the matching identity and fails, naming the field, on
   any mismatch or on an unreadable answer.
5. `observe.visible` on `private-timers` passes when every timer is `active`/`waiting` and every
   triggered service succeeded after the newest deployment, and otherwise fails naming the unit;
   the other targets behave exactly as before.
6. Forward, rollback and drill write the same attestation sequences as for the other targets,
   with no branch on the target inside the flows.
7. The tests of `vps-traefik`, `private-compose` and `private-systemd` pass unchanged.
8. On the private service host, after the migration and red-backup's changes: `rail deploy`
   delivers red-backup and attests `deployed`; `observe.visible` turns green after the first
   night; `rail drill` measures a rollback between two releases.
9. `make ci` is green, `rail check` passes on this repository, and
   `tests/test_no_machine_address.py` stays green.
