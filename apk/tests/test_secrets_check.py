"""The secrets check is the last guard before a private session or a 1.5 GB video ships in an APK."""
from __future__ import annotations

import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_no_secrets.py"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("check_no_secrets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_tree(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return tmp_path


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_apk(tmp_path: Path, app_files: dict[str, bytes], extra: dict[str, bytes] | None = None) -> Path:
    apk = tmp_path / "x.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/app.zip", zip_bytes(app_files))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return apk


def test_clean_tree_passes(check, tmp_path):
    root = make_tree(tmp_path, {"src/main.py": b"print('hi')\n", "src/tgdl/a.py": b"x = 1\n"})
    assert check.check_tree(root) == []


@pytest.mark.parametrize("name", ["src/.env", "src/session.session", "src/config.json", "src/link_rules.json",
                                  "src/clip.mp4", "extensions/upload.jks", "scripts/key.properties"])
def test_private_files_are_flagged(check, tmp_path, name):
    root = make_tree(tmp_path, {name: b"x"})
    assert check.check_tree(root), name


def test_example_env_is_allowed(check, tmp_path):
    assert check.check_tree(make_tree(tmp_path, {"src/.env.example": b"API_ID=\n"})) == []


def test_api_hash_in_code_is_flagged(check, tmp_path):
    root = make_tree(tmp_path, {"src/a.py": b'API_HASH = "0123456789abcdef0123456789abcdef"\n'})
    assert any("API hash" in p for p in check.check_tree(root))


def test_big_file_is_flagged(check, tmp_path):
    root = make_tree(tmp_path, {"src/blob.bin": b"0" * 5_000_001})
    assert any("too big" in p for p in check.check_tree(root))


def test_skipped_folders_are_ignored(check, tmp_path):
    root = make_tree(tmp_path, {"src/__pycache__/x.session": b"x", "build/.env": b"x"})
    assert check.check_tree(root) == []


def test_clean_apk_passes(check, tmp_path):
    apk = make_apk(tmp_path, {"main.py": b"x=1", "tgdl/__init__.py": b""}, {"lib/arm64-v8a/libpython3.13.so": b"\x7fELF"})
    assert check.check_apk(apk) == []


def test_apk_with_session_inside_app_is_flagged(check, tmp_path):
    apk = make_apk(tmp_path, {"main.py": b"x=1", "tgdl.session": b"sqlite"})
    assert any(".session" in p for p in check.check_apk(apk))


def test_apk_with_video_asset_is_flagged(check, tmp_path):
    apk = make_apk(tmp_path, {"main.py": b"x=1"}, {"assets/movie.mp4": b"x"})
    assert any("movie.mp4" in p for p in check.check_apk(apk))


def test_apk_with_credentials_in_code_is_flagged(check, tmp_path):
    apk = make_apk(tmp_path, {"main.py": b"api_hash = '0123456789abcdef0123456789abcdef'"})
    assert any("API hash" in p for p in check.check_apk(apk))


def test_apk_without_app_code_is_flagged(check, tmp_path):
    apk = make_apk(tmp_path, {"readme.txt": b"nothing"})
    assert any("no main.py" in p for p in check.check_apk(apk))


def test_missing_apk(check, tmp_path):
    assert check.check_apk(tmp_path / "nope.apk")
