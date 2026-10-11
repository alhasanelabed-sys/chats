from datetime import datetime

from hader.adms import protocol as P


def test_attlog_parsing_with_temperature():
    body = "1\t2024-05-01 08:01:02\t0\t15\t\t0\t0\n\n7\t2024-05-01 17:00:00\t1\t1\t0\t0\t0\t1\t36.6\nbad line\n"
    recs = P.parse_attlog(body)
    assert [r.pin for r in recs] == ["1", "7"]
    assert recs[0].time == datetime(2024, 5, 1, 8, 1, 2) and recs[0].verify == 15
    assert recs[1].state == 1 and recs[1].temperature == 36.6 and recs[1].mask == 1


def test_operlog_mixed_lines():
    body = ("USER PIN=5\tName=علي أحمد\tPri=14\tPasswd=123\tCard=99\tGrp=1\tTZ=0\tVerify=0\n"
            "FP PIN=5\tFID=6\tSize=4\tValid=1\tTMP=QUJD==\n"
            "BIODATA Pin=5\tNo=0\tIndex=0\tValid=1\tDuress=0\tType=9\tMajorVer=39\tMinorVer=1\tFormat=0\tTmp=eHh4=\n"
            "OPLOG 4\t0\t2024-05-01 10:00:00\t0\t0\t0\t0\n")
    items = P.parse_operlog(body)
    assert [i.kind for i in items] == ["USER", "FP", "BIODATA", "OPLOG"]
    assert items[0].data["name"] == "علي أحمد" and items[0].data["pri"] == "14"
    assert items[1].data["tmp"] == "QUJD=="          # '=' padding survives
    assert items[2].data["type"] == "9" and items[2].data["majorver"] == "39"
    assert items[3].fields[2] == "2024-05-01 10:00:00"


def test_options_and_info():
    opts = P.parse_options("~DeviceName=SpeedFace-V5L,MAC=00:17:61:aa:bb:cc,FWVersion=Ver 8.0.4.2,"
                           "MultiBioDataSupport=0:1:0:0:0:0:0:0:1:1")
    assert opts["DeviceName"] == "SpeedFace-V5L" and opts["MultiBioDataSupport"].count(":") == 9
    info = P.parse_info_param("Ver 8.0.4.2,12,20,300,10.0.0.5,10,7,0,12,101")
    assert info["user_count"] == "12" and info["ip"] == "10.0.0.5"


def test_devicecmd_with_info_lines():
    rets = P.parse_devicecmd("ID=3&Return=0&CMD=DATA\nID=4&Return=0&CMD=INFO\n~DeviceName=X\nUserCount=5\n"
                             "ID=5&Return=-1002&CMD=DATA")
    assert [(r.id, r.ret) for r in rets] == [(3, 0), (4, 0), (5, -1002)]
    assert rets[1].extra == {"DeviceName": "X", "UserCount": "5"}


def test_time_encoding_roundtrip():
    dt = datetime(2026, 9, 28, 13, 45, 7)
    assert P.dev_decode_time(P.dev_encode_time(dt)) == dt


def test_attphoto():
    meta, data = P.parse_attphoto(b"PIN=20240501080102-7.jpg\nSN=ABC\nsize=4\nCMD=uploadphoto\x00\xff\xd8\x00\x01")
    assert meta["pin"] == "20240501080102-7.jpg" and data == b"\xff\xd8\x00\x01"


def test_option_block():
    text = P.option_block("SN1", att_stamp="99", op_stamp="0", photo_stamp="0", time_zone=3, delay=10,
                          trans_interval=1, trans_times="00:00;14:05", realtime=True, upload_photos=True,
                          server_ver="2.4.1")
    assert text.startswith("GET OPTION FROM: SN1\n") and "ATTLOGStamp=99" in text and "TimeZone=3" in text


def test_prefixless_table_keeps_user_and_fingerprint_kind_in_mixed_body():
    users = P.parse_bio_lines("USER PIN=11\tName=First\nPIN=12\tName=Second", "USERINFO")
    assert [(r.kind, r.data["pin"]) for r in users] == [("USER", "11"), ("USER", "12")]
    fingers = P.parse_bio_lines("PIN=12\tFID=6\tValid=1\tTMP=QUFB", "FINGERTMP")
    assert fingers[0].kind == "FP" and fingers[0].data["fid"] == "6"
