"""Updating the program in place: from the program's GitHub repository, or from a ZIP file.

    python -m hader.update --check            # is a newer version available?
    python -m hader.update --apply            # download and install it
    python -m hader.update --zip file.zip     # install a downloaded ZIP (no internet needed)

What it does: downloads the new version, stops the server, keeps a copy of the current
program files (in the data folder), puts the new files in place, installs any new
package, starts the server again and checks it answers. If the new version does not
start, the previous files are put back and started. The data folder (database, photos,
backups, settings) is never touched.

The repository is private, so downloading from it needs an access key (a GitHub
"fine-grained personal access token" with read-only access to the repository's
contents), entered once in the Service Manager.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from .config import BASE_DIR, WINDOWS, load_settings

REPO = os.getenv("HADER_UPDATE_REPO", "abdulrahmanahmedelabed-prog/iug-attendance-pro")
BRANCH = os.getenv("HADER_UPDATE_BRANCH", "main")
BUILD_FILE = BASE_DIR / ".build"           # which version is installed (commit, date)
TOKEN_FILE = "update_token"                # in the data folder
# never replaced or removed by an update
KEEP = {".venv", "venv", "data", "hader.ini", ".build", ".git", "tools/cloudflared.exe"}
# folders whose files are exactly those of the new version (stale files are removed)
MANAGED = ("hader", "web", "tools")
KEEP_BACKUPS = 3


class UpdateError(Exception):
    pass


# --------------------------------------------------------------------------- what is installed / available

def installed() -> dict:
    try:
        return json.loads(BUILD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def token() -> str:
    try:
        return (load_settings().data_dir / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_token(value: str) -> None:
    path = load_settings().data_dir / TOKEN_FILE
    value = value.strip()
    if value:
        path.write_text(value, encoding="utf-8")
        if not WINDOWS:
            os.chmod(path, 0o600)
    elif path.exists():
        path.unlink()


def _get(url: str, accept: str = "application/vnd.github+json", timeout: int = 30):
    headers = {"Accept": accept, "User-Agent": "hader-updater", "X-GitHub-Api-Version": "2022-11-28"}
    tok = token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise UpdateError("لا يمكن الوصول إلى المستودع: أدخل مفتاح الوصول أو تحقق منه / "
                              "The repository cannot be reached: enter or check the access key") from exc
        raise UpdateError(f"GitHub: {exc.code} {exc.reason}") from exc
    except OSError as exc:
        raise UpdateError(f"لا يوجد اتصال بالإنترنت / No internet connection ({exc})") from exc


def latest() -> dict:
    """The newest version on the repository: {sha, date, message}."""
    with _get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}") as r:
        c = json.loads(r.read())
    return {"sha": c["sha"], "date": c["commit"]["committer"]["date"][:10],
            "message": c["commit"]["message"].splitlines()[0]}


def changes(since: str, to: str, limit: int = 12) -> list[str]:
    """What changed since the installed version (newest first, first line of each change)."""
    if not since:
        return []
    try:
        with _get(f"https://api.github.com/repos/{REPO}/compare/{since}...{to}") as r:
            data = json.loads(r.read())
    except UpdateError:
        return []
    msgs = [c["commit"]["message"].splitlines()[0] for c in data.get("commits", [])]
    return list(reversed(msgs))[:limit]


def check() -> dict:
    cur, new = installed(), latest()
    return {"installed": cur, "latest": new, "available": cur.get("sha") != new["sha"],
            "changes": changes(cur.get("sha", ""), new["sha"])}


def download(sha: str, folder: Path) -> Path:
    dest = folder / "update.zip"
    with _get(f"https://api.github.com/repos/{REPO}/zipball/{sha}", timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


# --------------------------------------------------------------------------- installing

def extract(zip_path: Path, folder: Path) -> tuple[Path, dict]:
    """Unpack a release ZIP; returns the program folder inside it and what version it is."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            sha = (z.comment or b"").decode("ascii", "ignore").strip()
            z.extractall(folder / "new")
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateError(f"ملف التحديث غير صالح / Not a valid update file ({exc})") from exc
    root = folder / "new"
    while not (root / "run.py").exists():
        subs = [p for p in root.iterdir() if p.is_dir()]
        if len(subs) != 1:
            raise UpdateError("هذا الملف ليس نسخة من البرنامج / This file is not a version of the program")
        root = subs[0]
    if not (root / "hader" / "version.py").exists():
        raise UpdateError("هذا الملف ليس نسخة من البرنامج / This file is not a version of the program")
    if not sha and "-" in root.name:   # zipball folders end with the commit: owner-repo-1a2b3c4
        sha = root.name.rsplit("-", 1)[-1]
    return root, {"sha": sha, "date": datetime.now().strftime("%Y-%m-%d")}


def _kept(rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return top in KEEP or rel in KEEP or top.startswith(".")


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and not _kept(p.relative_to(root).as_posix())}


def backup_program(dest: Path) -> Path:
    for rel in _files(BASE_DIR):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE_DIR / rel, target)
    return dest


def put_in_place(src: Path) -> None:
    new = _files(src)
    for rel in sorted(new):
        target = BASE_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)
    # files the new version no longer has (in the program's own folders only)
    for rel in _files(BASE_DIR) - new:
        if rel.split("/", 1)[0] in MANAGED:
            try:
                (BASE_DIR / rel).unlink()
            except OSError:
                pass


def install_packages() -> None:
    """New version, maybe new packages: the same check start.bat does."""
    exe = Path(sys.executable)
    if WINDOWS and exe.name.lower() == "pythonw.exe":
        exe = exe.parent / "python.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run([str(exe), str(BASE_DIR / "tools" / "bootstrap.py")], capture_output=True, text=True,
                       timeout=900, creationflags=flags, cwd=str(BASE_DIR))
    if r.returncode != 0:
        raise UpdateError("تعذّر تثبيت مكتبات النسخة الجديدة / Could not install the new version's packages: "
                          + (r.stdout + r.stderr).strip()[-300:])


def apply(src: Path, version: dict, say=print) -> dict:
    """Install the program files in `src` (an unpacked version). Rolls back if it does not start."""
    from . import manager as M
    s = load_settings()
    port = s.web_port
    was_running = bool(M.ping(port))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = s.data_dir / "program_backups"
    backup = backups / stamp
    say("نسخ احتياطي للبرنامج الحالي… / Keeping a copy of the current program…")
    backup_program(backup)
    old_build = installed()
    if was_running:
        say("إيقاف الخادم… / Stopping the server…")
        M.stop_server(port)
    try:
        say("وضع الملفات الجديدة… / Putting the new files in place…")
        put_in_place(src)
        say("تثبيت المكتبات الجديدة إن وجدت… / Installing new packages if any…")
        install_packages()
        BUILD_FILE.write_text(json.dumps(version), encoding="utf-8")
        if was_running:
            say("تشغيل الخادم… / Starting the server…")
            M.start_server()
            if not _wait(load_settings().web_port):
                raise UpdateError("لم تبدأ النسخة الجديدة / The new version did not start")
    except Exception as exc:
        say("إرجاع النسخة السابقة… / Putting the previous version back…")
        if was_running:
            M.stop_server(load_settings().web_port)   # a half-started new version must not hold the port
        put_in_place(backup)
        if old_build:
            BUILD_FILE.write_text(json.dumps(old_build), encoding="utf-8")
        else:
            BUILD_FILE.unlink(missing_ok=True)
        if was_running:
            M.start_server()
            _wait(load_settings().web_port)
        raise UpdateError(f"{exc} — أُعيدت النسخة السابقة / the previous version was put back") from exc
    for old in sorted(p for p in backups.iterdir() if p.is_dir())[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return {"ok": True, "version": version, "backup": str(backup)}


def _wait(port: int, seconds: int = 40) -> bool:
    from . import manager as M
    for _ in range(seconds * 2):
        if M.ping(port, 0.5):
            return True
        time.sleep(0.5)
    return False


def update_from_github(say=print) -> dict:
    info = latest()
    with tempfile.TemporaryDirectory(prefix="hader-update-") as tmp:
        say("تنزيل النسخة الجديدة… / Downloading the new version…")
        z = download(info["sha"], Path(tmp))
        src, _v = extract(z, Path(tmp))
        return apply(src, info, say)


def update_from_zip(zip_path: str | Path, say=print) -> dict:
    with tempfile.TemporaryDirectory(prefix="hader-update-") as tmp:
        src, version = extract(Path(zip_path), Path(tmp))
        return apply(src, version, say)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Update Hader")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--zip")
    a = ap.parse_args()
    try:
        if a.zip:
            print(update_from_zip(a.zip))
        elif a.apply:
            print(update_from_github())
        else:
            r = check()
            print(json.dumps(r, ensure_ascii=False, indent=2))
    except UpdateError as exc:
        print(f"✗ {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
