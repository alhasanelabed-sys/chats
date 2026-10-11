"""Make sure every package the program needs is installed (run by start.bat).

Standard library only. Installs requirements.txt the first time and again
whenever the file changes (an update added a package), and repairs the
environment if a package is missing. Nothing to install by hand.
"""
from __future__ import annotations

import hashlib
import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = Path(sys.prefix) / ".hader_requirements.sha256"
# import name of each package in requirements.txt
MODULES = ["fastapi", "uvicorn", "sqlalchemy", "multipart", "openpyxl", "segno", "httpx", "tzdata"]


def missing() -> list[str]:
    out = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - any import problem means reinstall
            out.append(name)
    return out


def pip(*args: str) -> int:
    return subprocess.call([sys.executable, "-m", "pip", "--disable-pip-version-check", *args])


def main() -> int:
    if sys.version_info < (3, 10):
        print(f"Python 3.10 or newer is needed (this is {sys.version.split()[0]}).")
        return 1
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    up_to_date = STAMP.exists() and STAMP.read_text().strip() == digest
    lacking = missing()
    if up_to_date and not lacking:
        return 0
    print("Installing / updating the program's packages (first run or after an update) ...")
    pip("install", "--upgrade", "pip")
    if pip("install", "-r", str(REQUIREMENTS)) != 0:
        print("\nPackage installation failed. This needs internet access once; check the connection or proxy.")
        return 1
    importlib.invalidate_caches()
    lacking = missing()
    if lacking:
        print("Still missing after installation: " + ", ".join(lacking))
        return 1
    STAMP.write_text(digest)
    print("All packages are installed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
