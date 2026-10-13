# Metrics publication

Install the units from this checkout and start the publisher timer and local server:

```sh
systemctl --user link "$PWD"/contrib/systemd/user/rail-metrics-publish.service
systemctl --user link "$PWD"/contrib/systemd/user/rail-metrics-publish.timer
systemctl --user link "$PWD"/contrib/systemd/user/rail-metrics-serve.service
systemctl --user enable --now rail-metrics-publish.timer
systemctl --user enable --now rail-metrics-serve.service
```

The publisher runs hourly and after a missed scheduled run. A block is stale when its
`generated_at` timestamp is more than two hours old (spec D9).

red-monitor's agent probes `127.0.0.1:9230/rail-metrics` and relays the `delivery` and
`autonomy` blocks. The server listens on loopback only.

For the first real run of success criterion 1, run `rail metrics publish` once on the host.
Then verify that both blocks are present, `coverage` reports the expected repositories, and
the compact served answer is at most 12 KiB. Record the measured size in the pull request that
installs these units.
