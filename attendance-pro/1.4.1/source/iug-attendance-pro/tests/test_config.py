"""The database must survive downloading a new version into another folder."""
from hader import config


def test_windows_data_folder_survives_new_versions(tmp_path, monkeypatch):
    old = tmp_path / "iug-attendance-pro-old"
    new = tmp_path / "iug-attendance-pro-main"
    (old / "data").mkdir(parents=True)
    (old / "data" / "hader.db").write_bytes(b"x" * 5000)       # the copy with the terminals
    (new / "data").mkdir(parents=True)
    (new / "data" / "hader.db").write_bytes(b"x" * 100)        # fresh, nearly empty
    monkeypatch.setattr(config, "BASE_DIR", new)
    monkeypatch.setattr(config, "WINDOWS", True)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    d = config._default_data_dir()
    assert d == tmp_path / "ProgramData" / "Hader"
    assert (d / "hader.db").stat().st_size == 5000              # the biggest earlier database was kept
    (d / "hader.db").write_bytes(b"y" * 7000)
    assert config._default_data_dir() == d                     # later runs use it as is
    assert (d / "hader.db").stat().st_size == 7000


def test_data_of_the_previous_name_is_brought_over(tmp_path, monkeypatch):
    old_shared = tmp_path / "ProgramData" / config._OLD_DATA_DIR
    (old_shared / "backups").mkdir(parents=True)
    (old_shared / f"{config._OLD_NAME}.db").write_bytes(b"x" * 9000)
    (old_shared / "backups" / f"{config._OLD_NAME}_20261001_auto.db").write_bytes(b"b")
    new = tmp_path / "app"
    new.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", new)
    monkeypatch.setattr(config, "WINDOWS", True)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    d = config._default_data_dir()
    assert (d / "hader.db").stat().st_size == 9000
    assert (d / "backups" / "hader_20261001_auto.db").exists()
