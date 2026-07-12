"""Tests for the write-enabled DHIS2 template + the generic ``extra_secret`` bind plumbing.

Wave 2 (Round 3) adds a second, optional secret to a bind mode: ``BindMode.extra_secret_env`` (a
generic secondary env var kodo exports but never interprets) and ``BindRequest.extra_secret`` (the
value the caller hands it). The ``dhis2-write`` template wires the session mode's
``extra_secret_env`` to ``DHIS2_SESSION_XSRF`` so a session bind can carry the CSRF token DHIS2
requires on writes into the stored d2w profile — the only place the DHIS2 name appears.
"""

from __future__ import annotations

import sys
import time
import tomllib
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from kodo import project
from kodo.app import create_app
from kodo.config import Settings
from kodo.project import AssistantInfo, BindMode
from kodo.project.templates import TEMPLATES


@pytest.fixture
def app() -> FastAPI:
    """App with a clean (no model loaded) manager."""
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    """Async client running the app's lifespan."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# --- templates ------------------------------------------------------------------------------


def test_dhis2_write_template_session_carries_xsrf_extra_secret_env() -> None:
    # The write template's session bind mode declares the CSRF-token env; the pat mode does not.
    tmpl = TEMPLATES["dhis2-write"]
    assert tmpl.assistant is not None
    info = AssistantInfo.model_validate(tmpl.assistant)
    assert info.bind is not None
    assert info.bind.modes["session"].extra_secret_env == "DHIS2_SESSION_XSRF"
    assert info.bind.modes["pat"].extra_secret_env is None
    assert info.readonly is False


def test_dhis2_write_bridge_is_read_write_and_readonly_template_unchanged() -> None:
    # The write template's bridge runs read-write (no DHIS2_MCP_READONLY); the read-only dhis2
    # template still pins DHIS2_MCP_READONLY=1 and stays readonly=True. Both share _dhis2_assistant,
    # so the session mode's extra_secret_env is present on both (generic + harmless), but the
    # read-only bridge never actually writes.
    write = TEMPLATES["dhis2-write"]
    assert write.mcp == [("dhis2", "env DHIS2_PROFILE=local_basic uvx dhis2w-mcp-bridge")]
    assert all("DHIS2_MCP_READONLY" not in cmd for _, cmd in write.mcp)

    read = TEMPLATES["dhis2"]
    assert read.mcp == [("dhis2", "env DHIS2_PROFILE=play42 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge")]
    assert read.assistant is not None
    read_info = AssistantInfo.model_validate(read.assistant)
    assert read_info.readonly is True


# --- round-trip -----------------------------------------------------------------------------


def test_extra_secret_env_survives_render_load_round_trip(tmp_path: Path) -> None:
    # A bind mode carrying extra_secret_env round-trips through the single writer -> single parser.
    info = AssistantInfo.model_validate(
        {
            "name": "local_basic",
            "base_url": "http://localhost:8080",
            "bind": {
                "modes": {
                    "session": {
                        "command": ["d2w", "profile", "add", "local_basic", "--url", "{base_url}"],
                        "secret_env": "DHIS2_SESSION_COOKIE",
                        "extra_secret_env": "DHIS2_SESSION_XSRF",
                    },
                    "pat": {"command": ["d2w", "profile", "add", "local_basic"], "secret_env": "DHIS2_PAT"},
                }
            },
        }
    )
    text = project.render_manifest(model="m", assistant=info)
    tomllib.loads(text)  # valid TOML
    p = tmp_path / "kodo.toml"
    p.write_text(text)
    loaded = project.load(p)
    assert loaded is not None and loaded.assistant is not None
    assert loaded.assistant == info  # full closed round-trip
    assert loaded.assistant.bind is not None
    assert loaded.assistant.bind.modes["session"].extra_secret_env == "DHIS2_SESSION_XSRF"
    # A mode without the field round-trips as None (exclude_none keeps it out of the TOML).
    assert loaded.assistant.bind.modes["pat"].extra_secret_env is None
    assert "extra_secret_env" not in text.split("[assistant.bind.modes.pat]", 1)[1]


def test_dhis2_write_template_scaffolds_and_round_trips(tmp_path: Path) -> None:
    # Scaffolding the write template (validate -> render, as _write_project does) writes a kodo.toml
    # whose load() round-trips the session mode's DHIS2_SESSION_XSRF extra secret.
    tmpl = TEMPLATES["dhis2-write"]
    assert tmpl.assistant is not None
    info = AssistantInfo.model_validate(tmpl.assistant)
    p = tmp_path / "kodo.toml"
    p.write_text(project.render_manifest(model=tmpl.model, system_prompt=tmpl.system_prompt, assistant=info))
    loaded = project.load(p)
    assert loaded is not None and loaded.assistant is not None and loaded.assistant.bind is not None
    assert loaded.assistant.bind.modes["session"].extra_secret_env == "DHIS2_SESSION_XSRF"


# --- validator ------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "1BAD", "has-dash", "has space"])
def test_extra_secret_env_rejects_bad_names(bad: str) -> None:
    # extra_secret_env is validated exactly like secret_env: a valid identifier (letters/digits/_,
    # not starting with a digit), never a loader-controlling variable (PATH/LD_*/DYLD_*).
    with pytest.raises(ValidationError):
        BindMode(command=["tool"], secret_env="COOKIE", extra_secret_env=bad)


def test_extra_secret_env_must_differ_from_secret_env() -> None:
    # Reusing one env name for both secrets would silently overwrite the primary secret with the
    # extra one in the child env (they are exported in order), so it is rejected at validation time.
    with pytest.raises(ValidationError):
        BindMode(command=["tool"], secret_env="COOKIE", extra_secret_env="COOKIE")


def test_extra_secret_env_accepts_valid_name_and_defaults_none() -> None:
    assert BindMode(command=["tool"], secret_env="COOKIE").extra_secret_env is None
    assert (
        BindMode(command=["tool"], secret_env="COOKIE", extra_secret_env="XSRF_TOKEN").extra_secret_env == "XSRF_TOKEN"
    )


# --- bind endpoint --------------------------------------------------------------------------


def _capture_env_code() -> str:
    """Child that writes both secret env vars (and echoes them so redaction is exercised)."""
    return (
        "import os,sys\n"
        "cookie=os.environ.get('DHIS2_SESSION_COOKIE','')\n"
        "xsrf=os.environ.get('DHIS2_SESSION_XSRF','<unset>')\n"
        "open(sys.argv[1],'w').write(cookie+'|'+xsrf)\n"
        "print('leaked',cookie,xsrf)\n"
    )


async def test_bind_session_mode_sets_both_secrets_and_redacts(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    # A session bind with extra_secret sets BOTH DHIS2_SESSION_COOKIE and DHIS2_SESSION_XSRF in the
    # child env (never on the argv), and BOTH are redacted from captured output.
    proof = tmp_path / "proof.txt"
    info = AssistantInfo.model_validate(
        {
            "name": "local_basic",
            "base_url": "http://localhost:8080",
            "bind": {
                "modes": {
                    "session": {
                        "command": [sys.executable, "-c", _capture_env_code(), str(proof)],
                        "secret_env": "DHIS2_SESSION_COOKIE",
                        "extra_secret_env": "DHIS2_SESSION_XSRF",
                    }
                }
            },
        }
    )
    app.state.assistant = info
    app.state.assistant_verified = (time.time(), object())  # stale outcome a successful bind must clear
    cookie, xsrf = "COOKIEVAL123", "XSRFVAL456"
    r = await client.post("/api/assistant/bind", json={"mode": "session", "secret": cookie, "extra_secret": xsrf})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["exit_code"] == 0
    assert proof.read_text() == f"{cookie}|{xsrf}"  # both handed over via env
    assert info.bind is not None
    assert all(cookie not in arg and xsrf not in arg for arg in info.bind.modes["session"].command)  # never on argv
    assert cookie not in body["stdout"] and xsrf not in body["stdout"]  # both redacted
    assert body["stdout"].count("***") >= 2
    assert app.state.assistant_verified is None  # verify cache invalidated


async def test_bind_session_mode_without_extra_secret_unchanged(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    # A session bind that omits extra_secret behaves exactly as before: the cookie is set, the XSRF
    # var is left unset in the child env.
    proof = tmp_path / "proof.txt"
    info = AssistantInfo.model_validate(
        {
            "name": "local_basic",
            "base_url": "http://localhost:8080",
            "bind": {
                "modes": {
                    "session": {
                        "command": [sys.executable, "-c", _capture_env_code(), str(proof)],
                        "secret_env": "DHIS2_SESSION_COOKIE",
                        "extra_secret_env": "DHIS2_SESSION_XSRF",
                    }
                }
            },
        }
    )
    app.state.assistant = info
    r = await client.post("/api/assistant/bind", json={"mode": "session", "secret": "ONLYCOOKIE"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert proof.read_text() == "ONLYCOOKIE|<unset>"  # XSRF var absent from the child env


async def test_bind_extra_secret_ignored_when_mode_has_no_extra_env(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    # A mode without extra_secret_env ignores a supplied extra_secret (lenient, no effect): the
    # cookie is set, the XSRF var stays unset.
    proof = tmp_path / "proof.txt"
    info = AssistantInfo.model_validate(
        {
            "name": "x",
            "bind": {
                "modes": {
                    "session": {
                        "command": [sys.executable, "-c", _capture_env_code(), str(proof)],
                        "secret_env": "DHIS2_SESSION_COOKIE",
                    }
                }
            },
        }
    )
    app.state.assistant = info
    r = await client.post("/api/assistant/bind", json={"mode": "session", "secret": "C", "extra_secret": "IGNORED"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert proof.read_text() == "C|<unset>"  # extra_secret had nowhere to go -> no effect


async def test_bind_extra_secret_size_capped(app: FastAPI, client: AsyncClient) -> None:
    # extra_secret is bounded by the same _MAX_SECRET cap as secret (400, not an unbounded env blob).
    from kodo.routers.serving.assistant import _MAX_SECRET

    info = AssistantInfo.model_validate(
        {
            "name": "x",
            "bind": {
                "modes": {
                    "session": {
                        "command": [sys.executable, "-c", "import sys"],
                        "secret_env": "DHIS2_SESSION_COOKIE",
                        "extra_secret_env": "DHIS2_SESSION_XSRF",
                    }
                }
            },
        }
    )
    app.state.assistant = info
    big = "z" * (_MAX_SECRET + 1)
    r = await client.post("/api/assistant/bind", json={"mode": "session", "secret": "ok", "extra_secret": big})
    assert r.status_code == 400
