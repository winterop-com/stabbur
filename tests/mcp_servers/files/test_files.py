"""Tests for the file server core: listing, reading, searching, the safe_join guard, and writes."""

from pathlib import Path

import pytest

from stabbur.mcp_servers.files.core import FilesSettings, list_dir, read_text, safe_join, search, write_text


def _root(tmp_path: Path) -> FilesSettings:
    (tmp_path / "a.txt").write_text("hello world\nsecond line\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("def foo():\n    return 42  # hello\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hello in vcs")  # must be skipped
    return FilesSettings(root=tmp_path)


def test_safe_join_rejects_escapes(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "sub/b.py") == (tmp_path / "sub" / "b.py").resolve()
    assert safe_join(tmp_path, "") == tmp_path.resolve()
    for bad in ("/etc/passwd", "../secret", "sub/../../secret"):
        with pytest.raises(ValueError):
            safe_join(tmp_path, bad)


def test_list_dir_sorted_and_skips_noise(tmp_path: Path) -> None:
    s = _root(tmp_path)
    entries = list_dir(s)
    kinds = {e.path: e.type for e in entries}
    assert kinds == {"sub": "dir", "a.txt": "file"}  # .git skipped; dir before file
    assert next(e for e in entries if e.path == "a.txt").size > 0


def test_read_text_and_limits(tmp_path: Path) -> None:
    s = _root(tmp_path)
    assert read_text(s, "a.txt").startswith("hello world")
    with pytest.raises(FileNotFoundError):
        read_text(s, "nope.txt")
    # oversize cap
    small = FilesSettings(root=tmp_path, max_read_bytes=5)
    with pytest.raises(ValueError, match="read cap"):
        read_text(small, "a.txt")
    # binary refused
    (tmp_path / "bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError, match="binary"):
        read_text(s, "bin")


def test_search_finds_lines_and_skips_vcs(tmp_path: Path) -> None:
    hits = search(_root(tmp_path), "hello")
    paths = {(m.path, m.line) for m in hits}
    assert ("a.txt", 1) in paths
    assert ("sub/b.py", 2) in paths  # the "# hello" comment
    assert not any(".git" in m.path for m in hits)  # vcs skipped


def test_writes_gated_by_flag(tmp_path: Path) -> None:
    ro = FilesSettings(root=tmp_path)
    with pytest.raises(PermissionError, match="STABBUR_FILES_WRITABLE"):
        write_text(ro, "new.txt", "hi")
    rw = FilesSettings(root=tmp_path, writable=True)
    write_text(rw, "nested/new.txt", "hi")
    assert (tmp_path / "nested" / "new.txt").read_text() == "hi"
    # even with writes on, escapes are still rejected
    with pytest.raises(ValueError):
        write_text(rw, "../escape.txt", "x")


def test_symlink_escaping_root_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "in.txt").write_text("needle inside\n")
    outside = tmp_path / "secret.txt"
    outside.write_text("needle secret\n")
    (root / "escape").symlink_to(outside)  # points out of the root
    s = FilesSettings(root=root)
    listed = {e.path for e in list_dir(s)}  # must not raise on the escaping symlink
    assert "in.txt" in listed and "escape" not in listed
    hits = search(s, "needle")
    assert hits and all("secret" not in m.text for m in hits)  # out-of-root bytes never read
