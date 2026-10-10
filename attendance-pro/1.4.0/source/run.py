"""Start Hader.

    python run.py                   # web on 8090, employee portal on 8091, devices (ADMS) on 8081 and 90
    python run.py --adms 90         # the port your terminals push to
    python run.py --web 80 --adms 8081,90

The address and ports can also be chosen inside the program (System settings > Server
address and ports); the server then restarts itself on the new ones.

Every web/device port serves the same application (web UI, API and /iclock ADMS), so a
terminal may also be pointed at the web port. The portal port serves the employee portal
only. A device port that cannot be opened (in use, or needs administrator rights) is
skipped with a warning and taken as soon as it is free.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys

import uvicorn


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen(2048)
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def _close(sock) -> None:
    try:
        sock.close()
    except OSError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Hader server")
    ap.add_argument("--host", help="listen address (default 0.0.0.0)")
    ap.add_argument("--web", type=int, help="web UI port (default 8090)")
    ap.add_argument("--adms", help="ADMS port(s) for terminals, comma separated (default 8081,90)")
    ap.add_argument("--data", help="data folder (database, photos, backups)")
    ap.add_argument("--portal", type=int, help="employee-portal-only port (default 8091, 0 = off)")
    ap.add_argument("--open", action="store_true", help="open the program in the browser once it is running")
    ap.add_argument("--desktop", action="store_true", help="open a Windows Edge/Chrome application window")
    args = ap.parse_args()
    if args.host:
        os.environ["HADER_HOST"] = args.host
    if args.web:
        os.environ["HADER_WEB_PORT"] = str(args.web)
    if args.adms:
        os.environ["HADER_ADMS_PORTS"] = args.adms
    if args.data:
        os.environ["HADER_DATA_DIR"] = args.data
    if args.portal is not None:
        os.environ["HADER_PORTAL_PORT"] = str(args.portal)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from hader.config import settings
    _log_to_file(settings.data_dir / "logs")
    from hader import runtime
    # A restore is staged by the API and applied only while no server sockets exist.
    # This keeps a live request from observing a half-installed database or photos tree.
    _restore_offline(runtime)
    from hader.app import app

    pid = runtime.pid_file()
    try:
        pid.write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass
    try:
        _run(args, settings, runtime, app)
    finally:
        try:
            if pid.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid.unlink()
        except OSError:
            pass


def _restore_offline(runtime) -> None:
    from hader import backup
    pending = backup.apply_pending_restore()
    if pending:
        if not pending.get("ok"):
            logging.getLogger("hader").error("pending restore failed: %s", pending.get("error", "unknown"))
        if pending.get("recovery_required"):
            raise RuntimeError("Restore recovery is incomplete; server startup stopped to preserve the database. "
                               + str(pending.get("error", "")))
        runtime.reload_settings()


def _log_to_file(folder) -> None:
    """Keep a log in the data folder (the server usually runs without a window)."""
    from logging.handlers import RotatingFileHandler
    try:
        folder.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(folder / "hader.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    except OSError:
        return
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(h)


def _run(args, settings, runtime, app) -> None:
    log = logging.getLogger("hader")
    desktop = getattr(args, "desktop", False)
    first = True
    while True:
        try:
            web_sock = _bind(settings.host, settings.web_port)
        except OSError as exc:
            if first:
                if args.open or desktop:
                    from hader.manager import ping
                    if ping(settings.web_port):
                        import webbrowser
                        from hader.desktop import open_window
                        (open_window if desktop else webbrowser.open)(f"http://127.0.0.1:{settings.web_port}")
                        return
                print(f"\n  Cannot open web port {settings.web_port} on {settings.host}: {exc}\n  Another program (another "
                      f"attendance server, IIS...) may be using it. Use: run.py --web <other port>\n", file=sys.stderr)
                sys.exit(1)
            # the address/port chosen in the program cannot be opened: go back to the previous ones
            log.error("cannot open %s:%s (%s) - keeping the previous address and ports", settings.host, settings.web_port, exc)
            runtime.restore_previous(f"{settings.host}:{settings.web_port} — {exc}")
            continue
        runtime.applied()
        log.info("listening on %s: web %s, portal %s, devices %s", settings.host, settings.web_port,
                 settings.portal_port or "-", ",".join(map(str, settings.adms_ports)))
        if first:
            runtime.firewall()
        if desktop:
            asyncio.run(serve(app, settings, web_sock, open_browser=first, desktop=True))
        else:
            asyncio.run(serve(app, settings, web_sock, open_browser=args.open and first))
        first = False
        if runtime.STOP.is_set() or not runtime.RESTART.is_set():
            logging.getLogger("hader").info("server stopped")
            break
        runtime.RESTART.clear()
        from hader.db import engine as _db_engine
        _db_engine.dispose()
        _restore_offline(runtime)
        runtime.reload_settings()
        from hader.db import reload_durability
        reload_durability()
        log.info("restarting on %s, web %s, portal %s, devices %s", settings.host, settings.web_port,
                 settings.portal_port, settings.adms_ports)
        runtime.firewall()


async def serve(app, settings, web_sock, open_browser: bool = False, desktop: bool = False) -> None:
    from hader import portal_gate, runtime
    from hader.adms import ports as port_status
    from hader.version import APP_NAME, VERSION

    log = logging.getLogger("hader")
    port_status.PORTS.clear()
    port_status.set_mode(settings.web_port, "own", "web + devices")
    socks: list[tuple[int, object]] = []
    waiting: list[int] = []
    for port in settings.adms_ports:
        if port in (settings.web_port, settings.portal_port):
            continue
        try:
            socks.append((port, _bind(settings.host, port)))
            port_status.set_mode(port, "own")
        except OSError as exc:
            # e.g. the previous server holds port 90: keep trying, take it the moment it is released.
            waiting.append(port)
            port_status.set_mode(port, "waiting", str(exc))
            print(f"  ! Port {port} is used by another program (another attendance server?). "
                  f"It will be taken automatically when released.\n"
                  f"    To free it: run free_port90.bat as administrator. Meanwhile: Devices > Search devices.",
                  file=sys.stderr)
    portal_sock = None
    portal_gate.LISTENING = False
    if settings.portal_port and settings.portal_port != settings.web_port and settings.portal_port not in settings.adms_ports:
        try:
            portal_sock = _bind(settings.host, settings.portal_port)
            portal_gate.LISTENING = True
        except OSError as exc:
            print(f"  ! Employee portal port {settings.portal_port} cannot be opened ({exc}); "
                  f"the portal stays available at /me on the web port.", file=sys.stderr)

    shown = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    print(f"\n  {APP_NAME} {VERSION}")
    first_password = settings.data_dir / "initial_admin_password.txt"
    first_login = f"admin / كلمة المرور في {first_password}" if first_password.exists() else "admin / كلمة المرور التي ضبطها المسؤول"
    print(f"  Web interface  : http://{shown}:{settings.web_port}   (first login: {first_login}; change it immediately)")
    if portal_sock is not None:
        print(f"  Employee portal: http://<this PC's IP>:{settings.portal_port}   (portal only - the port to open to the internet)")
    for port, _ in socks:
        print(f"  Devices (ADMS) : port {port}  -> on the terminal: Cloud Server = this PC's IP, port {port}")
    print(f"  Data folder    : {settings.data_dir}\n")

    def config(lifespan: str) -> uvicorn.Config:
        return uvicorn.Config(app, log_level="warning", access_log=False, lifespan=lifespan, timeout_keep_alive=65)

    async def serve_port(port: int, sock=None):
        """Serve a device port; give it back when released, take it again when free."""
        while not runtime.RESTART.is_set():
            if sock is None:
                if port in port_status.RELEASED:
                    port_status.set_mode(port, "released", "given back (for another program)")
                    await asyncio.sleep(1)
                    continue
                try:
                    sock = _bind(settings.host, port)
                except OSError as exc:
                    port_status.set_mode(port, "waiting", str(exc))
                    for _ in range(10):
                        if runtime.RESTART.is_set():
                            break
                        await asyncio.sleep(1)
                    continue
                log.info("port %d is free now: devices are served here (full mode)", port)
            port_status.set_mode(port, "own")
            srv = uvicorn.Server(config("off"))
            port_status.SERVERS[port] = srv
            await srv.serve(sockets=[sock])
            port_status.SERVERS.pop(port, None)
            _close(sock)
            sock = None
            if port not in port_status.RELEASED:
                return  # shutdown or restart
            log.info("port %d released (read-only mode)", port)

    web = uvicorn.Server(config("on"))
    extra = [uvicorn.Server(config("off"))] if portal_sock is not None else []
    runtime.SERVERS[:] = [web, *extra]
    if open_browser:
        import threading
        from hader.desktop import open_window
        import webbrowser
        launch = open_window if desktop else webbrowser.open
        threading.Timer(2.0, lambda: launch(f"http://127.0.0.1:{settings.web_port}")).start()
    try:
        await asyncio.gather(web.serve(sockets=[web_sock]),
                             *(serve_port(p, s) for p, s in socks),
                             *(serve_port(p) for p in waiting),
                             *(x.serve(sockets=[portal_sock]) for x in extra))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        runtime.SERVERS.clear()
        for sock in [web_sock, portal_sock, *(s for _p, s in socks)]:
            if sock is not None:
                _close(sock)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
