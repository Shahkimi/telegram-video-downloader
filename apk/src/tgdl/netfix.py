"""Environment fixes that matter on Android: CA certificates and a writable HOME."""
from __future__ import annotations

import os
from pathlib import Path

from .paths import is_android


def apply_network_fixes(data_dir: Path | None = None) -> dict[str, str]:
    """Call once at start-up. Returns what it changed (shown on the Diagnostics screen)."""
    changed: dict[str, str] = {}

    try:
        import certifi

        cafile = certifi.where()
        if is_android() or not _system_ca_available():
            if "SSL_CERT_FILE" not in os.environ:
                os.environ["SSL_CERT_FILE"] = cafile
                changed["SSL_CERT_FILE"] = cafile
            if "REQUESTS_CA_BUNDLE" not in os.environ:
                os.environ["REQUESTS_CA_BUNDLE"] = cafile
    except ImportError:
        pass

    if data_dir is not None:
        home = os.path.expanduser("~")
        if not home or home == "~" or not os.access(home, os.W_OK):
            os.environ["HOME"] = str(data_dir)
            changed["HOME"] = str(data_dir)
    return changed


def _system_ca_available() -> bool:
    import ssl

    paths = ssl.get_default_verify_paths()
    return bool((paths.cafile and os.path.exists(paths.cafile)) or (paths.capath and os.path.isdir(paths.capath)))
