import os

import pytest

from tgdl import crypto_backend as cb


def test_reference_roundtrip():
    data, key, iv = os.urandom(64), os.urandom(32), os.urandom(32)
    enc = cb.reference_ige(data, key, iv, True)
    assert enc != data
    assert cb.reference_ige(enc, key, iv, False) == data


def test_reference_matches_telethon_python_fallback(monkeypatch):
    """Our oracle must equal Telethon own pure-Python path, otherwise the self test proves nothing."""
    from telethon.crypto import aes, libssl

    monkeypatch.setattr(aes, "cryptg", None)
    monkeypatch.setattr(libssl, "encrypt_ige", None)
    monkeypatch.setattr(libssl, "decrypt_ige", None)
    data, key, iv = os.urandom(48), os.urandom(32), os.urandom(32)
    assert aes.AES.encrypt_ige(data, key, iv) == cb.reference_ige(data, key, iv, True)
    enc = cb.reference_ige(data, key, iv, True)
    assert aes.AES.decrypt_ige(enc, key, iv) == data


def test_openssl_ctypes_backend_matches_reference():
    loaded = cb.load_openssl()
    if loaded is None:
        pytest.skip("no OpenSSL libcrypto with AES_ige_encrypt on this machine")
    enc, dec = cb.make_openssl_ige(loaded[0])
    assert cb._self_test(enc, dec)
    data, key, iv = os.urandom(1024), os.urandom(32), os.urandom(32)
    assert enc(data, key, iv) == cb.reference_ige(data, key, iv, True)
    assert dec(enc(data, key, iv), key, iv) == data
    for key_len in (16, 24, 32):
        k = os.urandom(key_len)
        assert dec(enc(data, k, iv), k, iv) == data


def test_cryptography_fallback_matches_reference():
    pair = cb.make_cryptography_ige()
    if pair is None:
        pytest.skip("cryptography not installed")
    assert cb._self_test(*pair)


def test_install_picks_a_backend_and_telethon_still_roundtrips():
    from telethon.crypto import AES

    info = cb.install_fast_crypto(force=True)
    assert info.backend in {"cryptg", "telethon-libssl", "openssl-ctypes", "cryptography", "pyaes"}
    data, key, iv = os.urandom(160), os.urandom(32), os.urandom(32)
    assert AES.decrypt_ige(AES.encrypt_ige(data, key, iv), key, iv) == data
    assert cb.benchmark(0.25) > 0
    assert info.describe()


def test_forced_openssl_path_without_cryptg(monkeypatch):
    """Simulates Android: no cryptg, no system libssl known to Telethon."""
    from telethon.crypto import aes, libssl

    if cb.load_openssl() is None:
        pytest.skip("no OpenSSL available")
    monkeypatch.setattr(aes, "cryptg", None)
    monkeypatch.setattr(libssl, "encrypt_ige", None)
    monkeypatch.setattr(libssl, "decrypt_ige", None)
    monkeypatch.setattr(cb, "_info", None)
    info = cb.install_fast_crypto(force=True)
    assert info.backend == "openssl-ctypes", info
    data, key, iv = os.urandom(4096), os.urandom(32), os.urandom(32)
    assert aes.AES.decrypt_ige(aes.AES.encrypt_ige(data, key, iv), key, iv) == data
    assert cb.benchmark(2) > 5  # MB/s: far above the pure-Python path
