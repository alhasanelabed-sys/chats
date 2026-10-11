"""Service Manager: a small desktop window on the server PC to run the server and set its
address and ports — it works even when the server is stopped or cannot be reached.

    manager.bat                 (Windows: asks for administrator rights once)
    python -m hader.manager

Only the standard library is used (tkinter), and only the configuration module of the
program, so it opens instantly and never depends on the server running.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from . import update as U
from .config import BASE_DIR, WINDOWS, load_settings, write_network
from .version import VERSION

TASK = "Hader"
LANG_FILE = "manager_lang"
BRAND = {"ar": ("حاضر", "ح"), "en": ("Hader", "H")}
TEAL, TEAL_D, INK, MUTED, BG, CARD, OK, BAD, WARN = ("#0e8f86", "#0a6f68", "#13202c", "#5b6b7b", "#f2f4f7", "#ffffff",
                                                    "#0ca30c", "#d03b3b", "#b26b00")


# --------------------------------------------------------------------------- server control (no UI)

def settings():
    return load_settings()


def ping(port: int, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def control(port: int, action: str) -> bool:
    import hashlib
    import hmac
    s = settings()
    token = hmac.new(s.secret_key.encode(), b"hader-control", hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/system/control", method="POST",
                                 data=json.dumps({"action": action}).encode(),
                                 headers={"Content-Type": "application/json", "X-Hader-Control": token})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status == 200
    except OSError:
        return False


def pid() -> int | None:
    try:
        return int((settings().data_dir / "hader.pid").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def alive(p: int | None) -> bool:
    if not p:
        return False
    if WINDOWS:
        r = _run(["tasklist", "/FI", f"PID eq {p}", "/NH"])
        return str(p) in (r.stdout or "")
    try:
        os.kill(p, 0)
        return True
    except OSError:
        return False


def _run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def python_exe(windowless: bool = True) -> str:
    exe = Path(sys.executable)
    if WINDOWS and windowless and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        return str(exe.parent / "pythonw.exe")
    if WINDOWS and not windowless and exe.name.lower() == "pythonw.exe":
        return str(exe.parent / "python.exe")
    return str(exe)


def is_admin() -> bool:
    if not WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def task_installed() -> bool:
    return WINDOWS and _run(["schtasks", "/Query", "/TN", TASK]).returncode == 0


def start_server() -> None:
    if task_installed() and _run(["schtasks", "/Run", "/TN", TASK]).returncode == 0:
        return
    run_py = str(BASE_DIR / "run.py")
    if WINDOWS:
        flags = 0x00000008 | 0x00000200 | 0x08000000   # detached, new group, no window
        subprocess.Popen([python_exe(), run_py], cwd=str(BASE_DIR), creationflags=flags, close_fds=True)
    else:
        subprocess.Popen([python_exe(), run_py], cwd=str(BASE_DIR), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop_server(port: int) -> None:
    """Ask the server to stop; end its process if it does not answer."""
    import time
    if control(port, "stop"):
        for _ in range(20):
            time.sleep(0.5)
            if not ping(port, 0.5) and not alive(pid()):
                return
    p = pid()
    if p and alive(p):
        if WINDOWS:
            _run(["taskkill", "/PID", str(p), "/T", "/F"])
        else:
            try:
                os.kill(p, 15)
            except OSError:
                pass


def set_autostart(on: bool) -> bool:
    if not WINDOWS:
        return False
    if on:
        tr = f'"{python_exe()}" "{BASE_DIR / "run.py"}"'
        return _run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "ONSTART", "/RU", "SYSTEM", "/RL", "HIGHEST",
                     "/TR", tr]).returncode == 0
    return _run(["schtasks", "/Delete", "/TN", TASK, "/F"]).returncode == 0


def lan_addresses() -> list[str]:
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith(("127.", "169.254."))]


def port_free(host: str, port: int) -> bool:
    """Same test the server makes when it opens a port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if not WINDOWS:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def backup_now() -> Path:
    s = settings()
    src = s.database_url.split("sqlite:///", 1)[-1]
    dest = s.backups_dir / f"hader_{datetime.now():%Y%m%d_%H%M%S}_manager.db"
    with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
        a.backup(b)
    return dest


def open_path(path: str) -> None:
    if WINDOWS:
        os.startfile(path)  # noqa: S606 - a folder or file of the program
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------- window

class Manager:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.lang = self._load_lang()
        self.root = tk.Tk()
        self.root.configure(bg=BG)
        self.root.minsize(640, 640)
        self.busy = False
        self._timer = None
        self._last = ("", MUTED)
        self._tab = "net"
        self._upd_text = ""
        self._upd_available = False
        self.root.geometry("680x820")
        self._style()
        self.build()
        self.refresh()
        if U.token():
            threading.Thread(target=self._quiet_check, daemon=True).start()

    def _quiet_check(self) -> None:
        try:
            r = U.check()
        except U.UpdateError:
            return
        if r["available"]:
            new = r["latest"]
            self._upd_available = True
            self._upd_text = "\n".join([self.T(f"متوفر: {new['sha'][:7]} · {new['date']}", f"Available: {new['sha'][:7]} · {new['date']}")]
                                       + ["• " + (m if len(m) <= 110 else m[:107] + "…") for m in (r["changes"] or [new["message"]])[:8]])
            self.say(self.T("🔔 يوجد تحديث جديد — افتح تبويب «التحديثات».", "🔔 An update is available — open the “Updates” tab."), OK)
            self.root.after(0, self.build)

    # ---- helpers
    def T(self, ar: str, en: str) -> str:
        return ar if self.lang == "ar" else en

    def _load_lang(self) -> str:
        try:
            return (settings().data_dir / LANG_FILE).read_text(encoding="ascii").strip() or "ar"
        except OSError:
            return "ar"

    def _style(self) -> None:
        st = self.ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except self.tk.TclError:
            pass
        font = ("Segoe UI", 10)
        st.configure(".", font=font, background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("Card.TLabel", background=CARD, foreground=INK)
        st.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("H.TLabel", background=CARD, foreground=INK, font=("Segoe UI Semibold", 11))
        st.configure("TButton", padding=(14, 6))
        st.configure("Accent.TButton", background=TEAL, foreground="#fff", padding=(16, 7))
        st.map("Accent.TButton", background=[("active", TEAL_D), ("disabled", "#9fb7b4")])
        st.configure("TCheckbutton", background=CARD)
        st.configure("TEntry", padding=5)

    def card(self, parent, title: str):
        tk = self.tk
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="x", padx=14, pady=(0, 10))
        f = self.ttk.Frame(outer, style="Card.TFrame", padding=14)
        f.pack(fill="x")
        self.ttk.Label(f, text=title, style="H.TLabel", anchor=self.e).pack(fill="x", pady=(0, 8))
        return f

    @property
    def e(self) -> str:   # the "start" side for text
        return "e" if self.lang == "ar" else "w"

    @property
    def side(self) -> str:
        return "right" if self.lang == "ar" else "left"

    def grid_buttons(self, parent, buttons, cols: int) -> None:
        """Buttons in even columns, laid out from the reading side."""
        for i, b in enumerate(buttons):
            col = i % cols
            b.grid(row=i // cols, column=(cols - 1 - col) if self.lang == "ar" else col, sticky="ew", padx=3, pady=3)
        for c in range(cols):
            parent.columnconfigure(c, weight=1, uniform="b")

    def row(self, parent, label: str, widget_factory):
        f = self.ttk.Frame(parent, style="Card.TFrame")
        f.pack(fill="x", pady=3)
        self.ttk.Label(f, text=label, style="Card.TLabel", width=30, anchor=self.e).pack(side=self.side)
        w = widget_factory(f)
        w.pack(side=self.side, fill="x", expand=True, padx=(0, 8) if self.lang == "ar" else (8, 0))
        return w

    # ---- layout
    def build(self) -> None:
        tk, ttk, T = self.tk, self.ttk, self.T
        for w in self.root.winfo_children():
            w.destroy()
        name, mark = BRAND[self.lang]
        self.root.title(T(f"مدير خادم {name}", f"{name} Service Manager"))
        s = settings()

        head = tk.Frame(self.root, bg=INK, padx=16, pady=14)
        head.pack(fill="x")
        logo = tk.Label(head, text=mark, bg=TEAL, fg="#fff", font=("Segoe UI", 18, "bold"), width=2)
        logo.pack(side=self.side)
        txt = tk.Frame(head, bg=INK)
        txt.pack(side=self.side, padx=12)
        tk.Label(txt, text=T(f"مدير خادم {name}", f"{name} Service Manager"), bg=INK, fg="#fff",
                 font=("Segoe UI Semibold", 13)).pack(anchor=self.e)
        tk.Label(txt, text=T(f"الإصدار {VERSION}", f"Version {VERSION}"), bg=INK, fg="#9db0c1",
                 font=("Segoe UI", 9)).pack(anchor=self.e)
        tk.Button(head, text="English" if self.lang == "ar" else "العربية", command=self.toggle_lang, relief="flat",
                  bg="#24384a", fg="#fff", activebackground="#2f4a61", padx=10).pack(side="left" if self.lang == "ar" else "right")
        self.msg = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor=self.e,
                            justify="right" if self.lang == "ar" else "left", wraplength=600)
        self.msg.pack(fill="x", padx=16, pady=(8, 6))
        self.msg.configure(text=self._last[0], fg=self._last[1])

        # status
        c = self.card(self.root, T("حالة الخادم", "Server status"))
        top = ttk.Frame(c, style="Card.TFrame")
        top.pack(fill="x")
        self.dot = tk.Label(top, text="●", bg=CARD, fg=MUTED, font=("Segoe UI", 16))
        self.dot.pack(side=self.side)
        self.state = ttk.Label(top, text="…", style="H.TLabel")
        self.state.pack(side=self.side, padx=6)
        self.urls = ttk.Label(c, text="", style="Muted.TLabel", anchor=self.e, justify="right" if self.lang == "ar" else "left")
        self.urls.pack(fill="x", pady=(6, 8))
        btns = ttk.Frame(c, style="Card.TFrame")
        btns.pack(fill="x")
        self.b_start = ttk.Button(btns, text=T("تشغيل", "Start"), style="Accent.TButton", command=lambda: self.bg(self.do_start))
        self.b_stop = ttk.Button(btns, text=T("إيقاف", "Stop"), command=lambda: self.bg(self.do_stop))
        self.b_restart = ttk.Button(btns, text=T("إعادة تشغيل", "Restart"), command=lambda: self.bg(self.do_restart))
        self.b_open = ttk.Button(btns, text=T("فتح البرنامج", "Open program"), command=self.do_open)
        self.grid_buttons(btns, [self.b_start, self.b_stop, self.b_restart, self.b_open], 4)

        # the rest in tabs
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        tabs = {}
        for key, label in (("net", T("العنوان والمنافذ", "Address and ports")), ("tools", T("التشغيل والأدوات", "Start-up and tools")),
                           ("upd", T("التحديثات", "Updates"))):
            tabs[key] = tk.Frame(nb, bg=BG, pady=10)
            nb.add(tabs[key], text="  " + label + "  ")
        if self.lang == "ar":   # tabs read from the right
            for key in ("upd", "tools", "net"):
                nb.insert(0, tabs[key])
        nb.select(tabs[self._tab] if self._tab in tabs else tabs["net"])
        nb.bind("<<NotebookTabChanged>>", lambda e: setattr(self, "_tab", next(k for k, f in tabs.items() if str(f) == nb.select())))

        # address and ports
        c = self.card(tabs["net"], T("عنوان الخادم والمنافذ", "Server address and ports"))
        addrs = ["0.0.0.0", *lan_addresses(), "127.0.0.1"]
        self.v_host = tk.StringVar(value=s.host)
        self.v_web = tk.StringVar(value=str(s.web_port))
        self.v_portal = tk.StringVar(value=str(s.portal_port))
        self.v_adms = tk.StringVar(value=", ".join(map(str, s.adms_ports)))
        self.row(c, T("عنوان IP (0.0.0.0 = الكل)", "Server IP (0.0.0.0 = all networks)"),
                 lambda f: ttk.Combobox(f, textvariable=self.v_host, values=addrs, width=22))
        self.row(c, T("منفذ واجهة البرنامج", "Program (web) port"), lambda f: ttk.Entry(f, textvariable=self.v_web, width=10))
        self.row(c, T("منفذ البوابة (0 = إيقاف)", "Employee portal port (0 = off)"),
                 lambda f: ttk.Entry(f, textvariable=self.v_portal, width=10))
        self.row(c, T("منافذ استقبال الأجهزة", "Terminal (ADMS) ports"), lambda f: ttk.Entry(f, textvariable=self.v_adms, width=22))
        ttk.Label(c, text=T("منفذ الأجهزة يجب أن يطابق «الخادم السحابي» على الأجهزة. إن تعذّر فتح المنفذ الجديد يعود الخادم للسابق.",
                            "The terminal port must match “Cloud server” on the terminals. If a new port cannot be opened the server goes back to the previous one."),
                  style="Muted.TLabel", anchor=self.e, wraplength=500, justify="right" if self.lang == "ar" else "left").pack(fill="x", pady=(6, 6))
        self.b_apply = ttk.Button(c, text=T("حفظ وتطبيق", "Save and apply"), style="Accent.TButton", command=lambda: self.bg(self.do_apply))
        self.b_apply.pack(side=self.side)

        # windows start-up + tools
        c = self.card(tabs["tools"], T("التشغيل والأدوات", "Start-up and tools"))
        self.v_auto = tk.BooleanVar(value=task_installed())
        ttk.Checkbutton(c, text=T("تشغيل الخادم تلقائياً مع ويندوز (قبل تسجيل الدخول)", "Start the server with Windows (before anyone signs in)"),
                        variable=self.v_auto, command=lambda: self.bg(self.do_autostart), style="TCheckbutton").pack(anchor=self.e)
        tools = ttk.Frame(c, style="Card.TFrame")
        tools.pack(fill="x", pady=(8, 0))
        self.grid_buttons(tools, [ttk.Button(tools, text=text, command=cmd) for text, cmd in (
            (T("نسخة احتياطية الآن", "Back up now"), lambda: self.bg(self.do_backup)),
            (T("مجلد البيانات", "Data folder"), lambda: open_path(str(settings().data_dir))),
            (T("سجل الخادم", "Server log"), self.do_log),
            (T("فتح المنافذ بالجدار", "Open firewall ports"), lambda: self.bg(self.do_firewall)),
            (T("كلمة مرور المدير", "Reset admin password"), lambda: self.bg(self.do_reset)),
            (T("اختصار سطح المكتب", "Desktop shortcut"), lambda: self.bg(self.do_shortcut)))], 3)

        # updates
        c = self.card(tabs["upd"], T("تحديث البرنامج", "Update the program"))
        cur = U.installed()
        self.upd_cur = ttk.Label(c, style="Card.TLabel", anchor=self.e, text=T("النسخة المثبتة: ", "Installed: ") + (
            f"{cur.get('sha', '')[:7]} · {cur.get('date', '')}" if cur.get("sha") else T("غير معروفة (ثُبّتت يدوياً)", "unknown (installed by hand)")))
        self.upd_cur.pack(fill="x")
        self.upd_new = ttk.Label(c, style="Muted.TLabel", anchor=self.e, justify="right" if self.lang == "ar" else "left",
                                 wraplength=560, text=self._upd_text or T("اضغط «البحث عن تحديث».", "Press “Check for updates”."))
        self.upd_new.pack(fill="x", pady=(4, 8))
        ub = ttk.Frame(c, style="Card.TFrame")
        ub.pack(fill="x")
        self.b_check = ttk.Button(ub, text=T("البحث عن تحديث", "Check for updates"), command=lambda: self.bg(self.do_check))
        self.b_update = ttk.Button(ub, text=T("تحديث الآن", "Update now"), style="Accent.TButton", command=lambda: self.bg(self.do_update))
        self.b_zip = ttk.Button(ub, text=T("تحديث من ملف ZIP…", "Update from a ZIP file…"), command=self.do_zip)
        self.grid_buttons(ub, [self.b_check, self.b_update, self.b_zip], 3)
        self.b_update.state(["!disabled"] if self._upd_available else ["disabled"])
        c = self.card(tabs["upd"], T("مفتاح الوصول إلى المستودع", "Repository access key"))
        ttk.Label(c, style="Muted.TLabel", anchor=self.e, justify="right" if self.lang == "ar" else "left", wraplength=560,
                  text=T("المستودع خاص، فالتحديث يحتاج «مفتاح قراءة فقط» يُنشأ مرة واحدة:\n"
                         "1. اضغط «إنشاء مفتاح في GitHub» وسجّل الدخول.\n"
                         "2. Repository access ← Only select repositories ← iug-attendance-pro\n"
                         "3. Permissions ← Contents ← Read-only ، ثم Generate token\n"
                         "4. انسخ المفتاح والصقه هنا ثم «حفظ المفتاح».",
                         "The repository is private, so updating needs a read-only key, created once:\n"
                         "1. Press “Create a key on GitHub” and sign in.\n"
                         "2. Repository access > Only select repositories > iug-attendance-pro\n"
                         "3. Permissions > Contents > Read-only, then Generate token\n"
                         "4. Copy the key, paste it here and press “Save key”.")).pack(fill="x", pady=(0, 6))
        self.v_token = tk.StringVar(value="•" * 12 if U.token() else "")
        self.row(c, T("المفتاح", "Key"), lambda f: ttk.Entry(f, textvariable=self.v_token, show="•", width=40))
        kb = ttk.Frame(c, style="Card.TFrame")
        kb.pack(fill="x", pady=(6, 0))
        self.grid_buttons(kb, [ttk.Button(kb, text=T("حفظ المفتاح", "Save key"), command=self.do_token),
                               ttk.Button(kb, text=T("إنشاء مفتاح في GitHub", "Create a key on GitHub"),
                                          command=lambda: webbrowser.open("https://github.com/settings/personal-access-tokens/new"))], 2)

        if WINDOWS and not is_admin():
            self.say(T("بعض الأوامر تحتاج صلاحيات المسؤول: افتح المدير بزر الفأرة الأيمن ← تشغيل كمسؤول.",
                       "Some actions need administrator rights: right-click the manager > Run as administrator."), WARN)

    # ---- actions
    def say(self, text: str, color: str = MUTED) -> None:
        self._last = (text, color)
        self.root.after(0, lambda: self.msg.configure(text=text, fg=color))

    def bg(self, fn) -> None:
        if self.busy:
            return
        self.busy = True

        def work():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - shown to the user
                text = str(exc)
                if " / " in text:
                    parts = text.split(" — ")
                    text = " — ".join(p.split(" / ")[0 if self.lang == "ar" else -1] for p in parts)
                self.say(f"✗ {text}", BAD)
            finally:
                self.busy = False
                self.root.after(0, self.refresh)
        threading.Thread(target=work, daemon=True).start()

    def wait_up(self, port: int, seconds: int = 25) -> bool:
        import time
        for _ in range(seconds * 2):
            if ping(port, 0.5):
                return True
            time.sleep(0.5)
        return False

    def do_start(self) -> None:
        T = self.T
        s = settings()
        if ping(s.web_port):
            return self.say(T("الخادم يعمل.", "The server is running."), OK)
        self.say(T("جارٍ التشغيل…", "Starting…"))
        start_server()
        self.say(T("✓ الخادم يعمل.", "✓ The server is running."), OK) if self.wait_up(s.web_port) else \
            self.say(T("لم يبدأ الخادم خلال 25 ثانية — افتح «سجل الخادم» لمعرفة السبب.",
                       "The server did not start within 25 s — open “Server log” to see why."), BAD)

    def do_stop(self) -> None:
        T = self.T
        self.say(T("جارٍ الإيقاف…", "Stopping…"))
        stop_server(settings().web_port)
        self.say(T("أُوقف الخادم. الأجهزة تحتفظ بالحركات وترسلها عند تشغيله.", "Server stopped. Terminals keep their punches and send them when it runs again."))

    def do_restart(self) -> None:
        T = self.T
        port = settings().web_port
        if ping(port) and control(port, "restart"):
            self.say(T("جارٍ إعادة التشغيل…", "Restarting…"))
            import time
            time.sleep(1.5)
            ok = self.wait_up(settings().web_port)
        else:
            stop_server(port)
            start_server()
            ok = self.wait_up(settings().web_port)
        self.say(T("✓ أُعيد التشغيل.", "✓ Restarted.") if ok else T("لم يعد الخادم للعمل — راجع «سجل الخادم».", "The server did not come back — see “Server log”."),
                 OK if ok else BAD)

    def do_apply(self) -> None:
        import time
        T = self.T
        s = settings()
        host = self.v_host.get().strip() or "0.0.0.0"
        try:
            web, portal = int(self.v_web.get()), int(self.v_portal.get() or 0)
            adms = [int(p) for p in self.v_adms.get().replace(";", ",").split(",") if p.strip()]
        except ValueError:
            return self.say(T("أرقام المنافذ غير صحيحة.", "Invalid port numbers."), BAD)
        every = [web, *([portal] if portal else []), *adms]
        if any(not 1 <= p <= 65535 for p in every) or len(set(every)) != len(every):
            return self.say(T("كل منفذ بين 1 و65535 ومختلف عن غيره.", "Ports must be 1-65535 and all different."), BAD)
        if host not in ("0.0.0.0", "127.0.0.1", *lan_addresses()):
            return self.say(T("هذا العنوان ليس من عناوين هذا الحاسوب.", "That is not an address of this PC."), BAD)
        running = bool(ping(s.web_port))
        mine = {s.web_port, s.portal_port, *s.adms_ports} if running else set()
        for p in (web, *([portal] if portal else [])):
            if p not in mine and not port_free(host, p):
                return self.say(T(f"المنفذ {p} مستخدم من برنامج آخر.", f"Port {p} is used by another program."), BAD)
        write_network(s.data_dir, {"host": host, "web_port": web, "portal_port": portal, "adms_ports": ",".join(map(str, adms))})
        if not running:
            return self.say(T("✓ حُفظت. ستُستخدم عند تشغيل الخادم.", "✓ Saved. Used when the server starts."), OK)
        self.say(T("جارٍ التطبيق وإعادة التشغيل…", "Applying and restarting…"))
        if not control(s.web_port, "restart"):
            stop_server(s.web_port)
            start_server()
        time.sleep(1.5)
        if self.wait_up(web, 20):
            self.say(T(f"✓ الخادم يعمل الآن على المنفذ {web}.", f"✓ The server now runs on port {web}."), OK)
        elif ping(s.web_port):
            self.say(T("تعذّر فتح العنوان/المنفذ الجديد فبقي الخادم على السابق.", "The new address/port could not be opened; the server kept the previous one."), BAD)
        else:
            self.say(T("لم يرد الخادم — راجع «سجل الخادم».", "No answer from the server — see “Server log”."), BAD)
        self.root.after(0, self.build)

    def do_autostart(self) -> None:
        T = self.T
        want = self.v_auto.get()
        if not WINDOWS:
            return self.say(T("متاح على ويندوز فقط.", "Windows only."), WARN)
        ok = set_autostart(want)
        if not ok:
            self.root.after(0, lambda: self.v_auto.set(not want))
        self.say((T("✓ سيعمل الخادم مع ويندوز.", "✓ The server starts with Windows.") if want else T("✓ أُلغي التشغيل التلقائي.", "✓ Start-up removed."))
                 if ok else T("يحتاج صلاحيات المسؤول.", "Needs administrator rights."), OK if ok else BAD)

    def do_open(self) -> None:
        from .desktop import open_window
        open_window(f"http://127.0.0.1:{settings().web_port}")

    def do_log(self) -> None:
        f = settings().data_dir / "logs" / "hader.log"
        open_path(str(f if f.exists() else f.parent))

    def do_backup(self) -> None:
        dest = backup_now()
        self.say(self.T(f"✓ نسخة احتياطية: {dest.name}", f"✓ Backup: {dest.name}"), OK)

    def do_firewall(self) -> None:
        r = _run([python_exe(False), "-c", "from hader import runtime; runtime.firewall()"])
        self.say(self.T("✓ فُتحت المنافذ.", "✓ Ports opened.") if r.returncode == 0 and is_admin()
                 else self.T("يحتاج صلاحيات المسؤول.", "Needs administrator rights."), OK if is_admin() else BAD)

    def do_reset(self) -> None:
        r = _run([python_exe(False), str(BASE_DIR / "tools" / "reset_password.py")], timeout=60)
        self.say(self.T("✓ كلمة مرور admin أصبحت admin وسيُطلب تغييرها عند الدخول.", "✓ admin's password is now admin; it must be changed at sign-in.")
                 if r.returncode == 0 else f"✗ {(r.stderr or r.stdout).strip()[-200:]}", OK if r.returncode == 0 else BAD)

    def do_check(self) -> None:
        T = self.T
        self.say(T("جارٍ البحث عن تحديث…", "Checking for updates…"))
        r = U.check()
        new = r["latest"]
        if r["available"]:
            lines = [T(f"متوفر: {new['sha'][:7]} · {new['date']}", f"Available: {new['sha'][:7]} · {new['date']}")]
            lines += ["• " + (m if len(m) <= 110 else m[:107] + "…") for m in (r["changes"] or [new["message"]])[:8]]
            self._upd_text, self._upd_available = "\n".join(lines), True
            self.say(T("✓ يوجد تحديث جديد.", "✓ An update is available."), OK)
        else:
            self._upd_text, self._upd_available = T("لديك أحدث نسخة.", "You have the latest version."), False
            self.say(T("✓ لديك أحدث نسخة.", "✓ You have the latest version."), OK)
        self.root.after(0, self.build)

    def _after_update(self, r: dict) -> None:
        T = self.T
        self._upd_available = False
        self._upd_text = T("✓ ثُبّتت النسخة الجديدة.", "✓ The new version is installed.")
        self.say(T("✓ اكتمل التحديث. يُعاد فتح المدير بالنسخة الجديدة…", "✓ Updated. Reopening the manager with the new version…"), OK)

        def reopen():
            subprocess.Popen([python_exe(), "-m", "hader.manager", "--no-elevate"], cwd=str(BASE_DIR))
            self.root.destroy()
        self.root.after(2500, reopen)

    def do_update(self) -> None:
        T = self.T
        r = U.update_from_github(say=lambda m: self.say(m.split(" / ")[0 if self.lang == "ar" else -1]))
        self._after_update(r)

    def do_zip(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(title=self.T("اختر ملف النسخة الجديدة", "Choose the new version file"),
                                          filetypes=[("ZIP", "*.zip")])
        if not path:
            return

        def go():
            r = U.update_from_zip(path, say=lambda m: self.say(m.split(" / ")[0 if self.lang == "ar" else -1]))
            self._after_update(r)
        self.bg(go)

    def do_token(self) -> None:
        v = self.v_token.get().strip()
        if v and set(v) == {"•"}:
            return self.say(self.T("المفتاح محفوظ مسبقاً.", "The key is already saved."))
        U.save_token(v)
        self.say(self.T("✓ حُفظ المفتاح. اضغط «البحث عن تحديث».", "✓ Key saved. Press “Check for updates”.") if v
                 else self.T("✓ حُذف المفتاح.", "✓ Key removed."), OK)
        self.root.after(0, self.build)

    def do_shortcut(self) -> None:
        """A desktop shortcut that opens this manager."""
        if not WINDOWS:
            return self.say(self.T("متاح على ويندوز فقط.", "Windows only."), WARN)
        name = self.T("مدير خادم حاضر", "Hader Service Manager")
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\" + name + ".lnk');"
              f"$s.TargetPath='{python_exe()}';$s.Arguments='-m hader.manager';$s.WorkingDirectory='{BASE_DIR}';$s.Save()")
        r = _run(["powershell", "-NoProfile", "-Command", ps])
        self.say(self.T("✓ أُضيف الاختصار إلى سطح المكتب.", "✓ Shortcut added to the desktop.") if r.returncode == 0
                 else f"✗ {(r.stderr or '').strip()[-200:]}", OK if r.returncode == 0 else BAD)

    def toggle_lang(self) -> None:
        self.lang = "en" if self.lang == "ar" else "ar"
        try:
            (settings().data_dir / LANG_FILE).write_text(self.lang, encoding="ascii")
        except OSError:
            pass
        self.build()
        self.refresh()

    # ---- status
    def refresh(self) -> None:
        def probe():
            s = settings()
            info = ping(s.web_port, 1)
            self.root.after(0, lambda: self.show(s, info))
        threading.Thread(target=probe, daemon=True).start()

    def show(self, s, info) -> None:
        T = self.T
        try:
            if info:
                self.dot.configure(fg=OK)
                self.state.configure(text=T("الخادم يعمل", "Server running"))
                ip = (lan_addresses() or ["127.0.0.1"])[0]
                lines = [T(f"البرنامج:  http://{ip}:{s.web_port}", f"Program:  http://{ip}:{s.web_port}")]
                if s.portal_port:
                    lines.append(T(f"بوابة الموظف:  http://{ip}:{s.portal_port}", f"Employee portal:  http://{ip}:{s.portal_port}"))
                lines.append(T("الأجهزة:  ", "Terminals:  ") + ", ".join(map(str, s.adms_ports)))
                self.urls.configure(text="\n".join(lines))
            else:
                self.dot.configure(fg=BAD)
                self.state.configure(text=T("الخادم متوقف", "Server stopped"))
                self.urls.configure(text=T("الأجهزة تحتفظ بالحركات حتى يعمل الخادم.", "Terminals keep their punches until the server runs."))
            for b, on in ((self.b_start, not info), (self.b_stop, bool(info)), (self.b_restart, True), (self.b_open, bool(info))):
                b.state(["!disabled"] if on and not self.busy else ["disabled"])
            self.b_apply.state(["disabled"] if self.busy else ["!disabled"])
        except self.tk.TclError:
            return
        if self._timer:
            self.root.after_cancel(self._timer)
        self._timer = self.root.after(4000, self.refresh)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    if WINDOWS and not is_admin() and "--no-elevate" not in sys.argv:
        try:   # ask once for administrator rights (needed to start/stop the service and open ports)
            import ctypes
            params = " ".join(['-m', 'hader.manager', '--no-elevate'])
            if ctypes.windll.shell32.ShellExecuteW(None, "runas", python_exe(), params, str(BASE_DIR), 1) > 32:
                return
        except (OSError, AttributeError):
            pass
    Manager().run()


if __name__ == "__main__":
    main()
