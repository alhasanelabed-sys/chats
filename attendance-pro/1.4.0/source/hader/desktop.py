"""Open the existing local UI in a Windows browser application window."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import webbrowser


def open_window(url: str) -> bool:
    if os.name == "nt":
        roots = [os.environ.get(name, "") for name in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
        for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            for root in roots:
                if not root:
                    continue
                executable = Path(root) / relative
                if executable.is_file():
                    try:
                        subprocess.Popen([str(executable), "--app=" + url], close_fds=True)
                        return True
                    except OSError:
                        continue
    return bool(webbrowser.open(url))
