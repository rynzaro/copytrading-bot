"""Bot keys (one ed25519 key per owner) and NEAR transaction signing (hand Borsh).

Private keys live only in $DATA_DIR/keys/<owner>.json (0600). Never log them, never return them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import threading
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
# NEAR account id rules (also blocks path traversal in key file names).
ACCOUNT_RE = re.compile(r"^(?=.{2,64}$)(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$")


def b58e(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def b58d(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + b


def valid_account(a: str) -> bool:
    return isinstance(a, str) and bool(ACCOUNT_RE.match(a))


# ---- Borsh ----

def _str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("<I", len(b)) + b


def function_call(method: str, args: dict | bytes, gas: int, deposit: int = 0) -> bytes:
    """Borsh Action::FunctionCall (enum index 2)."""
    raw = args if isinstance(args, bytes) else json.dumps(args, separators=(",", ":")).encode()
    return b"\x02" + _str(method) + struct.pack("<I", len(raw)) + raw + struct.pack("<Q", gas) + deposit.to_bytes(16, "little")


def transaction(signer_id: str, public_key: bytes, nonce: int, receiver_id: str, block_hash: bytes,
                actions: list[bytes]) -> bytes:
    assert len(public_key) == 32 and len(block_hash) == 32
    return (_str(signer_id) + b"\x00" + public_key + struct.pack("<Q", nonce) + _str(receiver_id) + block_hash
            + struct.pack("<I", len(actions)) + b"".join(actions))


# ---- Keys ----

class BotKey:
    """Holds the private key in memory. repr/str never show it."""

    def __init__(self, seed: bytes):
        self._sk = Ed25519PrivateKey.from_private_bytes(seed)
        self.public_bytes = self._sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def public_key(self) -> str:
        return "ed25519:" + b58e(self.public_bytes)

    def sign(self, msg: bytes) -> bytes:
        return self._sk.sign(msg)

    def __repr__(self) -> str:
        return f"BotKey({self.public_key})"

    __str__ = __repr__


@dataclass(frozen=True)
class SignedTx:
    hash: str          # base58 sha256 of tx bytes
    base64: str        # signed tx, for send_tx
    nonce: int
    receiver_id: str


def sign_tx(key: BotKey, signer_id: str, nonce: int, receiver_id: str, block_hash: str, actions: list[bytes]) -> SignedTx:
    raw = transaction(signer_id, key.public_bytes, nonce, receiver_id, b58d(block_hash), actions)
    h = hashlib.sha256(raw).digest()
    signed = raw + b"\x00" + key.sign(h)
    return SignedTx(b58e(h), base64.b64encode(signed).decode(), nonce, receiver_id)


class KeyStore:
    """$DATA_DIR/keys/<owner>.json, mode 0600, dir 0700. Nonces: last used per key, next = max(chain, last) + 1."""

    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "keys"
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self._lock = threading.Lock()
        self._nonce: dict[str, int] = {}

    def _path(self, owner_id: str) -> Path:
        if not valid_account(owner_id):
            raise ValueError("invalid owner_id")
        return self.dir / f"{owner_id}.json"

    def ensure(self, owner_id: str) -> str:
        """Create the key once; return its public key. Idempotent."""
        p = self._path(owner_id)
        with self._lock:
            if not p.exists():
                seed = os.urandom(32)
                key = BotKey(seed)
                body = json.dumps({"public_key": key.public_key,
                                   "private_key": "ed25519:" + b58e(seed + key.public_bytes)}).encode()
                tmp = p.with_suffix(".tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, body)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(tmp, p)
            return self.load(owner_id).public_key

    def load(self, owner_id: str) -> BotKey:
        d = json.loads(self._path(owner_id).read_text())
        sk = b58d(d["private_key"].split(":", 1)[1])
        key = BotKey(sk[:32])
        if key.public_key != d["public_key"]:
            raise ValueError("key file mismatch")
        return key

    def exists(self, owner_id: str) -> bool:
        return self._path(owner_id).exists()

    def next_nonce(self, public_key: str, chain_nonce: int) -> int:
        """Reserve a nonce: last used + 1 (chain view or local, whichever is higher)."""
        with self._lock:
            n = max(chain_nonce, self._nonce.get(public_key, 0)) + 1
            self._nonce[public_key] = n
            return n
