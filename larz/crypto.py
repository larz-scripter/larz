"""
larz.crypto — authenticated encryption for secrets at rest, zero-dependency.

Used to store third-party API keys (e.g. customers' BYOK keys) encrypted in your
database. The construction is standard and built only on the standard library:

    * keys derived from your master secret with HMAC-SHA256 (separate enc/mac keys)
    * a random 16-byte nonce per message
    * HMAC-SHA256 in counter (CTR) mode as the keystream  (a keyed PRF stream cipher)
    * encrypt-then-MAC with HMAC-SHA256 for authentication

    from larz.crypto import Cipher
    c = Cipher(app_secret)
    token = c.encrypt("sk-secret-key")     # store this
    key   = c.decrypt(token)               # None if tampered or wrong secret

This is a sound, well-understood composition of vetted primitives — not a novel
cipher. For regulated/high-value secrets you may still prefer a KMS or the
`cryptography` package; `Cipher` is the zero-dependency default.
"""

import os
import hmac
import base64
import hashlib

__all__ = ["Cipher", "encrypt", "decrypt"]

_BLOCK = 32   # HMAC-SHA256 output size


class Cipher:
    def __init__(self, secret):
        secret = secret.encode() if isinstance(secret, str) else secret
        self.enc_key = hmac.new(secret, b"larz-crypto-enc", hashlib.sha256).digest()
        self.mac_key = hmac.new(secret, b"larz-crypto-mac", hashlib.sha256).digest()

    def _keystream(self, nonce, n):
        out = bytearray()
        counter = 0
        while len(out) < n:
            block = hmac.new(self.enc_key, nonce + counter.to_bytes(8, "big"),
                             hashlib.sha256).digest()
            out.extend(block)
            counter += 1
        return bytes(out[:n])

    def encrypt(self, plaintext):
        pt = plaintext.encode() if isinstance(plaintext, str) else plaintext
        nonce = os.urandom(16)
        ks = self._keystream(nonce, len(pt))
        ct = bytes(a ^ b for a, b in zip(pt, ks))
        tag = hmac.new(self.mac_key, nonce + ct, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")

    def decrypt(self, token):
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
        except Exception:
            return None
        if len(raw) < 16 + _BLOCK:
            return None
        nonce, ct, tag = raw[:16], raw[16:-_BLOCK], raw[-_BLOCK:]
        expected = hmac.new(self.mac_key, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            return None                              # tampered or wrong secret
        ks = self._keystream(nonce, len(ct))
        pt = bytes(a ^ b for a, b in zip(ct, ks))
        try:
            return pt.decode("utf-8")
        except UnicodeDecodeError:
            return pt


def encrypt(secret, plaintext):
    return Cipher(secret).encrypt(plaintext)


def decrypt(secret, token):
    return Cipher(secret).decrypt(token)
