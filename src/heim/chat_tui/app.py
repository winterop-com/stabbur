"""The Textual chat application (ChatApp) and the run_interactive entry point."""

import asyncio
import random
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static, TextArea

from heim import agent, attach, capabilities, project
from heim import library as library_ops
from heim import runtime as runtime_mod
from heim import tools as mcp_tools
from heim.chat_tui._util import _GERUNDS, _SAMPLING_FIELDS, _SLASH_COMMANDS, _SPINNER, _fmt_tokens
from heim.chat_tui._widgets import ChatInput, ConfirmModal, ModelPickerModal, _HeimCommands
from heim.runtime import sampling as sampling_mod


def _confirm_policy(proj: project.Project | None) -> Literal["all", "writes", "none"]:
    """The tool-confirmation policy for a session, derived from the project's assistant.

    A write-enabled assistant (an ``[assistant]`` block that is not ``readonly``) gates its
    non-read-only tool calls behind confirmation (``"writes"``); no project, no assistant, or a
    read-only assistant gates nothing (``"none"`` — identical to the pre-confirmation behavior).
    """
    if proj is None or proj.assistant is None or proj.assistant.readonly:
        return "none"
    return "writes"


class RemoteEndpoint(BaseModel):
    """An already-running OpenAI-compatible server the TUI attaches to (and never owns).

    Built by ``heim chat --server``: the TUI chats against ``base`` but must not stop the
    server on exit or switch its model. ``model`` carries local library metadata when the
    served model also resolved locally (sampling, capabilities); otherwise the session runs
    on the server-reported fields alone.
    """

    base: str  # normalized base URL (no trailing / or /v1)
    model: library_ops.LibraryModel | None = None  # local metadata, when the name resolved in the library
    model_name: str  # display name (library / /api/status / /v1/models / the URL itself)
    model_id: str | None = None  # OpenAI ``model`` field when there is no local model (from /v1/models)
    n_ctx: int | None = None  # context window reported by heim serve's /api/status, if it answered


class ChatApp(App[None]):
    """The heim chat TUI: transcript + input + pinned status footer."""

    CSS = """
    Screen { layers: base; }
    #transcript { height: 1fr; padding: 1 2; scrollbar-size-vertical: 0; }
    #transcript > .user { color: $text-muted; margin-top: 2; }
    #transcript > .tools { color: $text-muted; }
    #transcript > .answer { margin-bottom: 1; }
    #transcript Collapsible { border: none; padding: 0; background: transparent; }
    #transcript Collapsible > CollapsibleTitle { color: $text-muted; padding: 0; }
    #transcript Collapsible Contents { padding: 0 0 0 2; }
    .reasoning { color: $text-muted; }
    #transcript > .intro { margin-bottom: 1; }
    #status { height: 3; padding: 1 2 0 2; color: $text-muted; }
    #suggest { height: auto; max-height: 8; padding: 0 3; margin: 0 1; color: $text-muted; display: none; }
    #input { height: auto; max-height: 10; border: round #3a3a3a; margin: 0 1 1 1; padding: 0 1; }
    #input:focus { border: round #fb7185; }
    """

    COMMANDS = App.COMMANDS | {_HeimCommands}

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", priority=True),
        Binding("ctrl+p", "command_palette", "Commands", show=True, priority=True),
        Binding("escape", "cancel", "Stop", show=True),
        # A full-screen TUI captures the mouse, so the terminal's own click-drag selection
        # doesn't reach the text. Give an explicit "copy the last reply" shortcut; Textual's
        # own click-drag + ctrl+c selection also works, and holding Option (macOS terminals)
        # falls back to native terminal selection.
        Binding("ctrl+y", "copy_reply", "Copy reply", show=True, priority=True),
    ]

    def __init__(
        self,
        *,
        endpoint: "runtime_mod.RuntimeProc | RemoteEndpoint",
        servers: list[tuple[str | None, list[str], dict[str, str]]],
        system_prompt: str,
        images: list[str],
        audios: list[str],
        max_tokens: int | None,
    ) -> None:
        super().__init__()
        # A spawned runtime the TUI owns (it can swap it on model switch, and stops it on
        # exit) — or a RemoteEndpoint it merely attaches to and must leave running.
        self._endpoint: runtime_mod.RuntimeProc | RemoteEndpoint = endpoint
        self._model: library_ops.LibraryModel | None = endpoint.model
        self._servers = servers
        self._max_tokens = max_tokens
        # Confirmation policy from the loaded project's assistant: a write-enabled assistant gates
        # its non-read-only tool calls behind the ConfirmModal; otherwise nothing is gated.
        self._confirm_policy: Literal["all", "writes", "none"] = _confirm_policy(project.load())
        self._apply_model()  # derive _model_name/_format/_target/_base/_sampling/_ctx_max
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}] if system_prompt else []
        self._pending_images: list[str] | None = images
        self._pending_audios: list[str] | None = audios
        self.ctx_used: int | None = None
        self.toolset = mcp_tools.MCPToolset()  # replaced once MCP servers connect
        self._stack = AsyncExitStack()
        self._busy = False
        self._switching = False  # true while a model swap is loading
        self._queue: list[str] = []  # prompts submitted while a reply is streaming
        self._disabled: set[str] = set()  # MCP server prefixes the user turned off
        self._models_cache: list[library_ops.LibraryModel] | None = None  # switchable models (lazy)
        self._remote_models_cache: list[tuple[str, bool]] | None = None  # remote (id, loaded) rows (lazy)
        self._reason_collapsed_pref = False  # sticky: once the user collapses thinking, keep it collapsed
        self._reason_prog: dict[int, bool] = {}  # per reasoning box: the collapsed state heim set itself
        # A turn's reasoning is not part of self.messages (it's never resent to the model), so keep
        # it here keyed by the assistant message's id() for `/export --thinking` to pull back.
        self._reasonings: dict[int, str] = {}

    @on(Collapsible.Toggled)
    def _remember_reasoning_collapse(self, event: Collapsible.Toggled) -> None:
        """Remember the user's thinking-collapse choice so the next turn's block starts that way.

        ``@on(Collapsible.Toggled)`` catches both the Expanded and Collapsed subclasses. heim toggles
        the box itself (on mount, and the auto-collapse in ``finalize_reasoning``); those are ignored
        by comparing against the last state heim set for that box, so only a real user click sticks.
        """
        box = event.collapsible
        if not box.has_class("reasoning-box") or box.collapsed == self._reason_prog.get(id(box)):
            return
        self._reason_prog[id(box)] = box.collapsed
        self._reason_collapsed_pref = box.collapsed

    def _apply_model(self) -> None:
        """Set the per-model fields (name/format/target/base/sampling/context) from the current endpoint."""
        self._base = self._endpoint.base
        remote = self._endpoint if isinstance(self._endpoint, RemoteEndpoint) else None
        model = self._model
        self._vision = False  # feed tool-returned images back only when the model can see them
        if model is not None:
            self._model_name = model.name
            self._model_format = model.model_format.value
            self._model_target: str | None = str(model.load_target)  # OpenAI ``model`` field (mlx-vlm needs it)
            self._sampling = sampling_mod.recommended(model)
            try:
                caps = capabilities.capabilities(model)
                self._ctx_max = caps.context_length
                self._vision = caps.vision
            except Exception:  # noqa: BLE001 - detection is best-effort; the footer just omits ctx
                self._ctx_max = None
            if remote is not None and remote.n_ctx is not None:
                self._ctx_max = remote.n_ctx  # the window the server actually loaded, not the model's max
        else:
            # Remote attach with no local copy: run on the server-reported fields alone.
            assert remote is not None  # a spawned runtime always carries its LibraryModel
            self._model_name = remote.model_name
            self._model_format = "remote"
            self._model_target = remote.model_id
            self._sampling = sampling_mod.ModelSampling(repeat_penalty=sampling_mod.DEFAULT_REPEAT_PENALTY)
            self._ctx_max = remote.n_ctx

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        yield Static(id="suggest")  # slash-command autocomplete menu (above the input)
        yield ChatInput(id="input", soft_wrap=True, show_line_numbers=False)
        yield Static(id="status")

    async def on_mount(self) -> None:
        self.title = "heim chat"
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(Static(self._intro(), classes="intro"))
        self._refresh_status()
        self.query_one(ChatInput).focus()
        # Prime the remote model list (for /model and its name completion) off the UI thread.
        if isinstance(self._endpoint, RemoteEndpoint):
            self.run_worker(self._prime_remote_models(), group="models")
        # Connecting MCP servers can take a moment; do it after the UI is up.
        if self._servers:
            self.toolset = await self._stack.enter_async_context(mcp_tools.connect(self._servers))
            self._refresh_status()

    async def on_unmount(self) -> None:
        await self._stack.aclose()

    # -- status footer ---------------------------------------------------------

    def _intro(self) -> Group:
        """The welcome block shown on start: heim wordmark + version, then model + endpoint."""
        from importlib.metadata import version as _pkg_version  # noqa: PLC0415

        try:
            ver = _pkg_version("heim")
        except Exception:  # noqa: BLE001 - version is cosmetic; never block the intro on it
            ver = ""

        title = Text()
        title.append("♥ ", style="#f43f5e")  # heartbeat — heim is 鼓動, "heartbeat/pulse"
        title.append("heim", style="bold #f43f5e")
        if ver:
            title.append(f"  v{ver}", style="grey50")
        title.append("   local models, on your own hardware", style="grey42")

        model = Text()
        model.append(self._model_name, style="bold")
        model.append(f"   {self._model_format}", style="grey50")

        api = Text(f"{self._base}/v1", style="grey42")
        api.append("   OpenAI-compatible", style="grey35")
        if isinstance(self._endpoint, RemoteEndpoint):
            api.append("   attached (remote)", style="grey35")

        return Group(title, Text(), model, api)

    def _status_renderable(self) -> Group:
        line1 = Text()
        if self._switching:
            line1.append("switching… ", style="#fb7185")
        line1.append(self._model_name, style="grey62")
        if self._ctx_max:
            used = self.ctx_used or 0
            pct = used / self._ctx_max
            color = "green" if pct < 0.5 else "yellow" if pct < 0.85 else "red"
            window = f"{round(self._ctx_max / 1024)}K" if self._ctx_max >= 1024 else str(self._ctx_max)
            line1.append("  ·  ", style="grey37")
            line1.append(f"{_fmt_tokens(used)}/{window}", style=color)
            line1.append(f" ({pct * 100:.0f}%)", style="grey50")
        n_tools = len(self._active_toolset().names)
        n_off = len(self._disabled)
        if n_tools or n_off:
            line1.append("  ·  ", style="grey37")
            line1.append(f"{n_tools} tool{'s' if n_tools != 1 else ''}", style="cyan")
            if n_off:
                line1.append(f" ({n_off} off)", style="grey42")
        if self._queue:
            line1.append("  ·  ", style="grey37")
            line1.append(f"{len(self._queue)} queued", style="yellow")

        line2 = Text("enter send  ·  ^p commands  ·  /help  ·  esc stop  ·  /exit", style="grey42")
        return Group(line1, line2)

    def _last_reply_text(self) -> str | None:
        """The text of the most recent assistant reply (skips tool-call turns), if any."""
        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
        return None

    @staticmethod
    def _content_text(content: Any) -> str:
        """Plain text of a message's content (a string, or the text parts of a multimodal list)."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        return ""

    def action_export(self, path: str | None = None, *, thinking: bool = False) -> None:
        """Write the conversation to a Markdown file (default: chat.md in the current directory).

        With ``thinking``, each assistant turn's reasoning is included as a collapsed ``<details>``
        block before its answer — the transcript stays readable, but the thinking is there to expand.
        """
        turns = [
            m
            for m in self.messages
            if m.get("role") in ("user", "assistant") and self._content_text(m.get("content")).strip()
        ]
        if not turns:
            self.notify("Nothing to export yet.", severity="warning", timeout=2)
            return
        dest = Path(path) if path else Path("chat.md")
        lines = [f"# Chat — {self._model_name}", ""]
        for m in self.messages:
            role, text = m.get("role"), self._content_text(m.get("content"))
            if not text.strip():
                continue
            heading = {"system": "System prompt", "user": "You", "assistant": "Assistant"}.get(str(role))
            if not heading:
                continue
            lines += [f"## {heading}", ""]
            reason = self._reasonings.get(id(m), "").strip() if thinking and role == "assistant" else ""
            if reason:
                lines += ["<details>", "<summary>Thinking</summary>", "", reason, "", "</details>", ""]
            lines += [text, ""]
        try:
            dest.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            self.notify(f"Export failed: {exc}", severity="error", timeout=3)
            return
        self.notify(f"Exported to {dest}", timeout=3)
        self._post(Text(f"exported transcript → {dest}", style="grey50"))

    def action_show_sampling(self) -> None:
        """Show the current sampling settings (change them with `/set <param> <value>`)."""
        body = Text("sampling\n", style="bold #fb7185")
        for field in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty"):
            value = getattr(self._sampling, field)
            body.append(f"  {field}", style="grey66")
            body.append(f"   {value if value is not None else '—'}\n", style="grey42")
        body.append("\n/set <param> <value>  ·  e.g. /set temperature 0.7", style="grey42")
        self._post(body)

    def _set_sampling(self, param: str, value: str) -> None:
        """Apply `/set <param> <value>` to the live sampling used for subsequent turns."""
        field = _SAMPLING_FIELDS.get(param.lower())
        if field is None:
            self._post(
                Text(f"unknown setting {param!r} — temperature/top_p/top_k/min_p/repeat_penalty", style="grey50")
            )
            return
        try:
            num: float | int = int(value) if field == "top_k" else float(value)
        except ValueError:
            self._post(Text(f"{value!r} is not a number", style="grey50"))
            return
        self._sampling = self._sampling.model_copy(update={field: num})
        self._post(Text(f"{field} = {num}", style="grey50"))
        self._refresh_status()

    # -- model switch ----------------------------------------------------------

    def _switchable_models(self) -> list[library_ops.LibraryModel]:
        """Generative library models the session can switch to (scanned once, then cached)."""
        if self._models_cache is None:
            try:
                self._models_cache = [m for m in library_ops.scan() if m.generative]
            except Exception:  # noqa: BLE001 - a missing/unreadable library just means no switching
                self._models_cache = []
        return self._models_cache

    def _fetch_remote_models(self) -> list[tuple[str, bool]]:
        """The remote's ``GET /v1/models`` as ``(id, loaded)`` rows — empty on any failure.

        ``loaded`` comes from llama-server router mode's per-model ``status``; servers that
        don't report one (LM Studio, mlx-lm, a plain llama-server) just read as not-loaded.
        """
        import httpx  # noqa: PLC0415 - keep the TUI module import-light

        try:
            data = httpx.get(f"{self._base}/v1/models", timeout=5).json()
        except (httpx.HTTPError, ValueError):
            return []
        rows = data.get("data") if isinstance(data, dict) else None
        out: list[tuple[str, bool]] = []
        if isinstance(rows, list):
            for row in rows:
                rid = row.get("id") if isinstance(row, dict) else None
                if isinstance(rid, str):
                    status = row.get("status")
                    out.append((rid, isinstance(status, dict) and status.get("value") == "loaded"))
        return out

    async def _prime_remote_models(self) -> None:
        """Fill the remote model cache off the UI thread (for /model and its name completion)."""
        self._remote_models_cache = await asyncio.to_thread(self._fetch_remote_models)

    def action_show_models(self) -> None:
        """Open the model picker (arrow keys + Enter) — the remote's ids, or library models."""
        self.run_worker(self._pick_model(), group="models")

    async def _pick_model(self) -> None:
        """Gather the switchable models, show the picker modal, and switch to the choice."""
        if isinstance(self._endpoint, RemoteEndpoint):
            self._remote_models_cache = await asyncio.to_thread(self._fetch_remote_models)
            listed = self._remote_models_cache
            if not listed:
                self._post(Text(f"couldn't list models at {self._base} — is the server running?", style="grey50"))
                return
            rows = [(rid, "loaded" if is_loaded else "") for rid, is_loaded in listed]
            current = self._model_target or self._model_name
            title = f"models · {len(rows)} on {self._base}"
        else:
            models = self._switchable_models()
            if not models:
                self._post(Text("No switchable models in the library.", style="grey50"))
                return
            rows = [(m.name, m.model_format.value) for m in models]
            current = self._model_name
            title = f"models · {len(rows)} in the library"
        choice = await self.push_screen_wait(ModelPickerModal(rows, current, title))
        if choice is None or choice == current:
            return
        self.action_switch_model(choice)

    def _switch_remote_model(self, name: str) -> None:
        """Point the session at another of the remote's models (matched against ``/v1/models``).

        No teardown: a router-mode server hot-swaps on the next request, so switching is just
        changing the OpenAI ``model`` field. The conversation carries over — the new model sees
        the full history. A single-model server simply lists nothing else to switch to.
        """
        listed = self._remote_models_cache if self._remote_models_cache else self._fetch_remote_models()
        self._remote_models_cache = listed
        ids = [rid for rid, _ in listed]
        if not ids:
            self._post(Text(f"couldn't list models at {self._base} — is the server running?", style="grey50"))
            return
        want = name.strip().lower()
        want_base = want.rsplit("/", 1)[-1]
        match = next((rid for rid in ids if rid.lower() == want or rid.lower().rsplit("/", 1)[-1] == want_base), None)
        if match is None:
            self._post(Text(f"{self._base} does not serve {name!r} — /model to list", style="grey50"))
            return
        endpoint = self._endpoint
        assert isinstance(endpoint, RemoteEndpoint)  # only called for remote attaches
        if match == (endpoint.model_id or self._model_name):
            self._post(Text(f"already on {match}", style="grey50"))
            return
        endpoint.model_id = match
        endpoint.model_name = match
        endpoint.model = None  # any local library metadata described the previous model
        endpoint.n_ctx = None  # so does the context window the old status reported
        self._model = None
        self._apply_model()
        self._refresh_status()
        self._post(Text(f"switched to {match} — the server loads it on the next message", style="grey50"))

    def action_switch_model(self, name: str) -> None:
        """Switch the running model: swap the local runtime, or repoint a remote attach."""
        if self._busy:
            self.notify("Stop the current reply first (Esc).", severity="warning", timeout=2)
            return
        if isinstance(self._endpoint, RemoteEndpoint):
            self._switch_remote_model(name)
            return
        if self._switching:
            self.notify("Already switching models.", severity="warning", timeout=2)
            return
        want = name.strip().lower()
        match = next(
            (m for m in self._switchable_models() if want in (m.name.lower(), m.name.split("/")[-1].lower())),
            None,
        )
        if match is None:
            self._post(Text(f"no model matching {name!r} — /model to list", style="grey50"))
            return
        if match.name == self._model_name:
            self._post(Text(f"already running {match.name}", style="grey50"))
            return
        self.run_worker(self._do_switch(match), group="switch")

    async def _do_switch(self, model: library_ops.LibraryModel) -> None:
        """Tear down the current runtime and load ``model`` (off the event loop), then rebind to it."""
        self._switching = True
        self._refresh_status()
        self._post(Text(f"switching to {model.name} … (loading — this can take a moment)", style="grey50"))
        try:
            new = await asyncio.to_thread(self._reserve, model)
        except Exception as exc:  # noqa: BLE001 - surface a failed load instead of hanging
            self._post(Text(f"switch failed: {exc}", style="red"))
            self._switching = False
            self._refresh_status()
            return
        self._endpoint = new
        self._model = model
        self._apply_model()
        self.ctx_used = None  # new model, fresh context window
        self._switching = False
        self._refresh_status()
        self._post(Text(f"switched to {model.name}", style="grey50"))

    def _reserve(self, model: library_ops.LibraryModel) -> "runtime_mod.RuntimeProc":
        """(worker thread) Stop the current runtime, then start + wait for the new one."""
        assert isinstance(self._endpoint, runtime_mod.RuntimeProc)  # switching is blocked on remote attaches
        runtime_mod.stop(self._endpoint)  # free memory before loading the next model
        new = runtime_mod.start(model)
        try:
            runtime_mod.wait_ready(new)
        except BaseException:
            runtime_mod.stop(new)
            raise
        return new

    def action_copy_reply(self) -> None:
        """Copy the last assistant reply to the clipboard (works over SSH via OSC 52)."""
        text = self._last_reply_text()
        if text:
            self.copy_to_clipboard(text)
            self.notify(f"Copied last reply ({len(text)} chars)", timeout=2)
        else:
            self.notify("No reply to copy yet.", severity="warning", timeout=2)

    # -- slash-command autocomplete ---------------------------------------------

    def _match_commands(self, text: str) -> list[tuple[str, str, str]]:
        """Slash commands whose name prefixes the (single-line) text being typed."""
        query = text.lstrip("/").lower()
        return [c for c in _SLASH_COMMANDS if c[0].lstrip("/").lower().startswith(query)]

    def _match_model_args(self, text: str) -> list[tuple[str, str, str]]:
        """Model-name completions for ``/model <partial>``: library models, or the remote's ids.

        Matches by prefix on the full name or its basename (the part after the last ``/``), the
        same shapes ``/model <name>`` itself accepts. The remote candidates come from the cache
        primed at mount / by ``/model`` — never a network call per keystroke.
        """
        if not text.startswith("/model ") or "\n" in text:
            return []
        partial = text[len("/model ") :].strip().lower()
        if isinstance(self._endpoint, RemoteEndpoint):
            candidates = [(rid, "loaded" if is_loaded else "") for rid, is_loaded in (self._remote_models_cache or [])]
        else:
            candidates = [(m.name, m.model_format.value) for m in self._switchable_models()]
        return [
            (cand, f"/model {cand}", note)
            for cand, note in candidates
            if not partial or cand.lower().startswith(partial) or cand.lower().rsplit("/", 1)[-1].startswith(partial)
        ]

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._update_suggestions()

    def _update_suggestions(self) -> None:
        """Show/refresh the autocomplete menu while a slash command is being typed."""
        suggest = self.query_one("#suggest", Static)
        text = self.query_one(ChatInput).text
        matches: list[tuple[str, str, str]] = []
        if text.startswith("/") and "\n" not in text:
            matches = self._match_model_args(text) or self._match_commands(text)
        if not matches:
            suggest.display = False
            return
        body = Text()
        for i, (display, _complete, help_text) in enumerate(matches):
            if i:
                body.append("\n")
            body.append(f"{display:<20}", style="#fb7185")
            body.append(f"  {help_text}", style="grey42")
        body.append("\ntab", style="grey50")
        body.append(" complete  ·  ", style="grey37")
        body.append("enter", style="grey50")
        body.append(" run", style="grey37")
        suggest.update(body)
        suggest.display = True

    def _complete_slash(self) -> None:
        """Tab: fill the input with the matching command or model name (or their common prefix)."""
        input_w = self.query_one(ChatInput)
        matches = self._match_model_args(input_w.text) or self._match_commands(input_w.text)
        if not matches:
            return
        completions = [c[1] for c in matches]
        if len(completions) == 1:
            new = completions[0]
        else:
            new = completions[0]
            for other in completions[1:]:
                while not other.startswith(new):
                    new = new[:-1]
        if new and new != input_w.text:
            input_w.text = new
            input_w.move_cursor(input_w.document.end)
        self._update_suggestions()

    # -- commands / MCP management ----------------------------------------------

    def _post(self, renderable: Any) -> None:
        """Mount a muted command-output block in the transcript."""
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(Static(renderable, classes="reasoning"))
        transcript.scroll_end(animate=False)

    def _server_names(self) -> list[str]:
        """Distinct MCP server prefixes in the loaded toolset, in load order."""
        seen: list[str] = []
        for schema in self.toolset.schemas:
            srv = schema["function"]["name"].split("__")[0]
            if srv not in seen:
                seen.append(srv)
        return seen

    def _active_toolset(self) -> mcp_tools.MCPToolset:
        """What the model sees: the full toolset minus any disabled servers."""
        if not self._disabled:
            return self.toolset
        names = {
            s["function"]["name"]
            for s in self.toolset.schemas
            if s["function"]["name"].split("__")[0] not in self._disabled
        }
        return self.toolset.subset(names)

    def action_show_mcp(self) -> None:
        """List MCP servers + their tools (with on/off state) in the transcript."""
        from collections import defaultdict  # noqa: PLC0415

        servers = self._server_names()
        if not servers:
            self._post(Text("No MCP tools loaded — add servers with `heim mcp add <name>`.", style="grey50"))
            return
        by_server: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for s in self.toolset.schemas:
            srv, _, tool = s["function"]["name"].partition("__")
            desc = (s["function"].get("description", "") or "").splitlines()[0]
            by_server[srv].append((tool or s["function"]["name"], desc))
        body = Text()
        body.append(
            f"MCP  ·  {len(self._active_toolset().names)} tool(s) active · {len(servers)} server(s)\n", "grey50"
        )
        for srv in servers:
            off = srv in self._disabled
            body.append(f"\n{srv}", style="bold grey42" if off else "bold #fb7185")
            body.append("  (disabled)\n" if off else "\n", style="grey42")
            for tool, desc in by_server[srv]:
                body.append(f"  {tool}", style="grey35" if off else "grey66")
                if desc:
                    body.append(f"   {desc[:60]}", style="grey42")
                body.append("\n")
        body.append("\n/mcp on <server>  ·  /mcp off <server>  ·  /mcp reconnect", style="grey42")
        self._post(body)

    def _mcp_toggle(self, server: str, enable: bool) -> None:
        if server not in self._server_names():
            self._post(Text(f"no such server {server!r} — /mcp to list", style="grey50"))
            return
        self._disabled.discard(server) if enable else self._disabled.add(server)
        self._refresh_status()
        self.action_show_mcp()

    def action_reconnect_mcp(self) -> None:
        """Respawn the project's MCP servers (useful if one died or its config changed)."""
        if not self._servers:
            self._post(Text("No MCP servers to reconnect.", style="grey50"))
            return
        if self._busy:
            self.notify("Stop the current reply first (Esc).", severity="warning", timeout=2)
            return
        self.run_worker(self._reconnect_mcp(), group="mcp")

    async def _reconnect_mcp(self) -> None:
        self.notify("Reconnecting MCP servers…", timeout=2)
        try:
            await self._stack.aclose()
        except Exception:  # noqa: BLE001 - closing a dead connection can raise; ignore
            pass
        self._stack = AsyncExitStack()
        self.toolset = mcp_tools.MCPToolset()
        self._refresh_status()
        try:
            self.toolset = await self._stack.enter_async_context(mcp_tools.connect(self._servers))
        except Exception as exc:  # noqa: BLE001 - surface a failed reconnect instead of hanging
            self._post(Text(f"reconnect failed: {exc}", style="red"))
        self._refresh_status()
        self._post(Text(f"Reconnected — {len(self.toolset.names)} tool(s).", style="grey50"))

    def action_clear(self) -> None:
        """Clear the transcript (keep the intro) and reset the conversation (keep the system prompt)."""
        if self._busy:
            self.notify("Stop the current reply first (Esc).", severity="warning", timeout=2)
            return
        transcript = self.query_one("#transcript", VerticalScroll)
        for child in list(transcript.children):
            if "intro" not in child.classes:
                child.remove()
        self.messages = [m for m in self.messages if m.get("role") == "system"]
        # Drop stored reasoning too: it's keyed by id() of the now-freed assistant dicts, and a
        # later turn could reuse a freed address, folding this conversation's thinking into an
        # unrelated turn's `/export --thinking`.
        self._reasonings.clear()
        self.ctx_used = None
        self._refresh_status()

    def action_help(self) -> None:
        """Show slash commands + keyboard shortcuts in the transcript."""
        body = Text()
        body.append("commands\n", style="bold #fb7185")
        for cmd, desc in (
            ("/mcp", "MCP servers + tools (alias /tools)"),
            ("/mcp on|off <server>", "enable / disable a server's tools"),
            ("/mcp reconnect", "respawn the MCP servers"),
            ("/copy", "copy the last reply"),
            ("/model [name]", "switch the running model (or pick from a list)"),
            ("/export [file]", "save the transcript to markdown (chat.md)"),
            ("/export --thinking", "export and include each turn's thinking (folded)"),
            ("/set <param> <value>", "adjust sampling (temperature/top_p/top_k/min_p/repeat_penalty)"),
            ("/clear", "clear the conversation"),
            ("/help", "this help"),
            ("/exit", "quit"),
        ):
            body.append(f"  {cmd}", style="grey66")
            body.append(f"   {desc}\n", style="grey42")
        body.append("\nkeys\n", style="bold #fb7185")
        for key, desc in (("ctrl+p", "command palette"), ("ctrl+y", "copy reply"), ("esc", "stop"), ("ctrl+d", "quit")):
            body.append(f"  {key}", style="grey66")
            body.append(f"   {desc}\n", style="grey42")
        self._post(body)

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_renderable())

    # -- input / generation ----------------------------------------------------

    def action_cancel(self) -> None:
        # ESC stops the current reply and drops anything queued behind it.
        self._queue.clear()
        if self._busy:
            self.workers.cancel_group(self, "gen")
            self._refresh_status()

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        text = message.text.strip()
        if not text:
            return
        if text.startswith("/") or text in ("exit", "quit"):
            self.query_one(ChatInput).text = ""
            self._update_suggestions()
            self._run_command(text)
            return
        if self._switching:
            self.query_one(ChatInput).text = ""
            self.notify("Switching models — hold on.", severity="warning", timeout=2)
            return
        self.query_one(ChatInput).text = ""
        self._update_suggestions()
        # Send now if idle, otherwise queue behind the in-flight reply.
        self._queue.append(message.text)
        self._refresh_status()
        self._pump()

    def _run_command(self, raw: str) -> None:
        """Handle a ``/command`` typed in the input (not sent to the model)."""
        parts = raw.lstrip("/").split()
        name = parts[0].lower() if parts else ""
        arg = parts[1].lower() if len(parts) > 1 else ""
        if name in ("exit", "quit"):
            self.exit()
        elif name in ("mcp", "tools", "t"):
            if arg == "reconnect":
                self.action_reconnect_mcp()
            elif arg in ("on", "enable") and len(parts) > 2:
                self._mcp_toggle(parts[2], enable=True)
            elif arg in ("off", "disable") and len(parts) > 2:
                self._mcp_toggle(parts[2], enable=False)
            else:
                self.action_show_mcp()
        elif name in ("help", "h", "?"):
            self.action_help()
        elif name in ("copy", "y"):
            self.action_copy_reply()
        elif name in ("export", "save"):
            want_thinking = any(p in ("--thinking", "-t") for p in parts[1:])
            file_args = [p for p in parts[1:] if not p.startswith("-")]
            self.action_export(file_args[0] if file_args else None, thinking=want_thinking)
        elif name in ("set", "sampling"):
            if len(parts) >= 3:
                self._set_sampling(parts[1], parts[2])
            else:
                self.action_show_sampling()
        elif name in ("model", "switch"):
            if len(parts) > 1:
                self.action_switch_model(parts[1])
            else:
                self.action_show_models()
        elif name in ("clear", "reset"):
            self.action_clear()
        else:
            self._post(Text(f"unknown command {raw!r} — /help for commands", style="grey50"))

    def _pump(self) -> None:
        """Start the next queued prompt if nothing is generating."""
        if self._busy or not self._queue:
            return
        self._busy = True
        raw_text = self._queue.pop(0)
        self._refresh_status()
        self.run_worker(self._generate(raw_text), group="gen")

    async def _append(self, widget: Static) -> Static:
        await self.query_one("#transcript", VerticalScroll).mount(widget)
        return widget

    def _scroll_end(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    async def _generate(self, raw_text: str) -> None:
        input_w = self.query_one(ChatInput)  # kept enabled so the user can queue more
        try:
            text, imgs, auds, files = attach.split_input_media(raw_text)
            imgs = (self._pending_images or []) + imgs
            auds = (self._pending_audios or []) + auds
            self._pending_images = None
            self._pending_audios = None
            content_text = attach.inline_files(text, files)
            if not content_text and not imgs and not auds:
                return

            user = Text()
            user.append("❯ ", style="bold #fb7185")
            user.append(raw_text or "(attachments)")
            extras = []
            if imgs:
                extras.append(f"{len(imgs)} image")
            if auds:
                extras.append(f"{len(auds)} audio")
            if files:
                extras.append(f"{len(files)} file")
            if extras:
                user.append(f"   [{', '.join(extras)}]", style="dim")
            await self._append(Static(user, classes="user"))

            mark = len(self.messages)
            self.messages.append(
                {"role": "user", "content": agent.user_content(content_text, imgs or None, auds or None)}
            )

            reasoning_body = Static("", classes="reasoning")
            reasoning_box = Collapsible(
                reasoning_body, title="thinking …", collapsed=self._reason_collapsed_pref, classes="reasoning-box"
            )
            self._reason_prog[id(reasoning_box)] = self._reason_collapsed_pref  # heim set this state
            reasoning_box.display = False  # shown only if the model streams reasoning
            tools_w = Static("", classes="tools")
            tools_w.display = False
            answer_w = Static(Text(""), classes="answer")
            transcript = self.query_one("#transcript", VerticalScroll)
            await transcript.mount(reasoning_box, tools_w, answer_w)
            self._scroll_end()

            # Animated "thinking" placeholder — a whimsical word + elapsed timer, until the first
            # answer token replaces it (à la Claude Code's "· Moonwalking… (26s · thinking more)").
            think_word = random.choice(_GERUNDS)
            think_start = time.monotonic()
            think_timer: Any = None

            def _think_tick() -> None:
                elapsed = time.monotonic() - think_start
                frame = _SPINNER[int(elapsed / 0.08) % len(_SPINNER)]  # spins independent of the 1s clock
                secs = round(elapsed)
                label = Text(f"{frame} ", style="#fb7185")
                label.append(f"{think_word}… ", style="#fb7185")
                label.append(f"({secs}s{' · thinking more' if secs >= 10 else ''})", style="grey42")
                answer_w.update(label)

            def _stop_think() -> None:
                nonlocal think_timer
                if think_timer is not None:
                    think_timer.stop()
                    think_timer = None

            _think_tick()
            think_timer = self.set_interval(0.08, _think_tick)

            answer_buf: list[str] = []
            reasoning_buf: list[str] = []
            tool_lines: list[str] = []
            last_render = 0.0
            reason_start: float | None = None
            reason_collapsed = False

            def finalize_reasoning() -> None:
                # Once the answer (or tool call) starts, collapse the thinking block
                # under a "thought for Ns" summary the user can re-open. Idempotent.
                nonlocal reason_collapsed
                if reasoning_box.display and not reason_collapsed:
                    reason_collapsed = True
                    elapsed = time.monotonic() - (reason_start or time.monotonic())
                    reasoning_box.title = f"thought for {max(1, round(elapsed))}s"
                    if not reasoning_box.collapsed:  # auto-collapse; record it so it's not read as a user click
                        self._reason_prog[id(reasoning_box)] = True
                        reasoning_box.collapsed = True

            def on_token(tok: str) -> None:
                nonlocal last_render
                _stop_think()  # first answer token — drop the animated placeholder
                finalize_reasoning()
                answer_buf.append(tok)
                now = time.monotonic()
                if now - last_render > 0.08:  # throttle markdown re-render
                    last_render = now
                    answer_w.update(Markdown("".join(answer_buf)))
                    self._scroll_end()

            def on_reasoning(tok: str) -> None:
                nonlocal reason_start
                if reason_start is None:
                    reason_start = time.monotonic()
                    reasoning_box.display = True
                reasoning_buf.append(tok)
                reasoning_body.update(Text("".join(reasoning_buf), style="grey42"))
                self._scroll_end()

            def on_event(kind: str, detail: str) -> None:
                finalize_reasoning()  # a tool call ends the thinking phase too
                marker = "⚙ " if kind == "call" else "↳ "
                tool_lines.append(marker + detail[:200])
                tools_w.display = True
                tools_w.update(Text("\n".join(tool_lines), style="grey50"))
                self._scroll_end()

            def on_usage(usage: dict[str, Any]) -> None:
                total = usage.get("total_tokens")
                if isinstance(total, int):
                    self.ctx_used = total
                else:
                    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if isinstance(pt, int) and isinstance(ct, int):
                        self.ctx_used = pt + ct
                self._refresh_status()

            async def on_confirm(name: str, args: dict[str, Any]) -> bool:
                # Suspend the turn on a modal until the user approves/denies the gated write.
                return await self.push_screen_wait(ConfirmModal(name, args))

            try:
                reply = await agent.run(
                    self._base,
                    self.messages,
                    self._active_toolset(),  # honor any servers the user disabled
                    self._max_tokens,
                    on_event,
                    on_token,
                    on_reasoning=on_reasoning,
                    on_usage=on_usage,
                    temperature=self._sampling.temperature,
                    top_p=self._sampling.top_p,
                    top_k=self._sampling.top_k,
                    min_p=self._sampling.min_p,
                    repeat_penalty=self._sampling.repeat_penalty,
                    model=self._model_target,  # required by mlx-vlm; ignored by llama-server/mlx-lm
                    vision=self._vision,
                    on_confirm=on_confirm,
                    confirm_policy=self._confirm_policy,
                )
            except asyncio.CancelledError:  # ESC: drop the partial turn, keep the session
                _stop_think()
                finalize_reasoning()
                # A stop mid-confirmation leaves the ConfirmModal on the screen stack; close it.
                if isinstance(self.screen, ConfirmModal):
                    self.pop_screen()
                del self.messages[mark:]
                answer_w.update(Text("(canceled)", style="yellow"))
                self._scroll_end()
                return
            except Exception as exc:  # noqa: BLE001 - surface runtime/network errors in the transcript
                _stop_think()
                finalize_reasoning()
                del self.messages[mark:]
                answer_w.update(Text(f"error: {exc}", style="red"))
                self._scroll_end()
                return

            _stop_think()
            finalize_reasoning()  # reasoning-only replies (no answer tokens) still collapse
            # Stash this turn's thinking against its assistant message (agent.run left it last) so
            # `/export --thinking` can include it; the message history itself stays reasoning-free.
            if reasoning_buf and self.messages and self.messages[-1].get("role") == "assistant":
                self._reasonings[id(self.messages[-1])] = "".join(reasoning_buf)
            if (reply or "").strip():
                answer_w.update(Markdown(reply))
            else:
                answer_w.update(Text("(no response)", style="yellow"))
            self._scroll_end()
        finally:
            self._busy = False
            input_w.focus()
            self._refresh_status()
            self._pump()  # drain the next queued prompt, if any


def run_interactive(
    *,
    endpoint: "runtime_mod.RuntimeProc | RemoteEndpoint",
    servers: list[tuple[str | None, list[str], dict[str, str]]],
    system_prompt: str,
    images: list[str],
    audios: list[str],
    max_tokens: int | None,
) -> None:
    """Build and run the chat TUI (blocking) against ``endpoint``.

    A spawned runtime is owned by the TUI from here — it can switch models — and whatever
    it ends on is stopped. A :class:`RemoteEndpoint` is only attached to; it stays running.
    """
    app = ChatApp(
        endpoint=endpoint,
        servers=servers,
        system_prompt=system_prompt,
        images=images,
        audios=audios,
        max_tokens=max_tokens,
    )
    try:
        app.run()
    finally:
        if isinstance(app._endpoint, runtime_mod.RuntimeProc):
            runtime_mod.stop(app._endpoint)  # the current model's runtime (may have been swapped)
