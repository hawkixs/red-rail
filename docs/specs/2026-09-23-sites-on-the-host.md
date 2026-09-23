# Sites on the host: a private target's address never enters a repository

Status: proposed — 2026-09-23

## Problem

A `private-compose` project today carries its machine's address in its own repository, and the
operator has decided that it must not. When red-alerts, the pilot, first deploys to the private
service host, the address would appear in four places:

- `rail.yaml`, as the `gates: deploy.bind_address` override the target requires (spec
  `2026-09-21-private-compose-target`, decision 5);
- `rail.yaml` again, as the host of `deploy.healthcheck`, which the target follows for its
  verification (same spec, decision 3);
- every `deployed` attestation, whose `domain` field is the healthcheck's host. The receipt
  mirroring it is committed to `docs/receipts/` by the next receipts pull request;
- any `incident_detected` attestation, whose `reason` is the text of the error that stopped the
  deployment: an HTTP failure naming the URL, or ssh's own `connect to host <address>`.

The machine's other coordinate is already where it belongs. `deploy.ssh_host` is an alias, and
the host that runs `rail deploy` resolves it through its own ssh configuration. The address is
the one fact still missing on that side.

## Decisions

1. **A site is a label the repository declares; its address lives on the host.**
   `DeployConfig` gains an optional `site` field with the pattern `^[a-z0-9]+(-[a-z0-9]+)*$`.
   The pattern has no dot and no colon, so neither an IPv4 or IPv6 address nor a domain name can
   be written there. A repository says *which* machine, never *where* it is.

2. **The healthcheck names the address with the rail's own token.** Behind a site, the host of
   `deploy.healthcheck` is `${BIND_ADDRESS}`, the variable the rail already owns in the compose
   file (`http://${BIND_ADDRESS}:9204/healthz`). Only the braced form is the token in a URL. The
   URL still passes `^https?://`. The token and the site go together, and loading the manifest
   checks it:
   - a site whose healthcheck names a literal host is refused, since the verification could
     then reach another machine than the one the service is bound to;
   - a token without a site is refused, since nothing could fill it;
   - `site` is refused on any target but `private-compose`.

3. **One fact, one source.** A manifest that declares `site` and also overrides
   `deploy.bind_address` under `gates:` is refused. Without a site nothing changes: a project
   may still declare the address in `rail.yaml`, with its reason, exactly as today.

4. **The host file is `~/.config/red-rail/sites.yaml`**, or the path `RAIL_SITES_FILE` names.
   As with `RAIL_BRAIN_TOKEN_FILE`, the environment names the file and never holds the value.
   - It is read with `rail.private.read_private_file`: an absolute path, no symlink anywhere
     on it, a regular file owned by the current user, no group or other permission bits. The
     address is not a secret, but this file decides where a deployment lands, and nobody else
     may change that.
   - The schema is strict, and unknown keys are refused:

     ```yaml
     sites:
       private-1:
         address: 192.0.2.10
     ```

   - `address` is an IP literal, v4 or v6, because Docker binds an address, not a name. The
     unspecified addresses `0.0.0.0` and `::` are refused, since publishing everywhere is what
     this target exists to prevent.
   - A missing file, a file not private enough, an unknown site (the error lists the known
     ones) and an invalid address all fail while the target is built. That is before
     `rail deploy --plan` prints anything and before the first ssh, and each message names the
     site, the path and the fix.

5. **Only the target reads the host.** Gates, `rail check`, `rail audit` and CI never open the
   site file. The rules of decisions 1 to 3 are properties of the manifest alone, so a manifest
   with a site passes `rail check` where no host file exists, and gates stay pure functions of
   the repository's state. One exception, decision 8: `observe.visible`.

6. **The target resolves the site once, and everything downstream uses that address.** With a
   site, `PrivateCompose` reads the site's address and uses it everywhere the declared address
   is used today:
   - `BIND_ADDRESS` in the generated `.env`;
   - the refusal of a compose file publishing elsewhere (spec `2026-09-21-private-compose-target`,
     decision 4);
   - the healthcheck, where the token is replaced by the address, bracketed when it is IPv6
     (`http://[2001:db8::10]:9204/healthz`);
   - the origin of `/version`, derived from that resolved URL.

   Without a site, the target behaves exactly as today.

7. **What is attested names the site, never the address.**
   - Behind a site, `domain`, the attestation field and the "on …" of `rail deploy` and
     `rail drill`, is the site's name.
   - The target provides `redact(text)`, which replaces the address with the site's name and is
     the identity without a site. The flow's `Attester` applies it to every string of every
     payload, before the receipt mirror is written and before brain is called. This one boundary
     covers incident reasons (HTTP errors, ssh's stderr) and any string a later change adds.
   - Replacement respects number boundaries: redacting `192.0.2.1` leaves `192.0.2.10`
     untouched. The address is matched in its canonical text form, case-insensitively for IPv6.
     A bracketed IPv6 address is replaced brackets included, so a URL reads
     `http://private-1:9204/healthz`.
   - The operator's terminal is not the repository. `--plan`, errors and the report show the
     resolved address, and none of it is persisted.

8. **red-monitor is a site too** (amendment, 2026-09-23). This repository is public, and the
   versioned default of `observe.monitor_url` was a literal private address.
   - The default is now `http://${BIND_ADDRESS}:8081`. `observe.monitor_site` (default
     `red-monitor`, a label under decision 1's pattern) names the site whose address fills the
     token.
   - A project may still declare a literal `observe.monitor_url` in `rail.yaml`; without the
     token, no site is read.
   - `observe.visible` is the one gate that opens the site file. It already has `workstation`
     scope: it queries red-monitor over the network and is skipped under `--ci`. Decision 5's
     guarantees still hold: every manifest rule is checked without a host file, and CI never
     needs one.
   - Without the declaration, the gate fails closed with `load_site`'s message (site, path,
     fix). Its details redact the address to the site's name, as decision 7 does for records:
     `rail check` output is pasted into issues and pull requests.

## Non-goals

- **No `rail new --site`.** The pilot's manifest is written by hand. The scaffold follows when a
  second private project arrives.
- **`deploy.ssh_host` and `observe.monitor_agent` stay declared per project.** They are labels,
  not addresses. The site file's per-site mapping leaves room for them later.
- **`vps-traefik` does not change.** A public domain has nothing private to hide.
- **No change to the attestation vocabulary.** `domain` keeps its name; behind a site, its value
  is the site's name.
- **Existing receipts are not rewritten.** No deployment through a private target has happened
  yet.
- **No domain names as site addresses.** Resolution would add a second place where the answer
  can change silently.

## Success criteria

1. A manifest with a site and a tokenised healthcheck passes `rail check` with no host file,
   locally and under `--ci`.
2. Each refused combination (a site with an address-like name, a site on `vps-traefik`, a token
   without a site, a site without the token, a site with a `deploy.bind_address` override) fails
   when the manifest loads, with a message naming the rule.
3. With a host file, `rail deploy --plan` prints "on <site>", the ssh alias, and the resolved
   healthcheck and `/version` URLs. Without one, it fails before printing anything, naming the
   site and the path.
4. A deployment that fails with the address in its error text produces an `incident_detected`
   and a `deployed` record that contain the site's name and not the address. This holds in the
   receipt mirror and in the payload sent to the ledger.
5. `make ci` is green, `rail check` passes on this repository, and the mutation counter-proof
   kills a mutant that removes the redaction, one that drops decision 3, and one that forgets
   the IPv6 brackets.
6. After red-alerts' first deployment through a site, `git grep` for the address in red-alerts,
   receipts included, finds nothing.
