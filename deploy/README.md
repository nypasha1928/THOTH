# THOTH hosting and account deployment

The software and deployment files are prepared; no VPS, domain, certificate or exchange account has been purchased/provisioned here. Paid resources need a provider account/payment authorization. A reasonable starting target is an x86-64 Ubuntu 24.04 VPS with 2 GB RAM, 1–2 vCPU and sufficient persistent disk. Actual price/region availability must be checked with the provider before purchase.

This cloud workspace's Bybit production requests returned a country restriction. Host where your use is permitted and the required APIs are accessible. A VPN on your phone does not apply to the server. API-key IP restrictions must use the server's stable egress IP. No VPN bypass is configured by THOTH.

## 1. Prepare the host

Use your provider account and SSH public key to create a **new** Ubuntu 24.04 VPS. `cloud-init.yaml` prepares Docker/Compose, key-only SSH, firewall ports 22/80/443, time synchronization and signed package/security updates. It is a prepared template, not a validated provisioned host. Do not apply it blindly to an existing/custom-SSH-port machine.

Point a domain A record at the server (and only set AAAA if IPv6 really reaches it). Keep app port 8080 private. HTTPS is handled by Caddy after DNS reaches this host. Domain registration and DNS changes have not occurred here.

## 2. Upload a verified release

Build with `python tools/package_release.py dist/thoth-release.tar.gz`. Transfer the archive and its SHA-256 file over SSH using your own secured access. On the VPS:

```bash
sha256sum --check thoth-release.tar.gz.sha256
mkdir /opt/thoth-release
# The archive has a THOTH/ top-level directory.
tar -xzf thoth-release.tar.gz -C /opt/thoth-release
cd /opt/thoth-release/THOTH
```

Review the release manifest and scripts. Run `sudo THOTH_DOMAIN=your-actual-domain deploy/install.sh` only on the intended host. Replace the placeholder with your domain. The installer generates a password directly in `/etc/thoth/dashboard-password`, mode 0400, owned by app UID 10001, beneath a root-private directory. It never prints the password or sends it anywhere. Retrieve it through your secured server administration session—not chat or GitHub.

The default install starts paper mode only, waits for application health, and leaves both exchange gates disabled. Caddy's actual HTTPS certificate must then be checked from outside the server. Use `tools/server_check.py` with a private local password file to verify HTTPS/login isolation without trading actions.

## 3. Configure private testnet files

Create private files outside the checkout for the testnet key and secret, using a secure editor/secret manager. Files must be owned by UID 10001 with mode 0400, with private parent directories. Do not put credential values in commands, shell history, source, build arguments or `.env`.

Set only non-secret paths/hostname in your root-owned deployment environment:

- `THOTH_PUBLIC_HOST`: domain hostname only.
- `THOTH_PASSWORD_PATH`: `/etc/thoth/dashboard-password`.
- `THOTH_BYBIT_TESTNET_KEY_PATH` and `THOTH_BYBIT_TESTNET_SECRET_PATH`: the private file paths.

A root-private, Git-ignored `.env` may contain those paths. Files are mounted read-only. HMAC needs actual server-side secrets: a network-proxy placeholder is not interchangeable. The app itself runs as UID 10001 with read-only root filesystem, a writable data volume, temporary RAM filesystem, no Linux capabilities and no-new-privileges. Docker administrators/root can still access application memory/files; this is not a guarantee against server compromise.

For read-only checks:

```bash
docker compose -f compose.yaml -f compose.testnet.yaml up --build -d --wait
```

Sign in on your phone and use **Check connection**. It verifies account read access and rejects unknown/withdrawal-enabled wallet permissions. Keys and account IDs are never returned. This check alone never enables orders.

## 4. Testnet execution acceptance

Use a dedicated, USDT-only UTA 2.0 account with no unrelated positions/orders, one-way mode, and **isolated margin**. The bot verifies this; it never silently changes account-wide margin mode. The testnet key needs contract Order and Position permissions (or applicable DerivativesTrade) and withdrawals disabled.

To enable the server-side testnet feature:

```bash
docker compose -f compose.yaml -f compose.testnet.yaml -f compose.execution.yaml up --build -d --wait
```

Select Testnet funds in the app, configure symbols/size/risk, acknowledge the dedicated account, save, and type `ENABLE TESTNET ORDERS` in the activation dialog. The first observed candle warms up; only later confirmed signals may enter. Complete actual entries, stops, closes/reversals and reconnect/restart tests. Verify all results on Bybit itself. Simulator unit tests are not this acceptance test.

Pause entries, close/reconcile the account, then create a receipt using `tools/accept_testnet.py` against the persisted testnet database. Real credentials and an actual exchange result are required; fake transport/simulator records do not qualify. Do not generate or invent a receipt to bypass testing.

## 5. TradingView reference comparison

Generate the export helper with `tools/reference_export.py`, export matching chart data, and run `tools/compare_indicator.py` as described in the root README. Store current receipts under the data volume's `validation/` directory. The dataset must cover enough bars, all level families and both signal directions. Code/strategy changes invalidate matching receipts. No actual TradingView export has been supplied in this workspace.

## 6. Live is a separate, explicit step

Only after reference and actual testnet acceptance, provision separate **live** key/secret files with withdrawals disabled and the server IP whitelist enabled. Live also requires a dedicated USDT-only UTA 2.0 account in the selected isolated margin mode.

Use `THOTH_BYBIT_LIVE_KEY_PATH` and `THOTH_BYBIT_LIVE_SECRET_PATH` and the optional `compose.live.yaml` override. That flag does not activate orders. Select LIVE funds and type `ENABLE LIVE ORDERS` only after independently reviewing the checks. This can trade real funds. No live account has been exercised here.

After restart, recorded exit obligations can recover with entries paused; validation receipts are required again to enable new live entries. Unknown outcomes are reconciled, not blindly resubmitted. Keep regular monitoring: account-level emergency exits need network connectivity and can overshoot thresholds. Initial attached stops and dollar stops are not guarantees of an exact final loss.

## Backups, upgrades and recovery

Use one app process and one data volume per instance. The volume holds paper/testnet/live state, candle checkpoints and execution journals. Never discard/recreate that volume during upgrades.

Stop the app for an application-consistent snapshot, then run `tools/backup_data.py backup DATA_DIRECTORY NEW_PRIVATE_BACKUP_DIRECTORY` from a trusted admin context. It copies every `*.sqlite` using SQLite's backup API, verifies integrity and writes a checksummed manifest last. Credentials are not included and need a separate secured backup process. Keep backups private and off-server using an appropriately protected storage service.

Use `verify` before `restore`, restore into a **new** directory, and test it before switching the app volume. The tool refuses overwrite and detects modified backup contents. Restored real-account state must be reconciled against the exchange before any new entries; a backup is not proof that positions/orders are still current.

The app's session cookies expire after eight hours. Logout revokes the current token; restarting the app invalidates all sessions. Login throttling is process-local. PWA installation needs HTTPS and phone support; offline requests are never queued. Push notifications are not implemented.

## Restricted build-network fallback

The ordinary Dockerfile uses signed/TLS-verified package downloads. In this cloud machine, container DNS could not reach the package registry, so the image was built from wheels downloaded through the host's supported TLS route:

```bash
python -m pip download --dest /tmp/thoth-wheelhouse -r requirements.txt
docker build --build-context wheelhouse=/tmp/thoth-wheelhouse -f deploy/Dockerfile.offline -t thoth:release-review .
```

Use a wheelhouse matching Python/CPU architecture. Never disable TLS, signatures or expected checksums to make installation pass. Normal VPS builds can use the root Dockerfile.
