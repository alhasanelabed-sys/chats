"""Presence must follow contact, and reconnect must recover pre-contact punches."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from hader import models as m, store, tcp_pull
from hader.adms import server, sync
from hader.api import devices
from hader.db import session_scope
from hader import device_presence


def _rows(client):
    return client.get("/api/devices").json()["rows"]


def test_read_only_poll_terminal_remains_online_between_checks(client):
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
        db.add(m.Device(sn="READ-POLL", tcp_poll=True, managed_by=None,
                        last_activity=devices.now() - timedelta(seconds=150)))
    row = _rows(client)[0]
    assert row["state"] == "online"
    assert row["read_only"] is True
    assert row["status_detail"]["expires_after_seconds"] >= 180
    assert row["last_contact"] and row["last_upload"] is None


def test_empty_heartbeat_publishes_committed_contact_without_a_punch(client):
    from hader.events import current, since
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
        db.add(m.Device(sn="HEARTBEAT", last_activity=None))
    seq = current()
    response = client.get("/iclock/getrequest", params={"SN": "HEARTBEAT"})
    assert response.status_code == 200
    row = _rows(client)[0]
    assert row["state"] == "online" and row["last_sync"] is None
    assert any(kind == "device" and data["sn"] == "HEARTBEAT" for _, kind, data in since(seq)[1])
    assert client.get("/api/monitor").json()["total"] == 0


def test_first_adms_contact_requests_all_today_read_only_and_deduplicates(client, device_factory, monkeypatch):
    clock = [datetime(2026, 10, 11, 12, 0)]
    for module in (server, sync, devices, tcp_pull):
        monkeypatch.setattr(module, "now", lambda: clock[0])
    try:
        from hader import device_presence
        monkeypatch.setattr(device_presence, "now", lambda: clock[0])
    except ImportError:
        pass  # the baseline must fail on the missing history, before the fix exists
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
    simulator = device_factory(sn="HISTORY")
    simulator.attlog = [("101", datetime(2026, 10, 10, 23, 59, 59), 0, 1),
                        ("101", datetime(2026, 10, 11, 0, 0), 0, 1),
                        ("101", datetime(2026, 10, 11, 8, 0), 0, 1)]
    simulator.handshake()
    simulator.drain()
    monitor = client.get("/api/monitor").json()
    assert monitor["total"] == 2
    assert {r["punch_time"] for r in monitor["rows"]} == {"2026-10-11 00:00:00", "2026-10-11 08:00:00"}
    queries = [cmd for cmd in simulator.executed if cmd.startswith("DATA QUERY ATTLOG ")]
    assert len(queries) == 1 and "StartTime=2026-10-11 00:00:00" in queries[0]
    assert all(not sync.C.changes_device(cmd) for cmd in simulator.executed)
    simulator.drain()
    assert len([cmd for cmd in simulator.executed if cmd.startswith("DATA QUERY ATTLOG ")]) == 1
    # A punch kept during disconnection must be recovered without a fresh punch.
    simulator.attlog.append(("101", datetime(2026, 10, 11, 9, 0), 1, 1))
    clock[0] += timedelta(minutes=10)
    simulator.drain()
    assert client.get("/api/monitor").json()["total"] == 3
    assert len([cmd for cmd in simulator.executed if cmd.startswith("DATA QUERY ATTLOG ")]) == 2


def test_direct_reconnect_rechecks_unchanged_record_count(client, monkeypatch):
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
        db.add(m.Device(sn="RECOVER", ip="127.0.0.1", tcp_poll=True, managed_by="tcp",
                        last_activity=tcp_pull.now() - timedelta(hours=1), last_sync=tcp_pull.now(), att_count=2))
    class ReadOnlyCounters:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            return self
        def counters(self):
            return {"TransactionCount": "2"}
        def disconnect(self):
            pass
    monkeypatch.setattr(tcp_pull, "TerminalClient", ReadOnlyCounters)
    reads = []
    monkeypatch.setattr(tcp_pull, "read_and_store", lambda sn, **kwargs: reads.append(sn) or {"read": 2, "new": 1})
    tcp_pull._poll_one("RECOVER", True, False, False, False)
    assert reads == ["RECOVER"]


def test_busy_direct_socket_preserves_contact_without_claiming_new_contact(client):
    with session_scope() as db:
        stamp = tcp_pull.now() - timedelta(seconds=20)
        db.add(m.Device(sn="BUSY", ip="127.0.0.1", tcp_poll=True, last_activity=stamp))
    lock = tcp_pull._lock("BUSY")
    lock.acquire()
    try:
        assert tcp_pull.quick_status("BUSY")["busy"] is True
    finally:
        lock.release()
    row = _rows(client)[0]
    assert row["state"] == "online"
    with session_scope() as db:
        assert db.scalar(select(m.Device).where(m.Device.sn == "BUSY")).last_activity == stamp


def test_read_only_queries_are_not_starved_by_pending_writes(client):
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
        db.add(m.Device(sn="BACKLOG"))
        for pin in range(80):
            sync.queue(db, "BACKLOG", f"DATA UPDATE USERINFO PIN={pin}", "Pending write")
        sync.queue(db, "BACKLOG", "INFO", "Allowed read")
    response = client.get("/iclock/getrequest", params={"SN": "BACKLOG"})
    assert response.status_code == 200
    assert ":INFO\n" in response.text and "DATA QUERY ATTLOG " in response.text
    assert "DATA UPDATE" not in response.text
    with session_scope() as db:
        assert len(db.scalars(select(m.DeviceCommand).where(
            m.DeviceCommand.device_sn == "BACKLOG", m.DeviceCommand.status == "pending")).all()) == 80


def test_history_retry_budget_is_bounded_and_resets_at_midnight(client, monkeypatch):
    clock = [datetime(2026, 10, 11, 12, 0)]
    monkeypatch.setattr(device_presence, "now", lambda: clock[0])
    monkeypatch.setattr(sync, "now", lambda: clock[0])
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
        db.add(m.Device(sn="NO-ANSWER"))
    for attempt in range(5):
        with session_scope() as db:
            d = db.scalar(select(m.Device).where(m.Device.sn == "NO-ANSWER"))
            device_presence.touch(db, d)
            device_presence.request_today(db, d)
            sync.next_commands(db, d.sn)
        clock[0] += timedelta(minutes=6)
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == "NO-ANSWER"))
        commands = db.scalars(select(m.DeviceCommand).where(m.DeviceCommand.device_sn == d.sn)).all()
        assert len(commands) == 3
        assert all(not sync.C.changes_device(command.content) for command in commands)
        assert device_presence.reconciliation(d)["status"] == "failed"
    clock[0] = datetime(2026, 10, 12, 0, 1)
    with session_scope() as db:
        d = db.scalar(select(m.Device).where(m.Device.sn == "NO-ANSWER"))
        device_presence.touch(db, d)
        device_presence.request_today(db, d)
        history = device_presence.reconciliation(d)
        assert history["date"] == "2026-10-12" and history["attempts"] == 1
        assert history["start"] == "2026-10-12 00:00:00"


def test_contact_expiry_is_independent_of_read_only_permission(client):
    with session_scope() as db:
        db.add(m.Device(sn="CONTACT-EXPIRED", tcp_poll=True, last_activity=device_presence.now() - timedelta(minutes=4)))
        store.set_(db, "tcp.write_back", False)
    assert _rows(client)[0]["state"] == "offline"
    with session_scope() as db:
        store.set_(db, "tcp.write_back", True)
    assert _rows(client)[0]["state"] == "offline"


def test_counter_command_failure_after_verified_connect_is_still_contact(client, monkeypatch):
    from hader.terminal.client import TerminalError
    with session_scope() as db:
        db.add(m.Device(sn="READ-ERROR", ip="127.0.0.1", tcp_poll=True))
    class UnsupportedCounters:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            return self
        def counters(self):
            raise TerminalError("counter query not supported")
        def disconnect(self):
            pass
    monkeypatch.setattr(tcp_pull, "TerminalClient", UnsupportedCounters)
    result = tcp_pull.quick_status("READ-ERROR")
    assert result["online"] is True and "not supported" in result["error"]
    assert _rows(client)[0]["state"] == "online"


def test_failed_full_read_remains_due_for_next_poll(client, monkeypatch):
    with session_scope() as db:
        db.add(m.Device(sn="RETRY-READ", ip="127.0.0.1", tcp_poll=True))
    monkeypatch.setattr(tcp_pull, "_poll_one", lambda *args: False)
    state = {}
    tcp_pull.poll_due(state)
    assert ("tcp", "RETRY-READ") not in state
    monkeypatch.setattr(tcp_pull, "_poll_one", lambda *args: True)
    tcp_pull.poll_due(state)
    assert ("tcp", "RETRY-READ") in state


def test_partial_direct_log_is_not_reported_as_a_complete_history_read(client):
    with session_scope() as db:
        db.add(m.Device(sn="PARTIAL", ip="127.0.0.1", tcp_poll=True))
    result = tcp_pull.store_read("PARTIAL", tcp_pull.DeviceRead(attendance_complete=False,
                                                             attendance_raw_records=2,
                                                             attendance_rejected_records=2))
    assert result["attendance_complete"] is False
    row = _rows(client)[0]
    assert row["state"] == "online"
    assert row["day_reconciliation"]["status"] == "partial"
    assert row["day_reconciliation"]["raw_records"] == 2
    assert row["day_reconciliation"]["rejected_records"] == 2
    assert "incomplete" in row["day_reconciliation"]["error"]


def test_today_repair_has_priority_over_older_user_queries(client):
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
        db.add(m.Device(sn="READ-BACKLOG"))
        for pin in range(80):
            sync.queue(db, "READ-BACKLOG", sync.C.query_table("user", str(pin)))
    reply = client.get("/iclock/getrequest", params={"SN": "READ-BACKLOG"})
    assert reply.status_code == 200
    assert "DATA QUERY ATTLOG " in reply.text.splitlines()[0]


def test_known_partial_log_is_not_downloaded_again_on_every_minute(client, monkeypatch):
    with session_scope() as db:
        store.set_(db, "tcp.write_back", False)
        db.add(m.Device(sn="KNOWN-PARTIAL", ip="127.0.0.1", tcp_poll=True, managed_by="tcp"))
    tcp_pull.store_read("KNOWN-PARTIAL", tcp_pull.DeviceRead(info={"TransactionCount": "2"},
                       attendance_complete=False, attendance_raw_records=2, attendance_rejected_records=2))
    class SameCounters:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            return self
        def counters(self):
            return {"TransactionCount": "2"}
        def disconnect(self):
            pass
    monkeypatch.setattr(tcp_pull, "TerminalClient", SameCounters)
    reads = []
    monkeypatch.setattr(tcp_pull, "read_and_store", lambda *args, **kwargs: reads.append(args))
    tcp_pull._poll_one("KNOWN-PARTIAL", True, False, False, False)
    assert reads == []
    tcp_pull._poll_one("KNOWN-PARTIAL", True, False, False, True)
    assert len(reads) == 1


def test_decoder_rejections_preserve_diagnostic_and_history_uncertainty(client, monkeypatch):
    from hader.terminal import protocol as P
    class PartialTerminal:
        sizes = None
        last_attlog_stats = {"records": 2, "valid": 1, "size": 40}
        last_attlog_raw = b"raw terminal table with one undecodable record"
        def device_info(self):
            return {"TransactionCount": "2"}
        def get_users(self):
            return []
        def get_attendance(self):
            return [P.DevPunch("101", tcp_pull.now(), 0, 1)]
        def disconnect(self):
            pass
    diagnostics = []
    monkeypatch.setattr(tcp_pull, "_client", lambda *args: PartialTerminal())
    monkeypatch.setattr(tcp_pull, "_dump_raw", lambda *args: diagnostics.append(args))
    data = tcp_pull.read_device("127.0.0.1", with_users=False, with_bio=False)
    assert data.attendance_complete is False
    assert (data.attendance_raw_records, data.attendance_rejected_records) == (2, 1)
    assert diagnostics[0][2] == PartialTerminal.last_attlog_raw
    with session_scope() as db:
        db.add(m.Device(sn="DECODE-PARTIAL", ip="127.0.0.1", tcp_poll=True))
    result = tcp_pull.store_read("DECODE-PARTIAL", data)
    assert (result["read"], result["new"], result["raw_records"], result["rejected_records"]) == (1, 1, 2, 1)
    assert _rows(client)[0]["day_reconciliation"]["status"] == "partial"


@pytest.mark.parametrize("damage", ["truncated", "extra_bytes"])
def test_actual_decoder_clean_counts_do_not_hide_an_incomplete_transfer(client, monkeypatch, damage):
    from hader.terminal import protocol as P
    full = P.build_attlog([P.DevPunch("101", tcp_pull.now() + timedelta(minutes=i), 0, 1, uid=1)
                          for i in range(30)])
    raw = full[:-7] if damage == "truncated" else full + b"garbage"
    class UncleanTransfer:
        sizes = None
        last_attlog_raw = raw
        last_attlog_stats = {}
        def device_info(self):
            return {"TransactionCount": "30"}
        def get_users(self):
            return []
        def get_attendance(self):
            return P.parse_attlog(raw, count=30, diagnostics=self.last_attlog_stats)
        def disconnect(self):
            pass
    term = UncleanTransfer()
    diagnostics = []
    monkeypatch.setattr(tcp_pull, "_client", lambda *args: term)
    monkeypatch.setattr(tcp_pull, "_dump_raw", lambda *args: diagnostics.append(args))
    data = tcp_pull.read_device("127.0.0.1", with_users=False, with_bio=False)
    assert term.last_attlog_stats["valid"] == term.last_attlog_stats["records"]
    assert data.attendance_rejected_records == 0  # missing fragments are not fabricated rejected rows
    assert data.attendance_complete is False and data.attendance_clean is False
    assert data.attendance_truncated is (damage == "truncated")
    assert diagnostics[0][2] == raw
    with session_scope() as db:
        db.add(m.Device(sn="TRANSFER-PARTIAL", ip="127.0.0.1", tcp_poll=True))
    tcp_pull.store_read("TRANSFER-PARTIAL", data)
    history = _rows(client)[0]["day_reconciliation"]
    assert history["status"] == "partial" and history["rejected_records"] == 0
    assert history["transfer_truncated"] is (damage == "truncated")
