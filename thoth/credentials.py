"""Read runtime credentials without exposing them to API responses or logs."""
import os
import stat
from pathlib import Path


class CredentialError(RuntimeError):
    pass


def runtime_secret(name):
    """Prefer a private mounted file; support environment values for local use.

    Never include values or file contents in errors. Refuse ambiguous sources,
    group/world-accessible files, non-regular files, and empty configured files.
    """
    path = os.environ.get(name+'_FILE')
    value = os.environ.get(name)
    if path and value:
        raise CredentialError(f'Configure only one source for {name}')
    if path:
        try:
            with Path(path).open('r') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise CredentialError(f'Unsafe credential file permissions for {name}')
                value = stream.read(8193).strip()
        except (OSError, UnicodeError, ValueError):
            raise CredentialError(f'Credential file unavailable for {name}') from None
        if not value or len(value)>8192:
            raise CredentialError(f'Invalid credential file for {name}')
    return value or ''
