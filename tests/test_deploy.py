import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kerfcorrector import deploy


def test_pip_python_prefers_python3_on_path(monkeypatch):
    # sys.executable is unreliable inside a WSGI worker process (can resolve
    # to the web server's own binary, not a real Python interpreter) -- see
    # deploy.py's own comment. python3 on PATH is what a normal interactive
    # shell uses and is confirmed to work, so it should always win when found.
    monkeypatch.setattr(deploy.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    assert deploy._pip_python() == "/usr/bin/python3"


def test_pip_python_falls_back_to_sys_executable_when_python3_not_on_path(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda name: None)
    monkeypatch.setattr(deploy.sys, "executable", "/some/wsgi/binary")
    assert deploy._pip_python() == "/some/wsgi/binary"
