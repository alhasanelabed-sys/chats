"""SpeedFace-style ADMS terminal simulator.

Use it to try the server without a real device, or to load-test it:

    python tools/device_simulator.py --server http://127.0.0.1:8081 --sn SIM0001 --users 20 --days 5

It performs the same HTTP conversation as a real terminal: handshake,
option upload, heartbeats, executes queued commands (users, templates,
queries, reboot...) and uploads punches and enrolled templates.
"""
from __future__ import annotations

import argparse
import base64
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hader.adms.protocol import parse_kv  # noqa: E402


class HttpTransport:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def __call__(self, method: str, path: str, params: dict, body: bytes = b"") -> str:
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, data=body if method == "POST" else None, method=method,
                                     headers={"Content-Type": "text/plain", "User-Agent": "iClock Proxy/1.09"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")


class SimDevice:
    def __init__(self, transport, sn: str = "SIM0001", model: str = "SpeedFace-V5L",
                 firmware: str = "ZAM180-NF50VA-Ver3.0.36", face_ver: str = "39", fp_ver: str = "10",
                 palm_ver: str = "1", strict: bool = True, echo: bool = False, legacy: bool = False,
                 suffix: str = ""):
        # strict: like real firmware, a template for an unknown PIN is rejected (Return=-1)
        # echo:   upload an OPERLOG USER line after a server-side user change (some firmware do)
        # legacy: old push firmware without BIODATA / MultiBioDataSupport
        # suffix: ".aspx" for old iClock firmware paths
        self.strict, self.echo, self.legacy, self.suffix = strict, echo, legacy, suffix
        self.t, self.sn, self.model, self.firmware = transport, sn, model, firmware
        self.face_ver, self.fp_ver, self.palm_ver = face_ver, fp_ver, palm_ver
        self.users: dict[str, dict] = {}
        self.templates: dict[tuple, dict] = {}
        self.photos: dict[str, str] = {}
        self.attlog: list[tuple] = []
        self.options: dict[str, str] = {}
        self.executed: list[str] = []
        self.reboots = 0

    # ---------------------------------------------------------------- basic
    def _p(self, path: str) -> str:
        return path + self.suffix

    def handshake(self) -> dict:
        text = self.t("GET", self._p("/iclock/cdata"), {"SN": self.sn, "options": "all",
                                                         "pushver": "2.2.14" if self.legacy else "2.4.1",
                                                         "language": "69"})
        self.options = {}
        for line in text.splitlines():
            if line.startswith("GET OPTION FROM:"):
                self.options["GET OPTION FROM"] = line.split(":", 1)[1].strip()
                continue
            k, _, v = line.partition("=")
            self.options[k.strip()] = v.strip()
        info = (f"~DeviceName={self.model},MAC=00:17:61:12:34:56,FWVersion={self.firmware},"
                f"UserCount={len(self.users)},TransactionCount={len(self.attlog)},"
                f"FaceCount={self._count(9)},FPCount={self._count(1)},PvCount={self._count(8)},"
                f"FPVersion={self.fp_ver},FaceVersion={self.face_ver},"
                f"IPAddress=10.28.65.254,Platform=ZMM220_TFT")
        if not self.legacy:
            info += (f",MultiBioDataSupport=0:1:0:0:0:0:0:0:1:1,"
                     f"MultiBioVersion=0:{self.fp_ver}.0:0:0:0:0:0:0:{self.palm_ver}.0:{self.face_ver}.1")
        self.t("POST", self._p("/iclock/cdata"), {"SN": self.sn, "table": "options"}, info.encode())
        return self.options

    def _count(self, bio_type: int) -> int:
        return sum(1 for k in self.templates if k[1] == bio_type)

    def heartbeat(self) -> int:
        """One getrequest; executes whatever the server sent. Returns #commands."""
        text = self.t("GET", self._p("/iclock/getrequest"), {"SN": self.sn,
                                                     "INFO": f"{self.firmware},{len(self.users)},{self._count(1)},"
                                                             f"{len(self.attlog)},10.28.65.254,{self.fp_ver},"
                                                             f"{self.face_ver},0,{self._count(9)},101"})
        if text.strip() == "OK":
            return 0
        results = []
        n = 0
        for line in text.splitlines():
            if not line.startswith("C:"):
                continue
            _, cid, cmd = line.split(":", 2)
            ret, extra = self.execute(cmd)
            self.executed.append(cmd)
            results.append(f"ID={cid}&Return={ret}&CMD={cmd.split(' ')[0]}" + extra)
            n += 1
        if results:
            self.t("POST", self._p("/iclock/devicecmd"), {"SN": self.sn}, "\n".join(results).encode())
        return n

    def drain(self, rounds: int = 50) -> int:
        total = 0
        for _ in range(rounds):
            n = self.heartbeat()
            total += n
            if not n:
                break
        return total

    # ------------------------------------------------------------- commands
    def execute(self, cmd: str) -> tuple[int, str]:
        if cmd.startswith("DATA UPDATE USERINFO "):
            d = parse_kv(cmd[len("DATA UPDATE USERINFO "):])
            self.users[d["pin"]] = d
            if self.echo:
                line = (f"USER PIN={d['pin']}\tName={d.get('name', '')}\tPri={d.get('pri', 0)}\t"
                        f"Passwd={d.get('passwd', '')}\tCard={d.get('card', '')}\tGrp=1\tTZ=0\tVerify=0")
                self.t("POST", self._p("/iclock/cdata"), {"SN": self.sn, "table": "OPERLOG",
                                                          "Stamp": str(int(time.time()))}, line.encode())
            return 0, ""
        if cmd.startswith("DATA DELETE USERINFO "):
            pin = parse_kv(cmd[len("DATA DELETE USERINFO "):])["pin"]
            self.users.pop(pin, None)
            for k in [k for k in self.templates if k[0] == pin]:
                del self.templates[k]
            return 0, ""
        if cmd.startswith("DATA UPDATE BIODATA "):
            if self.legacy:
                return -1002, ""  # unknown command on old firmware
            d = parse_kv(cmd[len("DATA UPDATE BIODATA "):])
            if self.strict and d["pin"] not in self.users:
                return -1, ""
            self.templates[(d["pin"], int(d["type"]), int(d["no"]))] = d
            return 0, ""
        if cmd.startswith("DATA UPDATE FINGERTMP "):
            d = parse_kv(cmd[len("DATA UPDATE FINGERTMP "):])
            if self.strict and d["pin"] not in self.users:
                return -1, ""
            self.templates[(d["pin"], 1, int(d["fid"]))] = d
            return 0, ""
        if cmd.startswith("DATA DELETE FINGERTMP "):
            d = parse_kv(cmd[len("DATA DELETE FINGERTMP "):])
            for k in [k for k in self.templates if k[0] == d["pin"] and k[1] == 1]:
                del self.templates[k]
            return 0, ""
        if cmd.startswith("DATA DELETE BIODATA "):
            d = parse_kv(cmd[len("DATA DELETE BIODATA "):])
            for k in [k for k in self.templates if k[0] == d["pin"] and (not d.get("type") or k[1] == int(d["type"]))]:
                del self.templates[k]
            return 0, ""
        if cmd.startswith(("DATA UPDATE USERPIC ", "DATA UPDATE BIOPHOTO ")):
            d = parse_kv(cmd.split(" ", 3)[3])
            if self.strict and d["pin"] not in self.users:
                return -1, ""
            self.photos[d["pin"]] = d.get("content", "")
            return 0, ""
        if cmd.startswith("DATA QUERY ATTLOG"):
            d = parse_kv(cmd[len("DATA QUERY ATTLOG "):])
            lo = datetime.strptime(d["starttime"], "%Y-%m-%d %H:%M:%S")
            hi = datetime.strptime(d["endtime"], "%Y-%m-%d %H:%M:%S")
            rows = [r for r in self.attlog if lo <= r[1] <= hi]
            self._upload_att(rows)
            return len(rows), ""
        if cmd.startswith("DATA QUERY tablename="):
            fil = cmd.rsplit("filter=", 1)[-1]
            pin = fil.split("=", 1)[1] if fil.lower().startswith("pin=") else None
            table = cmd[len("DATA QUERY tablename="):].split(",", 1)[0]
            if table in ("biophoto", "userpic"):
                photos = {p: c for p, c in self.photos.items() if c and (pin is None or p == pin)}
                body = "\n".join(f"{table} pin={p}\tfilename={p}.jpg\ttype=9\tsize={len(c)}\tcontent={c}"
                                 for p, c in photos.items())
                self.t("POST", self._p("/iclock/querydata"), {"SN": self.sn, "type": "tabledata", "tablename": table,
                                                     "count": len(photos)}, body.encode())
                return 0, ""
            if pin is not None and table == "biodata":
                body = "\n".join("biodata " + "\t".join(f"{k}={v}" for k, v in d.items())
                                 for (p, _t, _n), d in self.templates.items() if p == pin)
                self.t("POST", self._p("/iclock/querydata"), {"SN": self.sn, "type": "tabledata",
                                                     "tablename": "biodata"}, body.encode())
                return 0, ""
        if cmd.startswith("DATA QUERY tablename=user"):
            body = "\n".join(f"user uid={i}\tcardno={u.get('card', '')}\tpin={p}\tpassword={u.get('passwd', '')}"
                             f"\tgroup=1\tstarttime=0\tendtime=0\tname={u.get('name', '')}\tprivilege={u.get('pri', 0)}"
                             f"\tdisable=0\tverify=0" for i, (p, u) in enumerate(self.users.items(), 1))
            self.t("POST", self._p("/iclock/querydata"), {"SN": self.sn, "type": "tabledata", "tablename": "user",
                                                 "count": len(self.users), "packcnt": 1, "packidx": 1}, body.encode())
            return 0, ""
        if cmd.startswith("DATA QUERY tablename=biodata"):
            body = "\n".join("biodata " + "\t".join(f"{k}={v}" for k, v in d.items())
                             for d in self.templates.values())
            self.t("POST", self._p("/iclock/querydata"), {"SN": self.sn, "type": "tabledata", "tablename": "biodata",
                                                 "count": len(self.templates)}, body.encode())
            return 0, ""
        if cmd == "INFO":
            return 0, (f"\n~DeviceName={self.model}\nFWVersion={self.firmware}\nUserCount={len(self.users)}"
                       f"\nTransactionCount={len(self.attlog)}\nFaceCount={self._count(9)}")
        if cmd == "CHECK":
            self.handshake()
            return 0, ""
        if cmd == "REBOOT":
            self.reboots += 1
            return 0, ""
        if cmd == "CLEAR LOG":
            self.attlog.clear()
            return 0, ""
        if cmd == "CLEAR DATA":
            self.users.clear()
            self.templates.clear()
            self.attlog.clear()
            return 0, ""
        if cmd.startswith(("ENROLL_BIO ", "ENROLL_FP ")):
            # the employee presents the finger / face / palm: the terminal uploads the template
            kv = {k.lower(): v for k, _, v in (p.partition("=") for p in cmd.split(" ", 1)[1].split("\t"))}
            btype = int(kv.get("type", 1) or 1)
            self.enroll(kv["pin"], self.users.get(kv["pin"], {}).get("name", ""), btype,
                        int(kv.get("no", kv.get("fid", 0)) or 0))
            return 0, ""
        if cmd.startswith(("SET OPTION", "CLEAR PHOTO", "RELOAD OPTIONS")):
            return 0, ""
        return -1, ""

    # ---------------------------------------------------------------- events
    def _upload_att(self, rows) -> str:
        body = "\n".join(f"{p}\t{ts:%Y-%m-%d %H:%M:%S}\t{st}\t{vf}\t0\t0\t0" for p, ts, st, vf in rows)
        return self.t("POST", self._p("/iclock/cdata"), {"SN": self.sn, "table": "ATTLOG",
                                                "Stamp": str(int(time.time()))}, body.encode())

    def punch(self, pin: str, ts: datetime | None = None, state: int = 0, verify: int = 15,
              upload: bool = True) -> None:
        row = (pin, ts or datetime.now().replace(microsecond=0), state, verify)
        self.attlog.append(row)
        if upload:
            self._upload_att([row])

    def upload_all_att(self) -> str:
        return self._upload_att(self.attlog)

    def enroll(self, pin: str, name: str = "", bio_type: int = 9, no: int = 0, upload: bool = True) -> None:
        """Enroll a user on the terminal: USER line + template + (face) photo."""
        self.users.setdefault(pin, {"pin": pin, "name": name, "pri": "0", "passwd": "", "card": ""})
        ver = {1: self.fp_ver, 8: self.palm_ver, 9: self.face_ver}.get(bio_type, "1")
        tmp = base64.b64encode(os.urandom(64)).decode()
        d = {"pin": pin, "no": str(no), "index": "0", "valid": "1", "duress": "0", "type": str(bio_type),
             "majorver": ver, "minorver": "1", "format": "0", "tmp": tmp}
        self.templates[(pin, bio_type, no)] = d
        if bio_type == 9:
            self.photos[pin] = base64.b64encode(b"\xff\xd8\xff\xe0" + os.urandom(200) + b"\xff\xd9").decode()
        if not upload:  # enrolled at the keypad while the terminal was not talking to us
            return
        user = f"USER PIN={pin}\tName={name}\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000\tVerify=0"
        self.t("POST", self._p("/iclock/cdata"), {"SN": self.sn, "table": "OPERLOG", "Stamp": str(int(time.time()))},
               (user + f"\nOPLOG 6\t0\t{datetime.now():%Y-%m-%d %H:%M:%S}\t{pin}\t0\t0\t0").encode())
        bio = (f"BIODATA Pin={pin}\tNo={no}\tIndex=0\tValid=1\tDuress=0\tType={bio_type}\tMajorVer={ver}"
               f"\tMinorVer=1\tFormat=0\tTmp={tmp}")
        self.t("POST", self._p("/iclock/cdata"), {"SN": self.sn, "table": "BIODATA", "Stamp": str(int(time.time()))},
               bio.encode())

    def edit_user_on_device(self, pin: str, **fields) -> None:
        """Admin changes a user on the terminal keypad (card, password, privilege...)."""
        u = self.users.setdefault(pin, {"pin": pin, "name": "", "pri": "0", "passwd": "", "card": ""})
        u.update({k.lower(): str(v) for k, v in fields.items()})
        line = (f"USER PIN={pin}\tName={u.get('name', '')}\tPri={u.get('pri', 0)}\tPasswd={u.get('passwd', '')}"
                f"\tCard={u.get('card', '')}\tGrp=1\tTZ=0000000100000000\tVerify=0")
        self.t("POST", self._p("/iclock/cdata"), {"SN": self.sn, "table": "OPERLOG", "Stamp": str(int(time.time()))},
               line.encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8081")
    ap.add_argument("--sn", default="SIM0001")
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--days", type=int, default=5, help="history of punches to generate")
    ap.add_argument("--loop", action="store_true", help="keep sending heartbeats and random punches")
    args = ap.parse_args()
    dev = SimDevice(HttpTransport(args.server), sn=args.sn)
    print("handshake:", dev.handshake().get("GET OPTION FROM"))
    print("commands executed:", dev.drain())
    for i in range(1, args.users + 1):
        dev.enroll(str(1000 + i), f"Sim User {i}", bio_type=9)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(args.days, 0, -1):
        day = today - timedelta(days=d)
        if day.weekday() in (4, 5):
            continue
        for i in range(1, args.users + 1):
            if random.random() < 0.08:
                continue  # absent
            cin = day + timedelta(hours=7, minutes=45 + random.randint(0, 40))
            cout = day + timedelta(hours=15, minutes=50 + random.randint(0, 45))
            dev.punch(str(1000 + i), cin, 0, upload=False)
            dev.punch(str(1000 + i), cout, 1, upload=False)
    print("upload:", dev.upload_all_att())
    print("commands executed:", dev.drain())
    while args.loop:
        time.sleep(10)
        if random.random() < 0.3:
            pin = str(1000 + random.randint(1, args.users))
            dev.punch(pin)
            print("punch", pin)
        dev.heartbeat()


if __name__ == "__main__":
    main()
