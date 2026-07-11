"""Tests for loading the kodo.toml project manifest."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from kodo import project
from kodo.project import AssistantInfo, AssistantVerify


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert project.load(tmp_path / "kodo.toml") is None


def test_load_parses_model_and_prompt(tmp_path: Path) -> None:
    # Tools are no longer in kodo.toml (they live in .mcp.json); the manifest is model+prompt+libs.
    manifest = tmp_path / "kodo.toml"
    manifest.write_text('[project]\nmodel = "gemma-4-12B-it-QAT-GGUF"\nsystem_prompt = "Be terse."\n')
    proj = project.load(manifest)
    assert proj is not None
    assert proj.model == "gemma-4-12B-it-QAT-GGUF"
    assert proj.system_prompt == "Be terse."


def test_voice_defaults_and_toggle(tmp_path: Path) -> None:
    # chat_voice + [voice] enabled default sensibly, and parse when set.
    plain = tmp_path / "plain.toml"
    plain.write_text('[project]\nmodel = "x"\n')
    proj = project.load(plain)
    assert proj is not None and proj.chat_voice is None and proj.voice_enabled is True

    manifest = tmp_path / "kodo.toml"
    manifest.write_text('[project]\nmodel = "x"\nchat_voice = "kokoro:af_bella"\n\n[voice]\nenabled = false\n')
    proj = project.load(manifest)
    assert proj is not None
    assert proj.chat_voice == "kokoro:af_bella"
    assert proj.voice_enabled is False


def test_load_raises_projecterror_on_bad_toml(tmp_path: Path) -> None:
    p = tmp_path / "kodo.toml"
    p.write_text("this = = not toml [[[")
    with pytest.raises(project.ProjectError, match="not valid TOML"):
        project.load(p)


def test_render_manifest_round_trips(tmp_path: Path) -> None:
    # What render_manifest writes, load reads back — the writer and reader agree (A1).
    text = project.render_manifest(
        model="pub/Foo-GGUF",
        system_prompt="You are helpful.",
        local_library_dir="library",
        chat_voice="kokoro:af_heart",
    )
    p = tmp_path / "kodo.toml"
    p.write_text(text)
    loaded = project.load(p)
    assert loaded is not None
    assert loaded.model == "pub/Foo-GGUF"
    assert loaded.system_prompt == "You are helpful."
    assert loaded.chat_voice == "kokoro:af_heart"
    assert loaded.libraries == ["library", "@shared"]


def test_assistant_parses_known_and_extra_keys(tmp_path: Path) -> None:
    # [assistant] is echoed verbatim for UI clients: known fields validate, and unknown keys
    # survive (extra="allow") so a project can carry extra target hints untouched.
    p = tmp_path / "kodo.toml"
    p.write_text(
        '[project]\nmodel = "m"\n\n'
        '[assistant]\nname = "play42"\nbase_url = "https://demo/x"\nauth = "basic"\n'
        'readonly = true\nsource = "d2w profile play42"\nregion = "eu"\n\n'
        '[assistant.verify]\ntool = "dhis2__dhis2_cli"\ntimeout = 12.0\n'
        'args = { args = ["profile", "verify", "play42"] }\n'
    )
    proj = project.load(p)
    assert proj is not None and proj.assistant is not None
    a = proj.assistant
    assert a.name == "play42" and a.base_url == "https://demo/x" and a.readonly is True
    assert a.model_dump()["region"] == "eu"  # unknown key survived
    assert a.verify == AssistantVerify(
        tool="dhis2__dhis2_cli", args={"args": ["profile", "verify", "play42"]}, timeout=12.0
    )


def test_assistant_absent_is_none(tmp_path: Path) -> None:
    p = tmp_path / "kodo.toml"
    p.write_text('[project]\nmodel = "m"\n')
    proj = project.load(p)
    assert proj is not None and proj.assistant is None


def test_assistant_malformed_verify_raises_projecterror(tmp_path: Path) -> None:
    # A bad [assistant.verify] (missing the required tool) must fail like any manifest value — a
    # clean ProjectError, not a traceback.
    p = tmp_path / "kodo.toml"
    p.write_text('[project]\nmodel = "m"\n\n[assistant]\nname = "x"\n\n[assistant.verify]\ntimeout = 5.0\n')
    with pytest.raises(project.ProjectError, match="invalid value"):
        project.load(p)


def test_render_manifest_round_trips_assistant(tmp_path: Path) -> None:
    # render (the single writer) -> tomllib -> load (the single parser) is a closed round-trip,
    # including the inline-table verify args and an extra key.
    info = AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://demo/x",
            "auth": "basic",
            "readonly": True,
            "source": "d2w profile play42",
            "region": "eu",  # extra key
            "verify": {"tool": "dhis2__dhis2_cli", "args": {"args": ["profile", "verify", "play42"]}, "timeout": 15.0},
        }
    )
    text = project.render_manifest(model="pub/Foo-GGUF", system_prompt="hi", assistant=info)
    tomllib.loads(text)  # valid TOML
    p = tmp_path / "kodo.toml"
    p.write_text(text)
    loaded = project.load(p)
    assert loaded is not None and loaded.assistant == info


def test_render_manifest_quotes_non_bare_assistant_keys(tmp_path: Path) -> None:
    # A key with a space would emit invalid TOML; a dotted key would silently become a nested
    # table (a DIFFERENT value). Both must survive the render -> load round-trip via quoted keys.
    info = AssistantInfo.model_validate(
        {
            "name": "x",
            "region.sub": "eu",  # dotted: unquoted this round-trips to {"region": {"sub": "eu"}}
            "weird key": "v",  # spaced: unquoted this is a TOMLDecodeError
            "verify": {"tool": "t", "args": {"dotted.arg": "a", "spaced arg": "b"}},
        }
    )
    text = project.render_manifest(model="m", assistant=info)
    tomllib.loads(text)  # valid TOML
    p = tmp_path / "kodo.toml"
    p.write_text(text)
    loaded = project.load(p)
    assert loaded is not None and loaded.assistant == info


def test_render_manifest_rejects_non_scalar_assistant_values(tmp_path: Path) -> None:
    # A dict-valued extra key would render as JSON-object syntax, which is invalid TOML —
    # the single writer must refuse it (ProjectError) instead of writing a manifest that
    # bricks the freshly scaffolded project on the next load().
    info = AssistantInfo.model_validate({"name": "x", "labels": {"env": "prod"}})
    with pytest.raises(project.ProjectError, match="scalars"):
        project.render_manifest(model="m", assistant=info)


def test_assistant_verify_extra_keys_round_trip(tmp_path: Path) -> None:
    # [assistant.verify] carries unknown keys through render -> load, matching the parent
    # block's extra="allow" pass-through promise (they must not silently vanish).
    info = AssistantInfo.model_validate({"name": "x", "verify": {"tool": "t", "args": {"a": "b"}, "retries": 3}})
    text = project.render_manifest(model="m", assistant=info)
    p = tmp_path / "kodo.toml"
    p.write_text(text)
    loaded = project.load(p)
    assert loaded is not None and loaded.assistant is not None and loaded.assistant == info
    verify = loaded.assistant.verify
    assert verify is not None
    assert verify.model_dump()["retries"] == 3


def test_render_manifest_rejects_bad_verify_arg_type(tmp_path: Path) -> None:
    # The single writer refuses an arg value that isn't a string or list of strings, so it never
    # emits a kodo.toml the parser would reject.
    info = AssistantInfo.model_validate(
        {"name": "x", "verify": {"tool": "t", "args": {"n": 7}}}  # int arg value → not renderable as TOML
    )
    with pytest.raises(project.ProjectError, match="string or list of strings"):
        project.render_manifest(model="m", assistant=info)


def test_render_manifest_round_trips_probe_and_bind(tmp_path: Path) -> None:
    # [assistant.probe] + [assistant.bind] (with per-mode sub-tables, the JSON-braced mint_payload,
    # and extra keys at every level) survive the render (single writer) -> load (single parser) trip.
    info = AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://demo/x",
            "probe": {
                "paths": ["/api/me.json?fields=name,username", "/api/system/info.json"],
                "fields": {"name": ["0.name"], "version": ["1.version", "1.v"]},
                "label": "Browsing as {name} on {instanceName} ({version})",
                "note": "extra-probe",  # extra key rides along (extra="allow")
            },
            "bind": {
                "mint_mode": "pat",
                "mint_path": "/api/apiToken",
                "mint_payload": '{"type":"X","expire":{expires_ms},"description":{description}}',
                "revoke_path": "/api/apiToken/{credential_id}",
                "methods_readonly": ["GET"],
                "methods_full": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                "region": "eu",  # extra key on the bind block
                "modes": {
                    "pat": {
                        "command": ["tool", "add", "--url", "{base_url}", "--name", "{name}"],
                        "secret_env": "TOK",
                        "unbind_command": ["tool", "remove"],
                        "unbind_note": "restore the shared profile",
                        "extra_flag": True,  # extra key on a mode
                    },
                    "session": {"command": ["tool", "add", "--url", "{base_url}"], "secret_env": "COOKIE"},
                },
            },
        }
    )
    text = project.render_manifest(model="m", assistant=info)
    tomllib.loads(text)  # valid TOML
    p = tmp_path / "kodo.toml"
    p.write_text(text)
    loaded = project.load(p)
    assert loaded is not None and loaded.assistant is not None
    assert loaded.assistant == info  # full closed round-trip, extras and all
    assert loaded.assistant.probe is not None and loaded.assistant.probe.model_dump()["note"] == "extra-probe"
    assert loaded.assistant.bind is not None and loaded.assistant.bind.model_dump()["region"] == "eu"
    assert loaded.assistant.bind.modes["pat"].model_dump()["extra_flag"] is True


def test_probe_rejects_absolute_or_traversal_path() -> None:
    # A probe path must be same-origin + relative: an absolute URL (scheme) or a "//" or ".." is rejected.
    for bad in ("https://evil.example/x", "//evil/x", "/api/../../etc", "api/relative"):
        with pytest.raises(ValidationError):
            AssistantInfo.model_validate({"probe": {"paths": [bad]}})


def test_bind_rejects_bad_command_placeholder() -> None:
    # A bind mode's argv may only template {base_url}/{name}; any other placeholder is rejected at parse.
    with pytest.raises(ValidationError):
        AssistantInfo.model_validate({"bind": {"modes": {"pat": {"command": ["run", "{evil}"], "secret_env": "X"}}}})


def test_bind_rejects_non_bare_mode_name() -> None:
    # A mode name becomes a TOML sub-table key, so a non-bare name (space) is rejected at parse.
    with pytest.raises(ValidationError):
        AssistantInfo.model_validate({"bind": {"modes": {"bad key": {"command": ["run"], "secret_env": "X"}}}})


def test_bind_rejects_mode_name_with_trailing_newline() -> None:
    # _BARE_KEY must reject "pat\n": "$" matches before a trailing newline, so an unquoted newline
    # key would emit invalid TOML and break the single-writer round-trip. \Z anchoring fixes it.
    with pytest.raises(ValidationError):
        AssistantInfo.model_validate({"bind": {"modes": {"pat\n": {"command": ["run"], "secret_env": "X"}}}})


def test_bind_rejects_invalid_secret_env_name() -> None:
    # secret_env holds the child env-var name the client secret is exported as; a non-env-var name
    # (spaces/junk) is rejected at parse.
    with pytest.raises(ValidationError):
        AssistantInfo.model_validate({"bind": {"modes": {"pat": {"command": ["run"], "secret_env": "bad name"}}}})


@pytest.mark.parametrize(
    "name", ["PATH", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH"]
)
def test_bind_rejects_loader_controlling_secret_env(name: str) -> None:
    # The secret is client-supplied data; routing it into a loader-controlling var would let a caller
    # control what the child loads/runs. All such names are rejected at parse.
    with pytest.raises(ValidationError):
        AssistantInfo.model_validate({"bind": {"modes": {"pat": {"command": ["run"], "secret_env": name}}}})


def test_bind_rejects_out_of_range_timeout() -> None:
    # timeout is bounded (0 < t <= 300) so one bad mode can't hold the shared bind lock forever.
    for bad in (0, -1, 999):
        with pytest.raises(ValidationError):
            AssistantInfo.model_validate(
                {"bind": {"modes": {"pat": {"command": ["run"], "secret_env": "X", "timeout": bad}}}}
            )


def test_bind_rejects_unknown_mint_payload_token() -> None:
    # A mint_payload typo like {expires_days} is caught at kodo.toml load, not later at mint time in
    # the browser. The allowed tokens (and the payload's own JSON braces) validate fine.
    with pytest.raises(ValidationError, match="mint_payload"):
        AssistantInfo.model_validate({"bind": {"mint_payload": '{"expire":{expires_days}}'}})
    AssistantInfo.model_validate(
        {"bind": {"mint_payload": '{"type":"X","expire":{expires_ms},"m":{allowed_methods},"d":{description}}'}}
    )


def test_bind_rejects_unknown_revoke_path_token() -> None:
    # revoke_path may only template {credential_id}; any other {token} is a manifest typo.
    with pytest.raises(ValidationError, match="revoke_path"):
        AssistantInfo.model_validate({"bind": {"revoke_path": "/api/apiToken/{token_uid}"}})
    AssistantInfo.model_validate({"bind": {"revoke_path": "/api/apiToken/{credential_id}"}})


def test_render_manifest_rejects_non_scalar_probe_extra(tmp_path: Path) -> None:
    # The single writer stays closed: a dict-valued probe extra can't render as TOML → ProjectError,
    # never a manifest that would brick the next load().
    info = AssistantInfo.model_validate({"probe": {"paths": ["/api/x"], "labels": {"env": "prod"}}})
    with pytest.raises(project.ProjectError, match="scalars"):
        project.render_manifest(model="m", assistant=info)


def test_load_rejects_bad_probe_path_as_projecterror(tmp_path: Path) -> None:
    # A hand-edited kodo.toml with a bad probe path fails like any manifest value — a clean ProjectError.
    p = tmp_path / "kodo.toml"
    p.write_text('[project]\nmodel = "m"\n\n[assistant]\nname = "x"\n\n[assistant.probe]\npaths = ["notabs"]\n')
    with pytest.raises(project.ProjectError, match="invalid value"):
        project.load(p)


def test_read_raw_is_the_single_parser(tmp_path: Path) -> None:
    # read_raw underlies both the manifest (load) and the machine settings (kodo.config), so a
    # malformed file raises one clean ProjectError rather than crashing differently in each.
    assert project.read_raw(tmp_path / "absent.toml") == {}
    p = tmp_path / "kodo.toml"
    p.write_text('library_root = "/x"\n[project]\nmodel = "m"\n')
    assert project.read_raw(p)["library_root"] == "/x"  # machine key + manifest table in one parse
    p.write_text("nope = = [[[")
    with pytest.raises(project.ProjectError, match="not valid TOML"):
        project.read_raw(p)
