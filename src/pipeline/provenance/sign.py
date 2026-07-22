"""Detached GPG signing/verification, plus the sign flow that records a leaf.

The GPG functions are thin ``subprocess`` shells around the user's ``gpg`` (and
therefore their YubiKey / gpg-agent) — deliberately un-unit-tested. Everything
above them takes an injected :data:`Signer` / :data:`Verifier`, so the flow logic
(:func:`sign_entry`, and verification in :mod:`verify`) is fully testable with a
fake and never shells out.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import log as plog
from .canonical import CanonicalEntry
from .proof import EntryProof

# data -> ASCII-armored detached signature.
Signer = Callable[[bytes], str]
# (data, armored signature) -> is the signature valid for the data?
Verifier = Callable[[bytes, str], bool]

ENTRIES_SUBDIR = "entries"


class GpgError(RuntimeError):
    """Raised when a ``gpg`` invocation fails."""


def _env(home: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if home:
        env["GNUPGHOME"] = home
    return env


def detach_sign(data: bytes, *, key: str, gpg: str = "gpg", home: str | None = None) -> str:
    """ASCII-armored detached signature over ``data`` using ``key`` (a
    fingerprint or uid). Uses the caller's gpg-agent, so a YubiKey PIN prompt is
    expected — this is why signing is a deliberate local act, never CI."""
    res = subprocess.run(
        [gpg, "--batch", "--yes", "--armor", "--local-user", key, "--detach-sign"],
        input=data,
        capture_output=True,
        env=_env(home),
    )
    if res.returncode != 0:
        raise GpgError(f"gpg detach-sign failed: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout.decode()


def verify(data: bytes, signature: str, *, gpg: str = "gpg", home: str | None = None) -> bool:
    """Whether ``signature`` is a valid detached signature over ``data`` against
    the keys in ``home`` (or the default keyring)."""
    with tempfile.NamedTemporaryFile("w", suffix=".asc") as sig_file:
        sig_file.write(signature)
        sig_file.flush()
        res = subprocess.run(
            [gpg, "--batch", "--verify", sig_file.name, "-"],
            input=data,
            capture_output=True,
            env=_env(home),
        )
    return res.returncode == 0


def fingerprint(key: str, *, gpg: str = "gpg", home: str | None = None) -> str:
    """The full fingerprint of ``key`` (uppercase hex, no spaces)."""
    res = subprocess.run(
        [gpg, "--batch", "--with-colons", "--fingerprint", key],
        capture_output=True,
        env=_env(home),
    )
    if res.returncode != 0:
        raise GpgError(f"gpg fingerprint failed: {res.stderr.decode(errors='replace').strip()}")
    for line in res.stdout.decode().splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    raise GpgError(f"no fingerprint found for key {key!r}")


def gpg_signer(key: str, *, gpg: str = "gpg") -> Signer:
    """A :data:`Signer` bound to a local GPG key."""
    return lambda data: detach_sign(data, key=key, gpg=gpg)


def pubkey_verifier(pubkey: str, *, gpg: str = "gpg") -> Verifier:
    """A :data:`Verifier` that checks signatures against ``pubkey`` (armored) in
    an ephemeral keyring — self-contained, independent of the caller's keyring,
    so anyone can verify with only the committed public key."""

    def _verify(data: bytes, signature: str) -> bool:
        with tempfile.TemporaryDirectory() as home:
            os.chmod(home, 0o700)
            imported = subprocess.run(
                [gpg, "--homedir", home, "--batch", "--import"],
                input=pubkey.encode(),
                capture_output=True,
            )
            if imported.returncode != 0:
                raise GpgError(f"gpg import failed: {imported.stderr.decode(errors='replace')}")
            return verify(data, signature, gpg=gpg, home=home)

    return _verify


def sign_entry(
    prov_dir: Path | str,
    entry: CanonicalEntry,
    *,
    signer: Signer,
    fingerprint: str,
    when: str | None = None,
) -> EntryProof:
    """Sign ``entry``'s canonical bytes, write the authoritative sidecar under
    ``provenance/entries/<slug>.sig``, record/refresh its leaf, and return the
    neutral :class:`EntryProof` for the adapter to render onto the site."""
    signature = signer(entry.to_bytes())
    sig_name = f"{entry.slug}.sig"
    entries_dir = Path(prov_dir) / ENTRIES_SUBDIR
    entries_dir.mkdir(parents=True, exist_ok=True)
    (entries_dir / sig_name).write_text(signature)

    plog.record_entry(prov_dir, entry, sig=f"{ENTRIES_SUBDIR}/{sig_name}", when=when)

    return EntryProof(
        slug=entry.slug,
        leaf_sha256=entry.leaf_hex(),
        signature=signature,
        sig_filename=sig_name,
        pubkey_fingerprint=fingerprint,
    )
