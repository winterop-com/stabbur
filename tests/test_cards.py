"""Tests for model-card sidecars + Hugging Face card backfill."""

from __future__ import annotations

from pathlib import Path

import pytest

from kodo import cards


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
