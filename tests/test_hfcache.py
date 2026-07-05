"""Tests for the on-drive HF cache redirect and the Dia DAC codec seeding."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kodo import hfcache
from kodo.voice import dac


@pytest.fixture(autouse=True)
def _clean_hf_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)


def _fake_settings(monkeypatch: pytest.MonkeyPatch, library_root: Path) -> None:
    monkeypatch.setattr("kodo.config.get_settings", lambda: SimpleNamespace(library_root=library_root, hf_token=None))


def test_drive_cache_dir_none_for_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_settings(monkeypatch, Path("data"))  # the relative default sentinel
    assert hfcache.drive_cache_dir() is None


def test_drive_cache_dir_none_for_missing_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_settings(monkeypatch, tmp_path / "not-mounted")
    assert hfcache.drive_cache_dir() is None


def test_drive_cache_dir_for_real_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_settings(monkeypatch, tmp_path)
    assert hfcache.drive_cache_dir() == tmp_path / ".cache" / "huggingface"


def test_configure_sets_hf_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_settings(monkeypatch, tmp_path)
    result = hfcache.configure()
    expected = tmp_path / ".cache" / "huggingface"
    assert result == expected
    import os

    assert os.environ["HF_HOME"] == str(expected)


def test_configure_respects_user_hf_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HF_HOME", "/user/set")
    _fake_settings(monkeypatch, tmp_path)
    assert hfcache.configure() is None


def test_configure_respects_user_hf_hub_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", "/user/set/hub")
    _fake_settings(monkeypatch, tmp_path)
    assert hfcache.configure() is None


def test_configure_noop_without_library(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_settings(monkeypatch, Path("data"))
    assert hfcache.configure() is None


def test_codec_present_reflects_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert not dac.codec_present()
    snap = dac.codec_dir() / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"weights")
    assert dac.codec_present()


def test_seed_codec_calls_snapshot_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_snapshot(repo_id: str, **kwargs: object) -> str:
        captured["repo_id"] = repo_id
        captured["allow_patterns"] = kwargs.get("allow_patterns")
        return str(tmp_path / "snapshot")

    monkeypatch.setattr("huggingface_hub.snapshot_download", _fake_snapshot)
    out = dac.seed_codec()
    assert out == tmp_path / "snapshot"
    assert captured["repo_id"] == dac.DAC_REPO
    assert captured["allow_patterns"] == ["*.safetensors", "*.json"]
