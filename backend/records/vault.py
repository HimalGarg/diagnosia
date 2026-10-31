"""Encrypts stored clinical files, and fails closed if it can't.

Each file gets its own AES-256-GCM key, which is itself wrapped by a master key
from a key ring. The ring lives in a file outside the repository, the storage
folder and the database, so stolen storage alone reveals nothing. Rotating a
master key only rewraps those per-file keys.

The authenticated data ties each file to its owner and name, so a file moved to
another patient's record won't decrypt.

Key ring format: {"active": "k1", "keys": {"k1": "<base64 of 32 bytes>"}}
"""
import base64
import json
import os
import struct
from pathlib import Path
from typing import Dict, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.config import settings
from backend.private_files import is_private
from backend.safety import key_file_is_safe

MAGIC = b"DGV1"
KEY_BYTES = 32
NONCE_BYTES = 12


class VaultUnavailable(Exception):
    """No usable key ring: refuse to store or read anything sensitive."""


class IntegrityFailure(Exception):
    """A stored file was changed, damaged or moved."""


def new_key() -> str:
    return base64.b64encode(os.urandom(KEY_BYTES)).decode()


def _load_ring() -> Tuple[str, Dict[str, bytes]]:
    if not settings.document_key_file:
        raise VaultUnavailable("DIAGNOSIA_DOCUMENT_KEY_FILE is not set")
    path = Path(settings.document_key_file)
    if not key_file_is_safe(path):
        raise VaultUnavailable("The key file must be outside the repository and away from stored documents")
    try:
        private = is_private(path)
        ring = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VaultUnavailable(f"The key file can't be read: {type(error).__name__}") from None
    if not private:
        raise VaultUnavailable(
            "The key file must be readable by its owner only "
            "(chmod 600, or on Windows remove other users in its Security settings)"
        )
    try:
        keys = {key_id: base64.b64decode(value, validate=True) for key_id, value in ring["keys"].items()}
        active = ring["active"]
    except (KeyError, TypeError, AttributeError, ValueError):
        raise VaultUnavailable("The key file has the wrong format") from None
    if active not in keys or any(len(value) != KEY_BYTES for value in keys.values()):
        raise VaultUnavailable("The key file needs an active 32-byte key")
    return active, keys


def active_key_id() -> str:
    return _load_ring()[0]


def status() -> Tuple[bool, str]:
    """Proves the keys work rather than just that a setting exists."""
    try:
        sample = encrypt(b"readiness check", "readiness")
        if decrypt(sample, "readiness") != b"readiness check":
            return False, "Key ring failed a round trip"
    except (VaultUnavailable, IntegrityFailure) as error:
        return False, str(error)
    return True, f"Key ring usable, active key {active_key_id()}"


def _wrap_context(context: str, key_id: str) -> bytes:
    return f"{key_id}|{context}".encode()


def encrypt(plaintext: bytes, context: str) -> bytes:
    key_id, keys = _load_ring()
    file_key = os.urandom(KEY_BYTES)
    nonce, wrap_nonce = os.urandom(NONCE_BYTES), os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(file_key).encrypt(nonce, plaintext, context.encode())
    wrapped = AESGCM(keys[key_id]).encrypt(wrap_nonce, file_key, _wrap_context(context, key_id))
    return _pack(key_id, wrap_nonce, wrapped, nonce, ciphertext)


def _pack(key_id: str, wrap_nonce: bytes, wrapped: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    header = json.dumps(
        {
            "alg": "AES-256-GCM",
            "kid": key_id,
            "wrap_nonce": base64.b64encode(wrap_nonce).decode(),
            "wrapped_key": base64.b64encode(wrapped).decode(),
            "nonce": base64.b64encode(nonce).decode(),
        }
    ).encode()
    return MAGIC + struct.pack(">I", len(header)) + header + ciphertext


def _unpack(blob: bytes) -> Tuple[dict, bytes]:
    if not blob.startswith(MAGIC) or len(blob) < 8:
        raise IntegrityFailure("Not an encrypted Diagnosia file")
    length = struct.unpack(">I", blob[4:8])[0]
    try:
        header = json.loads(blob[8:8 + length])
        for field in ("wrap_nonce", "wrapped_key", "nonce"):
            header[field] = base64.b64decode(header[field])
        header["kid"]
    except (ValueError, KeyError, TypeError):
        raise IntegrityFailure("Damaged file header") from None
    return header, blob[8 + length:]


def _file_key(header: dict, keys: Dict[str, bytes], context: str) -> bytes:
    master = keys.get(header["kid"])
    if master is None:
        raise VaultUnavailable(f"Key {header['kid']} isn't in the key ring")
    try:
        return AESGCM(master).decrypt(header["wrap_nonce"], header["wrapped_key"], _wrap_context(context, header["kid"]))
    except (InvalidTag, ValueError):
        raise IntegrityFailure("File key failed authentication") from None


def decrypt(blob: bytes, context: str) -> bytes:
    _, keys = _load_ring()
    header, ciphertext = _unpack(blob)
    file_key = _file_key(header, keys, context)
    try:
        return AESGCM(file_key).decrypt(header["nonce"], ciphertext, context.encode())
    except (InvalidTag, ValueError):
        raise IntegrityFailure("File failed authentication") from None


def key_id_of(blob: bytes) -> str:
    return _unpack(blob)[0]["kid"]


def rewrap(blob: bytes, context: str) -> bytes:
    """Moves a file to the active master key without touching its encrypted content."""
    key_id, keys = _load_ring()
    header, ciphertext = _unpack(blob)
    file_key = _file_key(header, keys, context)
    wrap_nonce = os.urandom(NONCE_BYTES)
    wrapped = AESGCM(keys[key_id]).encrypt(wrap_nonce, file_key, _wrap_context(context, key_id))
    return _pack(key_id, wrap_nonce, wrapped, header["nonce"], ciphertext)
