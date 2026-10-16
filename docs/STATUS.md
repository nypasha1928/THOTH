# THOTH release candidate — 2026-10-16

Implementation is prepared for review. Paper, testnet and separately gated live modes are present. Isolated margin is the selected default. No real account is connected; both exchange server flags and entry switches remain disabled in this workspace.

Verified locally:

- 78 unit/API/integration tests passed in a clean Python 3.12 environment using pinned dependencies.
- Stateful exchange simulation covered entry, stop verification, partial/rejected/ambiguous outcomes, emergency reduce-only closing, reversal ordering, restart recovery, daily/position loss shutdown and account binding, including stops closing before the first poll and entries filling after close-all cancellation.
- Incremental/pruned indicator state matched 6,000 bars of unpruned replay, including checkpoint restoration.
- Mobile browser checks passed separate paper/testnet/live views, saved settings, isolated default, locked activation, chart overlays/navigation/tap inspection and no horizontal overflow/JavaScript errors.
- Hardened Docker image built successfully from TLS-downloaded wheels. Non-root/read-only-root runtime, private SQLite modes, writable data volume, health endpoint and disabled exchange gates passed checks.
- Private password-file login/logout checks passed in the container over internal HTTP. This did not validate a publicly deployed HTTPS certificate.
- Backup integrity/restore and secret/database exclusion from the source bundle passed.
- Source regeneration matched the committed generated core; Compose and cloud-init YAML parsed. Actual VPS/cloud-init execution remains untested.

External acceptance still needed:

1. Hosting account/payment authorization and a permitted VPS/domain. None exists yet.
2. Real TradingView reference data; no matching reference receipt exists.
3. Private testnet credentials and actual exchange acceptance. Both Bybit production and testnet public endpoints returned HTTP 403 country restrictions from this cloud machine on 2026-10-16.
4. Actual domain/HTTPS/login and phone installation checks on the deployed host.
5. Separate live credentials and explicit live activation only after reference/testnet acceptance.

Synthetic chart screenshots are labeled TEST FIXTURE. They are not real trades. Simulator fixtures cannot create the real-exchange testnet acceptance receipt. A code release and passing tests are not proof of strategy profitability or production trading safety.

The deployable archive is generated under ignored `dist/`; it excludes databases, private files, virtual environments and Git metadata and has a checksummed manifest. Source is intended for the `codex/thoth-app-20261016` review branch. Public deployment, paid provisioning and real account trading have not occurred.
