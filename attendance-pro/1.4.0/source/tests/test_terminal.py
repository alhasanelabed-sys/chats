"""Wire format of the 4370 protocol (our implementation) over TCP and UDP."""
import struct
from datetime import datetime

import pytest

from hader.terminal import discovery
from hader.terminal import protocol as P
from hader.terminal.client import TerminalAuthError, TerminalClient
from terminal_simulator import FakeTerminal, serve


def test_time_codec_roundtrip():
    t = datetime(2026, 12, 31, 23, 59, 58)
    import struct
    assert P.decode_time(struct.pack("<I", P.encode_time(t))) == t


def test_checksum_matches_reference_values():
    # CMD_CONNECT exactly as captured from the vendor SDK on a fresh TCP session
    assert P.tcp_wrap(P.build_packet(P.CMD_CONNECT, 0, 65534)) == bytes.fromhex("5050827d08000000e80317fc00000000")


@pytest.mark.parametrize("udp", [False, True])
@pytest.mark.parametrize("user_size", [72, 28])
def test_full_read_and_write(udp, user_size):
    term = FakeTerminal(comm_key=77, user_size=user_size).seed(4, 300)
    port, stop = serve(term)
    try:
        with TerminalClient("127.0.0.1", port, 77, udp=udp, timeout=3) as cli:
            info = cli.device_info()
            assert info["SerialNumber"] == term.sn and info["UserCount"] == "4"
            users = cli.get_users()
            assert [u.user_id for u in users] == ["101", "102", "103", "104"]
            assert len(cli.get_templates()) == 4
            assert len(cli.get_faces(users)) == 4
            punches = cli.get_attendance()
            assert len(punches) == 300 and punches[0].user_id == "101"
            u = cli.save_user("555", "Ali", card=12, users=users)
            cli.save_user_templates(u, [P.DevTemplate(0, 2, 1, b"t" * 512)])
            assert cli.delete_user("101", users)
        assert {u.user_id for u in term.users} == {"102", "103", "104", "555"}
        assert any(t.fid == 2 and t.template == b"t" * 512 for t in term.templates)
    finally:
        stop()


def test_wrong_comm_key():
    term = FakeTerminal(comm_key=5)
    port, stop = serve(term)
    try:
        with pytest.raises(TerminalAuthError):
            TerminalClient("127.0.0.1", port, 6, timeout=2).connect()
    finally:
        stop()


def test_udp_sweep_and_network_parsing():
    term = FakeTerminal()
    port, stop = serve(term)
    try:
        assert discovery.udp_sweep(["127.0.0.1"], port) == {"127.0.0.1": P.CMD_ACK_OK}
    finally:
        stop()
    nets = discovery.parse_networks("10.0.0.0/30, 10.0.1.5, 10.0.2.1-10.0.2.3")
    assert discovery._hosts(nets) == ["10.0.0.1", "10.0.0.2", "10.0.1.5", "10.0.2.1", "10.0.2.2", "10.0.2.3"]
    with pytest.raises(ValueError):
        discovery._hosts(discovery.parse_networks("10.0.0.0/16"))


def test_attlog_layout_chosen_by_valid_records_even_with_a_wrong_count():
    """Someone punches during the read: the announced count is off, the 40-byte layout must
    still win, and no garbage personnel number may come out."""
    t0 = datetime(2026, 10, 5, 8, 0)
    punches = [P.DevPunch(str(1795 + i), t0, 0, 1, uid=i + 1) for i in range(25)]
    data = P.build_attlog(punches)
    for count in (0, 24, 26, 25):
        out = P.parse_attlog(data, count)
        assert [p.user_id for p in out] == [p.user_id for p in punches]
    # damaged records are dropped, the rest kept
    body = bytearray(data)
    body[4 + 40 * 3 + 2:4 + 40 * 3 + 6] = b"\xe2\x94\x8c\x00"
    out = P.parse_attlog(bytes(body), 25)
    assert len(out) == 24 and all(P.valid_pin(p.user_id) for p in out)


def test_users_layout_and_invalid_pins():
    users = [P.DevUser(uid=i, user_id=str(120 + i), name=f"U{i}") for i in range(1, 8)]
    for size in (72, 28):
        out, got = P.parse_users(P.build_users(users, size), 0)
        assert got == size and [u.user_id for u in out] == [u.user_id for u in users]
    assert not P.valid_pin("┌←B3") and not P.valid_pin("") and P.valid_pin("1795")


def test_stream_damage_does_not_shift_the_following_records():
    t0 = datetime(2026, 10, 5, 8, 0)
    punches = [P.DevPunch(str(4200 + i), t0, 0, 15, uid=i + 1) for i in range(300)]
    data = bytearray(P.build_attlog(punches))
    data[4 + 40 * 100:4 + 40 * 100] = b"\x00\xe2\x94"       # 3 stray bytes in the middle of the stream
    data[0:4] = struct.pack("<I", len(data) - 4)
    out = P.parse_attlog(bytes(data), 300)
    assert len(out) >= 299 and out[-1].user_id == "4499"
    assert all(P.valid_pin(p.user_id) for p in out)


def test_arabic_names_decode_cleanly():
    name = "محمد زكريا محمد المدهون"
    for cut in (23, 24, 25):                                 # the field may cut a letter in half
        got = P.cstr(name.encode("utf-8")[:cut])
        assert got and name.startswith(got) and len(name.encode()[:cut]) - len(got.encode()) <= 1
    assert P.cstr("أشرف سامي".encode("cp1256")) == "أشرف سامي"    # older firmware
    assert P.repair_mojibake(name.encode("utf-8").decode("latin-1")) == name
    assert P.repair_mojibake("Ali") == "Ali"


def test_short_arabic_words_in_the_old_code_page():
    for word in ("عبد", "علي", "عبد الرحمن المقادمة"):
        assert P.cstr(word.encode("cp1256")) == word
        assert P.repair_mojibake(word.encode("cp1256").decode("latin-1")) == word
        assert P.repair_mojibake(word.encode("utf-8").decode("latin-1")) == word


def test_adms_body_with_one_old_code_page_line():
    from hader.adms.protocol import decode_body
    body = "USER PIN=1\tName=محمد".encode("utf-8") + b"\n" + "USER PIN=2\tName=عبد".encode("cp1256")
    assert decode_body(body).splitlines() == ["USER PIN=1\tName=محمد", "USER PIN=2\tName=عبد"]


@pytest.mark.parametrize("damaged_index", [299, 298])
def test_damaged_final_records_finish_without_hanging(damaged_index):
    # A child process bounds the regression: the old decoder loops forever here.
    import subprocess
    import sys
    script = f"""
import struct
from datetime import datetime
from hader.terminal import protocol as P
rows = [P.DevPunch(str(4200+i), datetime(2026,10,5,8), 0, 15, uid=i+1) for i in range(300)]
data = bytearray(P.build_attlog(rows))
start = 4 + 40 * {damaged_index} + 27
data[start:start+4] = b'\\xff' * 4
result = P.parse_attlog(bytes(data), 300)
assert len(result) == 299
assert str(4200 + {damaged_index}) not in {{r.user_id for r in result}}
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr


def test_attlog_diagnostics_are_local_and_expose_truncation():
    rows = [P.DevPunch(str(4200 + i), datetime(2026, 10, 5, 8), 0, 15, uid=i + 1) for i in range(30)]
    clean, truncated = {}, {}
    P.parse_attlog(P.build_attlog(rows), 30, diagnostics=clean)
    P.parse_attlog(P.build_attlog(rows)[:-7], 30, diagnostics=truncated)
    assert clean["valid"] == 30 and clean["clean"] is True
    assert truncated["truncated"] is True and truncated["clean"] is False
    assert clean["valid"] == 30  # another parse cannot overwrite the first device's diagnostics
