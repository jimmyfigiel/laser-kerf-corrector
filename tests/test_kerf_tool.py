import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kerfcorrector import kerf_tool


# ---------------------------------------------------------------------------
# Usage counter: persists completed-correction counts across restarts (a
# single small text file living outside the git-tracked repo, same
# convention as feedback.py's DATA_FILE -- see kerf_tool.py's own comment).
# ---------------------------------------------------------------------------

def _isolate_usage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(kerf_tool, "_USAGE_DATA_DIR", tmp_path)
    monkeypatch.setattr(kerf_tool, "_USAGE_COUNT_FILE", tmp_path / "kerf-corrector-usage-count.txt")


def test_read_usage_count_defaults_to_zero_when_no_file_exists(tmp_path, monkeypatch):
    _isolate_usage_dir(tmp_path, monkeypatch)
    assert kerf_tool._read_usage_count() == 0


def test_increment_usage_count_persists_across_reads(tmp_path, monkeypatch):
    _isolate_usage_dir(tmp_path, monkeypatch)
    assert kerf_tool._increment_usage_count() == 1
    assert kerf_tool._increment_usage_count() == 2
    assert kerf_tool._increment_usage_count() == 3
    # A fresh read (simulating a new request/process) sees the same count --
    # this is the whole point of writing it to disk rather than memory.
    assert kerf_tool._read_usage_count() == 3


def test_increment_usage_count_creates_the_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "does-not-exist-yet"
    monkeypatch.setattr(kerf_tool, "_USAGE_DATA_DIR", data_dir)
    monkeypatch.setattr(kerf_tool, "_USAGE_COUNT_FILE", data_dir / "kerf-corrector-usage-count.txt")
    assert not data_dir.exists()
    kerf_tool._increment_usage_count()
    assert data_dir.exists()


def test_read_usage_count_ignores_a_corrupt_file(tmp_path, monkeypatch):
    _isolate_usage_dir(tmp_path, monkeypatch)
    kerf_tool._USAGE_COUNT_FILE.write_text("not a number", encoding="utf-8")
    assert kerf_tool._read_usage_count() == 0
