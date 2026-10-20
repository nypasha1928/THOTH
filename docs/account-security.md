# Dashboard password, recovery, authenticator and passkeys

Open **Security → Account security** after deploying. Existing dashboard access
continues to use the server password until you change it. Nothing here starts,
pauses, closes, or resets trading. Deployment retains the existing restart rules.

Recommended enrollment order:

1. Enter your current password and generate recovery codes. Download them and
   store them privately somewhere you can access if the phone is lost. Each
   works once. Generating another set replaces the old set.
2. Change the password to a memorable private passphrase (16–256 characters).
   Enter it twice. Existing login sessions are invalidated; this browser gets
   a replacement session. The old password cannot subsequently log in.
3. Set up Google Authenticator: enter current credentials, scan the QR code
   or use the manual setup key, then submit a fresh six-digit code. Protection
   does not activate until that confirmation succeeds. Setup expires after ten
   minutes. Codes can be used only once; wait for the next code before another
   security change or password login. An unused recovery code can substitute.
4. On Android Chrome or the installed PWA, add a passkey with a device name.
   Verify using the device's fingerprint, face, or screen lock. A passkey uses
   verified possession of that key and device user verification; it replaces
   password + authenticator sign-in. THOTH receives no biometric data. Physical
   device support depends on the browser and credential provider. Test sign-in
   from another session before relying on it. Password + authenticator remains
   available alongside passkeys.

Both the security page and login recovery page show sensitive recovery/setup
material only when needed; it is not stored in localStorage or offline caches.
Never paste recovery codes, setup keys, passwords or Bybit secrets into chat.

## Forgotten password

On the login page choose **Forgot your password?**, enter an unused saved
recovery code and a new password twice. This is a full account-access reset:
old sessions, authenticator enrollment, passkeys and old recovery codes are
invalidated. Download the replacement recovery codes and enroll extra factors
again. It does not access the exchange or modify trading state. There is no
email delivery or plaintext password retrieval.

If all recovery methods are lost, the SSH server owner can use the DigitalOcean
console/SSH and run this interactive command (do not place passwords in command
arguments or shell history):

```bash
docker exec -it --user 10001 thoth-app-1 python -m thoth.security_recovery
```

Enter and repeat the new password privately. This resets dashboard credentials
only, removes extra factors/recovery codes, and invalidates old sessions. Sign
in and generate fresh recovery codes. The command does not import the app or
start a second trading supervisor.

## Server storage and backups

`/app/data/security.sqlite` stores a salted scrypt password hash, hashes of
random 128-bit recovery codes, passkey public keys/counters, and an encrypted
TOTP secret. The separate `/app/data/security.key` is private (0600), persisted
in the existing Docker data volume, and ignored by Git. Sessions are bound to a
persistent security revision and a fresh process session key. Security changes
and recovery revoke old sessions; sessions also expire after eight hours.

Keep the existing `/etc/thoth/dashboard-password` mount for startup. Once a
password is changed, that file is only the bootstrap password; changing it does
not override the saved hash. Use the authenticated page or SSH recovery tool.
Never remove/reset the Docker data volume to fix login.

Private `tools/backup_data.py` backups include the security database and its
explicit encryption key, with checksums and private permissions. These backups
contain login secrets and must be protected accordingly. API key files are not
included. Restore the key and database together. Stop the app before taking an
application-consistent backup. Existing backup formats without security data
remain readable. Protect rollback copies too: restoring an older security
backup can restore older passwords and factors.

Passkey challenges are single use, expire, and bind to the relying-party host,
origin, and account revision. Registration also binds to the reauthenticated
session. User verification is required for registration and sign-in. Failed
password/security attempts are rate-limited; TOTP steps cannot be replayed.

## Development validation

Use a virtual environment and install `requirements-dev.txt`. Run:

```bash
python -m unittest discover -s tests -v
python tools/security_smoke.py
python tools/browser_smoke.py
```

Browser tools additionally need Playwright and Chromium at `/usr/bin/chromium`.
They use temporary databases, synthetic passwords/accounts, and no exchange
credentials. The security browser test performs real cryptographic WebAuthn
verification with Chrome's virtual authenticator. This is not proof of physical
Android enrollment, which must be checked after deployment.

History additionally has numbered page buttons and a page-number jump below the
table. Each page holds up to 50 trades, with the final partial page showing the
remaining trades. Filtering returns to page 1.
