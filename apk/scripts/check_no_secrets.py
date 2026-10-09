"""Make sure nothing private or huge ends up in the app.

The repository root holds a real .env, a Telegram session and gigabytes of downloaded videos. None of that may be
packaged, committed or released. Run this before and after every build:

    python scripts/check_no_secrets.py                      # source tree (src/, extensions/, scripts/)
    python scripts/check_no_secrets.py --apk build/apk/tg-downloader.apk

Exit status 0 means clean, 1 means something was found.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

APK_DIR = Path(__file__).resolve().parent.parent

FORBIDDEN_NAMES = {".env", "config.json", "link_rules.json", "key.properties", "local.properties.secret"}
FORBIDDEN_SUFFIXES = (
    ".session", ".session-journal", ".jks", ".keystore", ".p12", ".pfx", ".pem",
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".mp3", ".m4a", ".flac", ".wav", ".part", ".ytdl",
)
# Files that look like these names but are fine (example templates).
ALLOWED_NAMES = {".env.example", "link_rules.example.json"}

SECRET_PATTERNS = {
    "Telegram API hash": re.compile(rb"(?i)api[_-]?hash[\"']?\s*[:=]\s*[\"']?[0-9a-f]{32}\b"),
    "Telegram API id": re.compile(rb"(?i)\bapi[_-]?id[\"']?\s*[:=]\s*[\"']?\d{6,9}\b"),
    "Telegram string session": re.compile(rb"\b1[A-Za-z0-9_\-]{300,}={0,2}"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
}
TEXT_SUFFIXES = {".py", ".pyc", ".json", ".toml", ".txt", ".md", ".yml", ".yaml", ".ps1", ".kt", ".dart", ".xml", ".gradle", ".cfg", ".ini", ""}
MAX_SCAN_BYTES = 2_000_000
SKIP_DIRS = {".venv", "build", ".wheels", "__pycache__", ".pytest_cache", ".git", "dist", ".flet", "node_modules"}


def bad_name(name: str) -> str | None:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    low = base.lower()
    if low in ALLOWED_NAMES:
        return None
    if low in FORBIDDEN_NAMES or (low.startswith(".env") and low != ".env.example"):
        return f"private file name: {base}"
    if low.endswith(FORBIDDEN_SUFFIXES):
        return f"private or media file: {base}"
    return None


def scan_bytes(label: str, data: bytes, problems: list[str]) -> None:
    for what, pattern in SECRET_PATTERNS.items():
        if pattern.search(data[:MAX_SCAN_BYTES]):
            problems.append(f"{label}: looks like a {what}")


# ---- source tree ----------------------------------------------------------------------------------
def check_tree(root: Path) -> list[str]:
    problems: list[str] = []
    for folder in ("src", "extensions", "scripts", "tests"):
        base = root / folder
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts) or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            reason = bad_name(path.name)
            if reason and not rel.startswith("tests/"):
                problems.append(f"{rel}: {reason}")
                continue
            if path.stat().st_size > 5_000_000:
                problems.append(f"{rel}: file is {path.stat().st_size // 1_000_000} MB, too big for the app source")
                continue
            # tests deliberately contain fake credentials; their file names and sizes are still checked above
            if path.suffix.lower() in TEXT_SUFFIXES and not rel.startswith("tests/"):
                scan_bytes(rel, path.read_bytes(), problems)
    return problems


# ---- built APK ------------------------------------------------------------------------------------------
def check_apk(apk: Path) -> list[str]:
    problems: list[str] = []
    if not apk.exists():
        return [f"{apk}: not found"]
    with zipfile.ZipFile(apk) as outer:
        names = outer.namelist()
        for name in names:
            reason = bad_name(name)
            # Python packages may legitimately ship .ts/.pem files (certifi's cacert.pem); only the app itself is strict.
            if reason and not name.startswith(("assets/sitepackages", "assets/stdlib", "assets/extract", "lib/")):
                problems.append(f"{name}: {reason}")
        for inner_name in ("assets/app.zip", "assets/extract.zip", "assets/sitepackages.zip"):
            if inner_name not in names:
                if inner_name == "assets/app.zip":
                    problems.append("assets/app.zip is missing: this does not look like a Flet app")
                continue
            with outer.open(inner_name) as fh:
                inner = zipfile.ZipFile(io.BytesIO(fh.read()))
            strict = inner_name == "assets/app.zip"
            for info in inner.infolist():
                label = f"{inner_name}!{info.filename}"
                reason = bad_name(info.filename)
                if reason and (strict or info.filename.lower().endswith((".session", ".jks", ".keystore", ".env"))):
                    problems.append(f"{label}: {reason}")
                if strict and not info.is_dir() and info.file_size <= MAX_SCAN_BYTES:
                    scan_bytes(label, inner.read(info), problems)
            if strict:
                inside = {i.filename for i in inner.infolist()}
                for needed in ("main.py", "main.pyc"):
                    if needed in inside:
                        break
                else:
                    problems.append("assets/app.zip has no main.py: the app code did not get packaged")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apk", type=Path, help="check a built .apk instead of the source tree")
    parser.add_argument("--root", type=Path, default=APK_DIR, help="apk project folder (default: the one containing this script)")
    args = parser.parse_args()

    problems = check_apk(args.apk) if args.apk else check_tree(args.root.resolve())
    target = str(args.apk) if args.apk else f"{args.root.resolve()} (src, extensions, scripts, tests)"
    if problems:
        print(f"FOUND {len(problems)} problem(s) in {target}:")
        for line in problems[:50]:
            print("  -", line)
        return 1
    print(f"clean: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
