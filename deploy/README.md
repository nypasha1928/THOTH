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

The default install starts the live dashboard, waits for application health, and leaves live execution disabled. Caddy's actual HTTPS certificate must then be checked from outside the server. Use `tools/server_check.py` with a private local password file to verify HTTPS/login isolation without trading actions.

## 3. Configure private live files

Use a dedicated USDT-only UTA 2.0 account, one-way position mode and the selected isolated margin mode. Create a live API key with contract order/position permissions, withdrawals disabled and an IP whitelist matching the server's stable public egress address. The app verifies these prerequisites and does not silently change account-wide margin mode.

Store the live key and secret outside the checkout, under a private directory such as `/etc/thoth`. Each mounted credential must be owned by app UID 10001 with mode 0400. Never put key/secret values in commands, shell history, Git, `.env`, dashboard forms or chat.

Set deployment paths only:

```bash
export THOTH_PUBLIC_HOST=app.your-domain.example
export THOTH_PASSWORD_PATH=/etc/thoth/dashboard-password
export THOTH_BYBIT_LIVE_KEY_PATH=/etc/thoth/bybit-live-key
export THOTH_BYBIT_LIVE_SECRET_PATH=/etc/thoth/bybit-live-secret
docker compose -f compose.yaml -f compose.live.yaml up --build -d --wait
```

For the already-authorized unverified indicator strategy, include `-f compose.live-unverified.yaml` before `up`. This skips the indicator reference prerequisite only, preserves account/risk checks, and never automatically enables entries. No testnet credentials or acceptance receipt are used.

Open the app over HTTPS and use **Check connection / refresh balance**. That operation reads account status and USDT balance; it cannot place an order. Configure symbols and risk, save, then explicitly type `ENABLE LIVE ORDERS` to activate. A server restart leaves new entries paused while known exit obligations recover. Verify owned positions and stops on Bybit as well as THOTH.

## 4. Existing deployment upgrade

Pause new entries, preserve the existing data volume and private files, update the checkout, and rebuild with the same compose overrides and deployment paths. Do not run `docker compose down -v` or remove databases. Live state and journals keep their prior names and location. Old simulation/testnet database files are left untouched for historical records; the app no longer opens them or starts their workers. The removed mode URLs cannot activate trading.

Refresh the PWA/browser after the build. Confirm account connection, recovered positions and exchange stops before manually enabling new entries. Activity now emits a separate fill/stop-verification event after the exchange readback, once per trade. Earlier “awaiting” events remain as historical submission records.

## Backups, upgrades and recovery

Use one app process and one data volume per instance. The volume holds live state, candle checkpoints and execution journals, plus any untouched historical files from older releases. Never discard/recreate that volume during upgrades.

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

## Explicit unverified live option

The existing `compose.live-unverified.yaml` sets `THOTH_ALLOW_UNVERIFIED_LIVE=1`. It permits activation without an indicator reference comparison, while retaining account checks and real-funds confirmation. It neither fabricates verification nor starts entries:

```bash
docker compose -f compose.yaml -f compose.live.yaml -f compose.live-unverified.yaml up --build -d --wait
```

Removing this override restores the indicator-reference requirement for new activation. Known exit recovery remains available. The live-only dashboard still reports the strategy's actual comparison status.
