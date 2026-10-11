"""The updater replaces program files only, and refuses files that are not the program."""
import zipfile

import pytest

from hader import update as U


def _zip(path, files, comment=b""):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
        z.comment = comment
    return path


def test_extract_finds_the_program_and_its_version(tmp_path):
    z = _zip(tmp_path / "a.zip", {"iug-attendance-pro-main/run.py": "", "iug-attendance-pro-main/hader/version.py": ""}, b"abc123")
    root, v = U.extract(z, tmp_path / "x")
    assert root.name == "iug-attendance-pro-main" and v["sha"] == "abc123"
    with pytest.raises(U.UpdateError):
        U.extract(_zip(tmp_path / "b.zip", {"other/readme.txt": "hi"}), tmp_path / "y")
    (tmp_path / "c.zip").write_text("not a zip")
    with pytest.raises(U.UpdateError):
        U.extract(tmp_path / "c.zip", tmp_path / "z")


def test_new_files_replace_the_program_but_never_data_or_environment(tmp_path, monkeypatch):
    base, new = tmp_path / "prog", tmp_path / "new"
    for rel, text in {"run.py": "old", "hader/app.py": "old", "hader/gone.py": "x", "hader.ini": "mine",
                      ".venv/python": "env", "data/hader.db": "db", "docs/keep.md": "k", "tools/cloudflared.exe": "bin"}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text)
    for rel, text in {"run.py": "new", "hader/app.py": "new", "hader/added.py": "a", "hader.ini": "theirs"}.items():
        (new / rel).parent.mkdir(parents=True, exist_ok=True)
        (new / rel).write_text(text)
    monkeypatch.setattr(U, "BASE_DIR", base)
    U.backup_program(tmp_path / "bak")
    U.put_in_place(new)
    read = lambda rel: (base / rel).read_text()  # noqa: E731
    assert read("run.py") == "new" and read("hader/app.py") == "new" and read("hader/added.py") == "a"
    assert not (base / "hader/gone.py").exists()              # stale program file removed
    assert read("hader.ini") == "mine" and read(".venv/python") == "env" and read("data/hader.db") == "db"
    assert read("docs/keep.md") == "k" and read("tools/cloudflared.exe") == "bin"
    U.put_in_place(tmp_path / "bak")                          # rolling back restores the old program
    assert read("run.py") == "old" and (base / "hader/gone.py").exists() and not (base / "hader/added.py").exists()
