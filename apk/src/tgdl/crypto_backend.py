"""
Pick the fastest AES-IGE backend for Telethon.

Every Telegram download is decrypted with AES-IGE. Telethon uses, in order:
  cryptg (C extension)  ->  system libssl through ctypes  ->  pure Python pyaes.
Pure Python is roughly 100x slower, which makes downloads crawl. On Android there is no cryptg
wheel and Telethon usually cannot find a system libssl, so this module loads the OpenSSL that
ships with the app's own Python and plugs it into Telethon.

Order tried here:
  1. cryptg
  2. libssl found by Telethon itself
  3. OpenSSL loaded through ctypes (AES_ige_encrypt)
  4. the cryptography package (slow, per block)
  5. pyaes (very slow, shown as a warning in the UI)
"""
from __future__ import annotations

import ctypes
import ctypes.util
import glob
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("tgdl.crypto")

IgeFn = Callable[[bytes, bytes, bytes], bytes]


@dataclass
class CryptoInfo:
    backend: str                    # cryptg | telethon-libssl | openssl-ctypes | cryptography | pyaes
    detail: str = ""
    mb_per_s: float | None = None

    @property
    def fast(self) -> bool:
        return self.backend in ("cryptg", "telethon-libssl", "openssl-ctypes")

    def describe(self) -> str:
        text = self.backend + (f" ({self.detail})" if self.detail else "")
        if self.mb_per_s is not None:
            text += f", {self.mb_per_s:.0f} MB/s"
        return text


_info: CryptoInfo | None = None


# ---------------------------------------------------------------------
# Reference implementation (used to verify the fast ones)
# ---------------------------------------------------------------------

def reference_ige(data: bytes, key: bytes, iv: bytes, encrypt: bool) -> bytes:
    """Slow but obviously correct IGE built on pyaes, same maths as Telethon fallback."""
    import pyaes

    aes = pyaes.AES(key)
    iv1, iv2 = list(iv[:16]), list(iv[16:32])
    out: list[int] = []
    for off in range(0, len(data), 16):
        block = list(data[off:off + 16])
        if encrypt:
            enc = aes.encrypt([b ^ x for b, x in zip(block, iv1)])
            enc = [b ^ x for b, x in zip(enc, iv2)]
            iv1, iv2 = enc, block
            out.extend(enc)
        else:
            dec = aes.decrypt([b ^ x for b, x in zip(block, iv2)])
            dec = [b ^ x for b, x in zip(dec, iv1)]
            iv1, iv2 = block, dec
            out.extend(dec)
    return bytes(out)


def _self_test(enc: IgeFn, dec: IgeFn) -> bool:
    data = bytes((i * 7 + 3) % 256 for i in range(96))
    key = bytes((i * 5 + 1) % 256 for i in range(32))
    iv = bytes((i * 11 + 2) % 256 for i in range(32))
    try:
        want_enc = reference_ige(data, key, iv, True)
        return enc(data, key, iv) == want_enc and dec(want_enc, key, iv) == data
    except Exception:  # noqa: BLE001 - any failure means the backend is unusable
        log.exception("crypto self test crashed")
        return False


# ---------------------------------------------------------------------
# OpenSSL through ctypes
# ---------------------------------------------------------------------

def _candidate_libs() -> list[str]:
    try:
        import ssl  # noqa: F401  - makes sure the interpreter's own OpenSSL is mapped
    except ImportError:
        pass
    found: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in found:
            found.append(name)

    try:  # Linux / Android: libraries already mapped into this process
        with open("/proc/self/maps", "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if "libcrypto" in line:
                    path = line.split()[-1]
                    if path.startswith("/"):
                        add(path)
    except OSError:
        pass

    for name in ("libcrypto_python.so", "libcrypto.so.3", "libcrypto.so", "libcrypto-3.dll",
                 "libcrypto-3-x64.dll", "libcrypto.dylib"):
        add(name)
    for pattern in (os.path.join(sys.base_prefix, "DLLs", "libcrypto*.dll"),
                    os.path.join(os.path.dirname(sys.executable), "libcrypto*.dll")):
        for path in glob.glob(pattern):
            add(path)
    add(ctypes.util.find_library("crypto"))
    return found


def load_openssl() -> tuple[ctypes.CDLL, str] | None:
    for cand in _candidate_libs():
        try:
            lib = ctypes.CDLL(cand)
            for symbol in ("AES_set_encrypt_key", "AES_set_decrypt_key", "AES_ige_encrypt"):
                getattr(lib, symbol)
        except (OSError, AttributeError):
            continue
        return lib, cand
    return None


def make_openssl_ige(lib: ctypes.CDLL) -> tuple[IgeFn, IgeFn]:
    set_enc, set_dec, ige = lib.AES_set_encrypt_key, lib.AES_set_decrypt_key, lib.AES_ige_encrypt
    for fn in (set_enc, set_dec):
        fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        fn.restype = ctypes.c_int
    ige.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    ige.restype = None

    def run(data: bytes, key: bytes, iv: bytes, encrypt: bool) -> bytes:
        data, key = bytes(data), bytes(key)
        schedule = ctypes.create_string_buffer(512)  # AES_KEY is 244 bytes
        if (set_enc if encrypt else set_dec)(key, len(key) * 8, schedule) != 0:
            raise ValueError("OpenSSL rejected the AES key")
        out = ctypes.create_string_buffer(len(data))
        iv_buf = ctypes.create_string_buffer(bytes(iv), len(iv))  # OpenSSL updates the iv in place
        ige(data, out, len(data), schedule, iv_buf, 1 if encrypt else 0)
        return out.raw

    return (lambda d, k, i: run(d, k, i, True)), (lambda d, k, i: run(d, k, i, False))


# ---------------------------------------------------------------------
# cryptography package fallback
# ---------------------------------------------------------------------

def make_cryptography_ige() -> tuple[IgeFn, IgeFn] | None:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return None

    def run(data: bytes, key: bytes, iv: bytes, encrypt: bool) -> bytes:
        cipher = Cipher(algorithms.AES(bytes(key)), modes.ECB())
        update = (cipher.encryptor() if encrypt else cipher.decryptor()).update
        iv1 = int.from_bytes(iv[:16], "big")
        iv2 = int.from_bytes(iv[16:32], "big")
        out = bytearray(len(data))
        for off in range(0, len(data), 16):
            block = int.from_bytes(data[off:off + 16], "big")
            if encrypt:
                res = int.from_bytes(update((block ^ iv1).to_bytes(16, "big")), "big") ^ iv2
                iv1, iv2 = res, block
            else:
                res = int.from_bytes(update((block ^ iv2).to_bytes(16, "big")), "big") ^ iv1
                iv1, iv2 = block, res
            out[off:off + 16] = res.to_bytes(16, "big")
        return bytes(out)

    return (lambda d, k, i: run(d, k, i, True)), (lambda d, k, i: run(d, k, i, False))


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def install_fast_crypto(force: bool = False) -> CryptoInfo:
    """Choose and install the best backend. Safe to call more than once."""
    global _info
    if _info is not None and not force:
        return _info

    from telethon.crypto import aes as tl_aes
    from telethon.crypto import libssl as tl_libssl

    if getattr(tl_aes, "cryptg", None):
        _info = CryptoInfo("cryptg", "C extension")
        return _info
    if tl_libssl.encrypt_ige and tl_libssl.decrypt_ige and _info is None:
        _info = CryptoInfo("telethon-libssl", "system libssl")
        return _info

    loaded = load_openssl()
    if loaded:
        lib, where = loaded
        try:
            enc, dec = make_openssl_ige(lib)
        except AttributeError:
            enc = dec = None
        if enc and dec and _self_test(enc, dec):
            tl_libssl.encrypt_ige, tl_libssl.decrypt_ige = enc, dec
            _info = CryptoInfo("openssl-ctypes", where)
            return _info
        log.warning("OpenSSL at %s failed the AES-IGE self test", where)

    pair = make_cryptography_ige()
    if pair and _self_test(*pair):
        tl_libssl.encrypt_ige, tl_libssl.decrypt_ige = pair
        _info = CryptoInfo("cryptography", "per-block, slow")
        return _info

    tl_libssl.encrypt_ige = tl_libssl.decrypt_ige = None
    _info = CryptoInfo("pyaes", "pure Python, very slow")
    return _info


def current_info() -> CryptoInfo:
    return _info or install_fast_crypto()


def benchmark(size_mb: float = 4.0) -> float:
    """Decrypt size_mb of data with whatever Telethon is using now. Returns MB/s."""
    from telethon.crypto import AES

    info = current_info()
    if info.backend == "pyaes":
        size_mb = min(size_mb, 0.05)  # keep the slow path from freezing the UI
    elif info.backend == "cryptography":
        size_mb = min(size_mb, 0.5)
    data = os.urandom(int(size_mb * 1024 * 1024) // 16 * 16)
    key, iv = os.urandom(32), os.urandom(32)
    start = time.perf_counter()
    AES.decrypt_ige(data, key, iv)
    elapsed = max(time.perf_counter() - start, 1e-9)
    info.mb_per_s = size_mb / elapsed
    return info.mb_per_s
