"""Runtime settings.

Everything can be overridden with environment variables or a ``hader.ini``
file next to the program (``[server]`` section, same names in lower case).
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"


# Names used by earlier versions of this program, read once to bring data and settings over.
_OLD_NAME = "zk" + "pro"
_OLD_DATA_DIR = "ZK" + "AttendancePro"


def _ini() -> dict[str, str]:
    path = Path(os.getenv("HADER_CONFIG", BASE_DIR / "hader.ini"))
    old = BASE_DIR / f"{_OLD_NAME}.ini"
    if not path.exists() and old.exists():
        try:
            old.rename(path)
        except OSError:
            path = old
    if not path.exists():
        return {}
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    return dict(cp["server"]) if cp.has_section("server") else {}


def _ports(value: str) -> list[int]:
    return [int(p) for p in str(value).replace(";", ",").split(",") if p.strip()]


@dataclass
class Settings:
    data_dir: Path
    database_url: str
    host: str
    web_port: int
    portal_port: int = 0   # serves the employee portal only (safe to open to the internet); 0 = off
    adms_ports: list[int] = field(default_factory=list)
    secret_key: str = ""
    session_hours: int = 12
    # A device that has not contacted the server for this many seconds is offline.
    offline_after: int = 120

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


def _bring_over(src_db: Path, dest: Path) -> None:
    """Copy an earlier data folder into ``dest`` and give its files the current names."""
    import shutil
    shutil.copytree(src_db.parent, dest, dirs_exist_ok=True)
    _rename_old_files(dest)


def _rename_old_files(folder: Path) -> None:
    old_db = folder / f"{_OLD_NAME}.db"
    if old_db.exists() and not (folder / "hader.db").exists():
        old_db.rename(folder / "hader.db")
    backups = folder / "backups"
    if backups.is_dir():
        for f in backups.glob(f"{_OLD_NAME}_*.db"):
            f.rename(f.with_name("hader_" + f.name[len(_OLD_NAME) + 1:]))


def _default_data_dir() -> Path:
    """Where the database lives when hader.ini does not say.

    On Windows it is a fixed folder (C:\\ProgramData\\Hader) so that downloading a new
    version into another folder never starts from an empty database. The first time, the
    biggest database of an earlier copy (the previous shared folder, or …/<any folder>/data
    next to this one) is brought over, so terminals, employees and punches are kept."""
    local = BASE_DIR / "data"
    if not WINDOWS:
        _rename_old_files(local) if local.is_dir() else None
        return local
    root = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData"))
    shared = root / "Hader"
    if (shared / "hader.db").exists():
        return shared
    candidates = [root / _OLD_DATA_DIR / f"{_OLD_NAME}.db"]
    for name in ("hader.db", f"{_OLD_NAME}.db"):
        candidates += [local / name, *BASE_DIR.parent.glob(f"*/data/{name}")]
    found = [c for c in dict.fromkeys(candidates) if c.exists()]
    try:
        shared.mkdir(parents=True, exist_ok=True)
        if found:
            _bring_over(max(found, key=lambda c: c.stat().st_size), shared)
        return shared
    except OSError:
        return local


NETWORK_FILE = "network.ini"   # address and ports chosen inside the program (System settings)
NETWORK_KEYS = ("host", "web_port", "portal_port", "adms_ports")


def read_network(data_dir: Path) -> dict[str, str]:
    path = data_dir / NETWORK_FILE
    if not path.exists():
        return {}
    cp = configparser.ConfigParser()
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error:
        return {}
    return {k: v for k, v in (dict(cp["server"]) if cp.has_section("server") else {}).items() if k in NETWORK_KEYS}


def write_network(data_dir: Path, values: dict[str, str]) -> None:
    cp = configparser.ConfigParser()
    cp["server"] = {k: str(values[k]) for k in NETWORK_KEYS if k in values}
    tmp = data_dir / (NETWORK_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("; Written by Hader (System settings > Server address and ports). Delete to go back to hader.ini.\n")
        cp.write(f)
    os.replace(tmp, data_dir / NETWORK_FILE)


def load_settings() -> Settings:
    ini = _ini()
    net: dict[str, str] = {}

    def get(name: str, default: str) -> str:
        return os.getenv("HADER_" + name.upper(), net.get(name, ini.get(name, default)))

    configured = get("data_dir", "")
    data_dir = Path(configured) if configured else _default_data_dir()
    if not data_dir.is_absolute():  # relative to the program, not to the (service) working dir
        data_dir = BASE_DIR / data_dir
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    net = read_network(data_dir)   # chosen in the program: wins over hader.ini (the command line still wins)
    db_url = get("database_url", f"sqlite:///{(data_dir / 'hader.db').as_posix()}")
    s = Settings(
        data_dir=data_dir,
        database_url=db_url,
        host=get("host", "0.0.0.0"),
        web_port=int(get("web_port", "8090")),
        portal_port=int(get("portal_port", "8091") or 0),
        adms_ports=_ports(get("adms_ports", "8081,90")),
        secret_key=get("secret_key", ""),
        session_hours=int(get("session_hours", "12")),
        offline_after=int(get("offline_after", "120")),
    )
    s.photos_dir.mkdir(parents=True, exist_ok=True)
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    if not s.secret_key:
        key_file = data_dir / ".secret_key"
        if not key_file.exists():
            key_file.write_text(os.urandom(32).hex(), encoding="utf-8")
        s.secret_key = key_file.read_text(encoding="utf-8").strip()
    return s


settings = load_settings()
