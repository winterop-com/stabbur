"""Tests for model-card sidecars + Hugging Face card backfill."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from stabbur import cards


def test_has_card_detects_readme_and_sidecar(tmp_path: Path) -> None:
    assert cards.has_card(tmp_path) is False  # empty dir → no card
    (tmp_path / "README.md").write_text("# hi")
    assert cards.has_card(tmp_path) is True  # top-level README
    readme_only = tmp_path / "no-readme"
    readme_only.mkdir()
    cards.write_card(readme_only / cards.SIDECAR_DIR, "# sidecar card")
    assert cards.has_card(readme_only) is True  # written sidecar model-card.md


def test_fetch_hf_readme_returns_text_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    downloaded = tmp_path / "README.md"
    downloaded.write_text("# Model Card\n\nUseful docs.")

    def _fake_download(*, repo_id: str, filename: str, token: str | None = None) -> str:
        assert repo_id == "pub/Model" and filename == "README.md"
        return str(downloaded)

    monkeypatch.setattr("huggingface_hub.hf_hub_download", _fake_download)
    assert cards.fetch_hf_readme("pub/Model") == "# Model Card\n\nUseful docs."


def test_fetch_hf_readme_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_: object) -> str:
        raise OSError("offline / 404 / no README")

    monkeypatch.setattr("huggingface_hub.hf_hub_download", _boom)
    assert cards.fetch_hf_readme("pub/Missing") is None  # never raises — a missing card is not fatal


# --- the sidecar writes are atomic (stabbur.fsatomic) -------------------------------------------


def _fail_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the durable write fail where an unclean eject of the library drive would."""

    def _drive_went_away(_fd: int) -> None:
        raise OSError("drive went away mid-write")

    monkeypatch.setattr(os, "fsync", _drive_went_away)


def test_metadata_write_never_truncates_the_previous_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # metadata.json is what `verify` reads to detect a truncated pull, so it must not be the file
    # a truncated write can produce: it is staged and fsynced before the rename, and a write that
    # fails leaves the last complete version in place rather than a half-written one.
    sidecar = tmp_path / cards.SIDECAR_DIR
    cards.write_metadata(sidecar, {"name": "pub/model", "files": 3})

    _fail_fsync(monkeypatch)
    with pytest.raises(OSError):
        cards.write_metadata(sidecar, {"name": "pub/model", "files": 4})

    assert json.loads((sidecar / "metadata.json").read_text())["files"] == 3
    assert sorted(p.name for p in sidecar.iterdir()) == ["metadata.json"]  # no staging temp left


def test_card_write_never_truncates_the_previous_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # For an Ollama pull the generated card is the only copy of the manifest's system prompt,
    # template and licence — a half-written one loses what the pull already consumed.
    sidecar = tmp_path / cards.SIDECAR_DIR
    cards.write_card(sidecar, "# card\n\nfull text\n")

    _fail_fsync(monkeypatch)
    with pytest.raises(OSError):
        cards.write_card(sidecar, "# card\n\nreplacement\n")

    assert (sidecar / "model-card.md").read_text() == "# card\n\nfull text\n"
    assert sorted(p.name for p in sidecar.iterdir()) == ["model-card.md"]


def test_metadata_serializes_non_json_values_and_creates_the_sidecar(tmp_path: Path) -> None:
    # Routing through fsatomic must not lose `default=str`: a pull records Paths and timestamps.
    sidecar = tmp_path / "deep" / cards.SIDECAR_DIR
    path = cards.write_metadata(sidecar, {"name": "pub/model", "path": tmp_path / "weights.gguf"})
    assert json.loads(path.read_text())["path"] == str(tmp_path / "weights.gguf")
