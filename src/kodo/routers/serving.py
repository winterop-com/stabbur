"""Model lifecycle, server-side chat (agent loop + MCP), and OpenAI `/v1` proxy."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from kodo import agent, capabilities, cards, doctor, kokoro, runtime, sampling, tts
from kodo import library as library_ops
from kodo.config import Settings
from kodo.sampling import ModelSampling
from kodo.server import ServerManager
from kodo.tools import MCPToolset

router = APIRouter(tags=["serving"])

# Hop-by-hop headers that must not be forwarded through the proxy.
_DROP_HEADERS = {"content-length", "transfer-encoding", "connection", "host"}


class ServerStatus(BaseModel):
    """Current runtime status for the UI."""

    state: str
    model: str | None = None
    locked: bool = False
    n_ctx: int | None = None  # context window the current model was loaded with (None = runtime default)
    error: str | None = None  # why the runtime died (stderr tail), if it exited unexpectedly
    default_system_prompt: str = ""  # the project (kodo.toml) system prompt, so the UI can prefill/show it


class LibraryModelInfo(BaseModel):
    """A runnable library model, for the UI's model picker."""

    name: str
    model_format: str
    size_bytes: int
    size_human: str
    vision: bool = False
    audio: bool = False
    tools: bool = False
    context_length: int | None = None


def get_manager(request: Request) -> ServerManager:
    """Dependency: the app's singleton runtime manager."""
    manager: ServerManager = request.app.state.manager
    return manager


def get_http(request: Request) -> httpx.AsyncClient:
    """Dependency: the app's shared HTTP client (created in lifespan)."""
    client: httpx.AsyncClient = request.app.state.http
    return client


def get_conf(request: Request) -> Settings:
    """Dependency: the app's configured settings (not the global cache)."""
    settings: Settings = request.app.state.settings
    return settings


ManagerDep = Annotated[ServerManager, Depends(get_manager)]
HttpDep = Annotated[httpx.AsyncClient, Depends(get_http)]
ConfDep = Annotated[Settings, Depends(get_conf)]


async def _status(manager: ServerManager, settings: Settings, system_prompt: str = "") -> ServerStatus:
    current = manager.current
    return ServerStatus(
        state=(await manager.state()).value,
        model=current.name if current else None,
        locked=settings.serve_model is not None,
        n_ctx=manager.n_ctx,
        error=manager.last_error if current is None else None,
        default_system_prompt=system_prompt,
    )


@router.get("/api/status")
async def status(manager: ManagerDep, settings: ConfDep, request: Request) -> ServerStatus:
    """Report the loaded model and runtime state."""
    return await _status(manager, settings, getattr(request.app.state, "system_prompt", "") or "")


@router.get("/api/library")
def library() -> list[LibraryModelInfo]:
    """List runnable (generative) library models for the UI's picker.

    Sync (``def``) so the filesystem scan runs in a worker thread, off the loop.
    """
    out: list[LibraryModelInfo] = []
    for m in library_ops.scan():
        if not m.generative or m.is_ollama:
            continue
        caps = capabilities.capabilities(m)
        out.append(
            LibraryModelInfo(
                name=m.name,
                model_format=m.model_format.value,
                size_bytes=m.size_bytes,
                size_human=m.size_human,
                vision=caps.vision,
                audio=caps.audio,
                tools=caps.tools,
                context_length=caps.context_length,
            )
        )
    return out


class ModelCardInfo(BaseModel):
    """A model's card + metadata for the UI's info panel."""

    name: str
    model_format: str
    size_human: str
    path: str
    card: str | None = None
    metadata: dict[str, Any] | None = None
    sampling: ModelSampling = ModelSampling()  # model-recommended defaults (for UI placeholders)


@router.get("/api/model")
def model_info(name: str) -> ModelCardInfo:
    """Return the model card (README/model-card.md) + metadata for a library model."""
    matches = library_ops.find(name)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No library model matches {name!r}")
    m = matches[0]
    card_text: str | None = None
    card_path = cards.find_card(m.path) or (m.path / cards.SIDECAR_DIR / "model-card.md")
    if card_path.is_file():
        try:
            card_text = card_path.read_text(errors="replace")[:100_000]  # cap huge READMEs
        except OSError:
            card_text = None
    metadata: dict[str, Any] | None = None
    meta_path = m.path / cards.SIDECAR_DIR / "metadata.json"
    if meta_path.is_file():
        try:
            metadata = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            metadata = None
    return ModelCardInfo(
        name=m.name,
        model_format=m.model_format.value,
        size_human=m.size_human,
        path=str(m.path),
        card=card_text,
        metadata=metadata,
        sampling=sampling.recommended(m),
    )


class ToolInfo(BaseModel):
    """One MCP tool exposed to the UI (namespaced ``<server>__<tool>``)."""

    name: str
    server: str
    tool: str
    description: str


@router.get("/api/doctor")
def doctor_report(settings: ConfDep) -> doctor.DoctorReport:
    """System health: runtime binaries, library, and the current project.

    Sync (``def``) so the filesystem scan runs in a worker thread, off the loop.
    Mirrors the ``kodo doctor`` CLI so the UI can show the same status.
    """
    return doctor.run_checks(settings)


@router.get("/api/tools")
def tools(request: Request) -> list[ToolInfo]:
    """List the MCP tools attached to this server (empty if none configured)."""
    toolset: MCPToolset | None = getattr(request.app.state, "toolset", None)
    if toolset is None:
        return []
    out: list[ToolInfo] = []
    for schema in toolset.schemas:
        fn = schema["function"]
        name = fn["name"]
        server, _, tool = name.partition("__")
        # Descriptions come from tool docstrings; strip backtick markup (RST/markdown
        # inline literals) so it reads as prose in the UI (rendered as plain text).
        desc = fn.get("description", "").replace("`", "")
        out.append(ToolInfo(name=name, server=server or "mcp", tool=tool or name, description=desc))
    return out


class ChatRequest(BaseModel):
    """A chat turn for the server-side agent loop."""

    messages: list[dict[str, Any]]
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    use_tools: bool = True  # off → don't attach MCP tools (for non-tool-trained models)
    enabled_tools: list[str] | None = None  # None → all tools; else only these namespaced names
    # Authoritative system prompt: a string (incl. "" for *no* system prompt) overrides
    # the project default; None (field absent) falls back to it. Lets a roleplay model
    # run with no assistant framing instead of being forced into "I'm an AI" refusals.
    system_prompt: str | None = None


@router.post("/api/chat")
async def chat(req: ChatRequest, manager: ManagerDep, request: Request) -> StreamingResponse:
    """Run the agent loop (MCP tools + the loaded model) and stream typed SSE.

    Events: ``{"type":"token","text":...}``, ``{"type":"tool","kind":"call"|"result",
    "detail":...}``, ``{"type":"error","detail":...}``, ``{"type":"done"}``. Unlike
    the raw ``/v1`` proxy, this executes tool calls server-side so the web UI and
    extension get tools — and surfaces tool activity the proxy can't.
    """
    current = manager.current
    if current is None:
        raise HTTPException(status_code=409, detail="No model loaded")
    model_target = current.load_target
    # Model-recommended sampling (LM Studio parity); an explicit request value wins.
    rec = sampling.recommended(current)
    eff_temperature = req.temperature if req.temperature is not None else rec.temperature
    eff_top_p = req.top_p if req.top_p is not None else rec.top_p
    # use_tools off → empty toolset (non-tool-trained models otherwise regurgitate
    # the injected tool schema as text instead of calling tools).
    toolset: MCPToolset = (
        (getattr(request.app.state, "toolset", None) or MCPToolset()) if req.use_tools else MCPToolset()
    )
    # An explicit allow-list narrows the toolset to the tools the user left enabled.
    if req.enabled_tools is not None:
        toolset = toolset.subset(set(req.enabled_tools))
    base = manager.base_url

    # System prompt precedence: an explicit ``system_prompt`` from the client is
    # authoritative — including "" for *no* system prompt (a roleplay model then
    # runs with no assistant framing). Only when the field is absent (None) do we
    # fall back to the project (kodo.toml) prompt. A system message already in
    # ``messages`` still wins (kept for API clients that inline their own).
    if req.system_prompt is not None:
        system_prompt = req.system_prompt
    else:
        system_prompt = getattr(request.app.state, "system_prompt", "") or ""
    messages = list(req.messages)
    if system_prompt and not (messages and messages[0].get("role") == "system"):
        messages = [{"role": "system", "content": system_prompt}, *messages]

    async def events() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        done = {"type": "done"}

        def on_event(kind: str, detail: str) -> None:
            queue.put_nowait({"type": "tool", "kind": kind, "detail": detail[:2000]})

        def on_token(text: str) -> None:
            queue.put_nowait({"type": "token", "text": text})

        def on_reasoning(text: str) -> None:
            queue.put_nowait({"type": "reasoning", "text": text})

        async def produce() -> None:
            try:
                await agent.run(
                    base,
                    messages,
                    toolset,
                    req.max_tokens,
                    on_event,
                    on_token,
                    on_reasoning=on_reasoning,
                    temperature=eff_temperature,
                    top_p=eff_top_p,
                    top_k=rec.top_k,
                    min_p=rec.min_p,
                    repeat_penalty=rec.repeat_penalty,
                    # mlx-vlm requires the OpenAI ``model`` field match what it loaded
                    # (the launch path); harmless for llama-server / mlx-lm.
                    model=str(model_target) if model_target else None,
                )
            except Exception as exc:  # noqa: BLE001 - surface any runtime/tool failure to the client
                queue.put_nowait({"type": "error", "detail": str(exc)})
            finally:
                queue.put_nowait(done)

        task = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is done:
                    break
                yield f"data: {json.dumps(item)}\n\n"
            yield 'data: {"type": "done"}\n\n'
        finally:
            if not task.done():
                task.cancel()  # client disconnected → cancel the in-flight generation

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/api/load/{name:path}")
async def load(name: str, manager: ManagerDep, settings: ConfDep, n_ctx: int | None = None) -> ServerStatus:
    """Load (or switch to) a model by name; rejected in locked mode.

    ``n_ctx`` sets the context window (GGUF/llama.cpp only); changing it reloads
    the model since context is fixed at load time.
    """
    if settings.serve_model is not None:
        raise HTTPException(status_code=409, detail="Server is locked to a single model")
    if n_ctx is not None and n_ctx < 1:
        raise HTTPException(status_code=422, detail="n_ctx must be a positive integer")
    matches = library_ops.find(name)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No library model matches {name!r}")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"{name!r} is ambiguous across formats")
    reason = runtime.runnable_error(matches[0])
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    try:
        manager.load(matches[0], n_ctx)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return await _status(manager, settings)


class TTSModelInfo(BaseModel):
    """A library TTS model, for the UI's voice picker."""

    name: str
    languages: list[str] = []
    size_human: str


@router.get("/api/tts")
def tts_models() -> list[TTSModelInfo]:
    """List library text-to-speech models (empty if none pulled)."""
    return [TTSModelInfo(name=m.name, languages=m.languages, size_human=m.size_human) for m in library_ops.tts_models()]


class VoiceInfo(BaseModel):
    """A selectable voice for the Listen picker, spanning both TTS engines."""

    id: str
    """Voice id: ``kokoro:<name>``, ``oute`` (default), or ``oute:<model>``."""
    label: str
    engine: str  # "kokoro" | "oute"
    language: str = ""
    gender: str = ""


@router.get("/api/voices")
def voices() -> list[VoiceInfo]:
    """Every available voice: Kokoro's built-in voices plus OuteTTS (llama-tts).

    Kokoro (the ``tts`` extra) contributes 54 named voices; OuteTTS contributes a
    default plus any library TTS models. Empty if neither engine is installed.
    """
    out: list[VoiceInfo] = []
    if kokoro.available():
        out += [
            VoiceInfo(id=f"kokoro:{v.id}", label=v.name, engine="kokoro", language=v.language, gender=v.gender)
            for v in kokoro.voices()
        ]
    if tts.available():
        out.append(VoiceInfo(id="oute", label="OuteTTS (default)", engine="oute"))
        out += [
            VoiceInfo(id=f"oute:{m.name}", label=m.name.split("/")[-1], engine="oute", language=", ".join(m.languages))
            for m in library_ops.tts_models()
        ]
    return out


class SpeakRequest(BaseModel):
    """Text to synthesize into speech, with an optional voice id."""

    text: str
    voice: str | None = None  # "kokoro:<name>" | "oute" | "oute:<model>"; None → default
    model: str | None = None  # deprecated: a library OuteTTS model name (→ oute:<model>)


def _default_voice() -> str:
    """The voice to use when a request specifies none (Kokoro if installed)."""
    return "kokoro:af_heart" if kokoro.available() else "oute"


@router.post("/api/speak")
async def speak(req: SpeakRequest) -> Response:
    """Text-to-speech: synthesize ``text`` to a WAV via the chosen voice's engine.

    Markdown is reduced to prose first (so syntax/code aren't read aloud). Kokoro
    voices route to the ONNX engine (``tts`` extra); ``oute``/``oute:<model>``
    route to ``llama-tts``. Blocking synthesis runs in a worker thread; returns
    ``audio/wav`` bytes. 503 if the chosen engine isn't installed.
    """
    text = tts.speech_text(req.text)
    if not text:
        raise HTTPException(status_code=422, detail="nothing speakable (only code or formatting)")

    voice = req.voice or (f"oute:{req.model}" if req.model else _default_voice())
    try:
        if voice.startswith("kokoro:"):
            if not kokoro.available():
                raise HTTPException(status_code=503, detail="Kokoro TTS is not installed (make install-tts)")
            wav_path = await asyncio.to_thread(kokoro.synthesize, text, voice.split(":", 1)[1], None)
        else:
            if not tts.available():
                raise HTTPException(status_code=503, detail="llama-tts is not installed (install llama.cpp)")
            model_path = vocoder_path = None
            if voice.startswith("oute:"):
                name = voice.split(":", 1)[1]
                matches = [m for m in library_ops.find(name) if m.tts]
                if not matches:
                    raise HTTPException(status_code=404, detail=f"No TTS model matches {name!r}")
                model_path, vocoder_path = matches[0].load_target, matches[0].vocoder
            wav_path = await asyncio.to_thread(tts.synthesize, text, None, model_path, vocoder_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    data = wav_path.read_bytes()
    wav_path.unlink(missing_ok=True)
    return Response(content=data, media_type="audio/wav")


@router.post("/api/unload")
async def unload(manager: ManagerDep, settings: ConfDep) -> ServerStatus:
    """Eject the loaded model, stopping its runtime process (frees memory).

    Rejected in locked mode (the server is bound to one model). A no-op if
    nothing is loaded.
    """
    if settings.serve_model is not None:
        raise HTTPException(status_code=409, detail="Server is locked to a single model")
    manager.stop()
    return await _status(manager, settings)


@router.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def proxy_v1(path: str, request: Request, manager: ManagerDep, client: HttpDep) -> StreamingResponse:
    """Stream-proxy OpenAI `/v1/*` calls to the loaded runtime.

    A manual ``StreamingResponse`` (rather than a yielding path op) is used
    deliberately: a transparent proxy must forward the upstream status code and
    headers (e.g. ``text/event-stream`` for streaming), which the yield form
    cannot set. Bytes are forwarded verbatim, so SSE deltas stream through live.
    """
    if manager.current is None:
        raise HTTPException(status_code=409, detail="No model loaded")

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
    req = client.build_request(
        request.method,
        f"{manager.base_url}/v1/{path}",
        content=body,
        headers=headers,
        params=request.query_params,
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"runtime unreachable: {exc}") from exc

    resp_headers: dict[str, Any] = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_HEADERS}
    # Close only the upstream response; the shared client lives for the app's lifetime.
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream.aclose),
    )
