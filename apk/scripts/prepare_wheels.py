"""Build wheels for dependencies that PyPI only ships as source archives.

`flet build` installs the app's Python packages with --only-binary, so a package without a wheel stops the build.
Telethon needs `pyaes`, which is source-only. This script builds that wheel once into a local folder; point
PIP_FIND_LINKS at it (build_apk.ps1 and the GitHub workflow do) and the build can install it.

    python scripts/prepare_wheels.py            # builds into apk/.wheels and prints the folder
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

APK_DIR = Path(__file__).resolve().parent.parent
SOURCE_ONLY = ["pyaes==1.6.1"]


def have_wheel(folder: Path, requirement: str) -> bool:
    name = requirement.split("==")[0].lower().replace("-", "_")
    return any(p.name.lower().startswith(name + "-") for p in folder.glob("*.whl"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=APK_DIR / ".wheels", help="folder for the wheels (default: apk/.wheels)")
    args = parser.parse_args()

    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for requirement in SOURCE_ONLY:
        if have_wheel(out, requirement):
            continue
        print(f"building wheel for {requirement} ...", file=sys.stderr)
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-cache-dir", "-w", str(out), requirement],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(proc.stdout[-2000:], proc.stderr[-2000:], file=sys.stderr)
            print(f"could not build a wheel for {requirement}", file=sys.stderr)
            return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
