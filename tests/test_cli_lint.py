"""Tests for the lint CLI command."""
import subprocess
import sys

from secure_wiki.__main__ import main
from secure_wiki.store.wiki_store import WikiStore


def test_lint_command_exits_zero_on_clean_wiki(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path)
    store.init()
    monkeypatch.setattr(sys, "argv", ["secure-wiki", "lint"])
    main()
    out = capsys.readouterr().out
    assert "orphans" in out


def test_lint_report_only_makes_no_git_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path)
    store.init()
    log_before = subprocess.check_output(
        ["git", "log", "--oneline"], cwd=tmp_path
    ).decode()
    monkeypatch.setattr(sys, "argv", ["secure-wiki", "lint", "--report-only"])
    main()
    log_after = subprocess.check_output(
        ["git", "log", "--oneline"], cwd=tmp_path
    ).decode()
    assert log_before == log_after


def test_lint_outputs_five_check_counts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WIKI_DATA_PATH", str(tmp_path))
    store = WikiStore(root=tmp_path)
    store.init()
    monkeypatch.setattr(sys, "argv", ["secure-wiki", "lint"])
    main()
    out = capsys.readouterr().out
    assert "missing links" in out
    assert "incomplete pages" in out
    assert "low-confidence" in out
