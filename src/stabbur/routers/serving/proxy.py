"""Transparent OpenAI /v1 proxy to the loaded runtime.

Transparent, with two deliberate exceptions, both of them **locked mode** (``serve --model``):

- The lock is enforced *here* as well as on ``/api/load``. Without that, ``/v1`` was a way
  around it: a client could list every id the backend serves and name a different one, and an
  upstream router would happily hot-swap to it — evicting the model the server is locked to.
- The id a locked server presents is stabbur's model name, not the backend's own. llama.cpp
  answers with the model's absolute path, which leaks the library location, differs between
  the local and upstream backends, and is not the name any stabbur client (the extension,
  ``stabbur chat --server``) knows the model by.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from stabbur import library as library_ops
from stabbur.backends import Backends
from stabbur.config import Settings
from stabbur.routers.serving._base import (  # shared router + request deps
    _DROP_HEADERS,
    _DROP_REQUEST_HEADERS,
    ConfDep,
    HttpDep,
    ManagerDep,
    _acquire_runtime,
    _release_runtime,
    router,
)

# ``/v1`` POSTs that carry a ``model`` even when the caller left it out. Pinning those is what
# stops a locked server from being asked for a different model; anything else is only rewritten
# when it already names one, so a body shape we don't know isn't handed a field it never had.
_MODEL_BEARING = frozenset({"chat/completions", "completions", "embeddings", "rerank"})


def _models_response(names: list[str]) -> Response:
    """An OpenAI ``/v1/models`` listing over ``names``."""
    now = int(time.time())
    data = [{"id": name, "object": "model", "created": now, "owned_by": "stabbur"} for name in names]
    return Response(content=json.dumps({"object": "list", "data": data}), media_type="application/json")


def _library_as_models() -> Response:
    """The local library as an OpenAI ``/v1/models`` list.

    Local mode has no runtime to forward to until a model is loaded, but the library IS the
    answer to "what can I ask for" — so list it rather than 409. A client that then sends a
    completion still gets a clear "No model loaded"; discovery and use are different questions.

    Blocking (``library.scan()`` walks the library roots), so callers hand it to a thread.
    """
    return _models_response([m.name for m in library_ops.scan() if m.generative and not m.is_ollama])


def _locked_name(settings: Settings, manager: Backends) -> str | None:
    """The stabbur-facing name of the ONE model a locked server serves; ``None`` when unlocked.

    The *public* half of the locked-mode id translation. In upstream mode ``manager.current``
    is the remote's own id (a path, on a llama-server in router mode), so the lock's configured
    name is the stable one; locally the loaded library model's name already is that name, and is
    preferred over the configured spelling because ``--model`` resolves a prefix and
    ``/api/status`` reports the resolved name.
    """
    if settings.serve_model is None:
        return None
    if manager.is_upstream:
        return settings.serve_model
    current = manager.current
    return current.name if current is not None else settings.serve_model


def _forward_name(manager: Backends, public: str) -> str:
    """The id the *backend* knows the locked model by — what a forwarded body must say.

    Upstream: the remote's own id, or the request selects some other model on the router.
    Local: the public name; a single-model ``llama-server`` / ``mlx_lm.server`` ignores the
    field, and there is no second id to translate to.
    """
    if manager.is_upstream and (current := manager.current) is not None:
        return current.name
    return public


def _pin_model(body: bytes, path: str, name: str) -> bytes:
    """Force a forwarded request's ``model`` to ``name``.

    Pinning rather than 409-ing on a mismatch: many OpenAI clients send back whatever id they
    listed (or a hardcoded ``gpt-*``), and a locked server has exactly one answer for all of
    them — so the request is served rather than refused. A body that isn't a JSON object
    (multipart, empty) rides through untouched.
    """
    try:
        payload = json.loads(body) if body else None
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body
    if "model" not in payload and path not in _MODEL_BEARING:
        return body
    payload["model"] = name
    return json.dumps(payload).encode()


def _rename_json(raw: bytes, name: str) -> bytes:
    """Rewrite a JSON object's ``model`` to ``name``; anything else passes through unchanged."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(payload, dict) or "model" not in payload:
        return raw
    payload["model"] = name
    return json.dumps(payload).encode()


def _rename_sse(line: bytes, name: str) -> bytes:
    """Rewrite the ``model`` in one SSE ``data:`` frame (``data: [DONE]`` and comments pass through)."""
    if not line.startswith(b"data:"):
        return line
    data = line[len(b"data:") :].strip()
    if not data.startswith(b"{"):
        return line
    return b"data: " + _rename_json(data, name)


async def _renamed(source: AsyncIterator[bytes], name: str, content_type: str) -> AsyncGenerator[bytes, None]:
    """Relay ``source``, presenting the locked model under its stabbur ``name``.

    Two body shapes carry a ``model`` back: an SSE stream of deltas and a single JSON document.
    SSE is rewritten frame by frame (line-buffered, so a field split across two network chunks is
    still seen whole) and keeps streaming live; a JSON document is buffered — it is one object,
    there is nothing to stream — and rewritten once. Any other content type is forwarded verbatim.
    """
    if content_type.startswith("text/event-stream"):
        buffer = b""
        async for chunk in source:
            buffer += chunk
            while (cut := buffer.find(b"\n")) != -1:
                line, buffer = buffer[:cut], buffer[cut + 1 :]
                yield _rename_sse(line, name) + b"\n"
        if buffer:
            yield _rename_sse(buffer, name)
    elif content_type.startswith("application/json"):
        body = b""
        async for chunk in source:
            body += chunk
        yield _rename_json(body, name)
    else:
        async for chunk in source:
            yield chunk


def _error_frame(exc: Exception) -> bytes:
    """An OpenAI-shaped error as a final SSE frame (deliberately not followed by ``[DONE]``)."""
    payload = {
        "error": {
            "message": f"upstream stream failed: {exc}",
            "type": "upstream_error",
            "code": "upstream_error",
        }
    }
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


async def proxy_v1(
    path: str, request: Request, manager: Backends, client: httpx.AsyncClient, settings: Settings
) -> Response:
    """Stream-proxy one OpenAI `/v1/*` call to the loaded runtime (shared by both methods).

    A manual ``StreamingResponse`` (rather than a yielding path op) is used
    deliberately: a transparent proxy must forward the upstream status code and
    headers (e.g. ``text/event-stream`` for streaming), which the yield form
    cannot set. Bytes are forwarded verbatim, so SSE deltas stream through live.
    """
    locked = _locked_name(settings, manager)
    # `GET /v1/models` is how an OpenAI client DISCOVERS what it may ask for, so refusing it
    # until something is loaded is a chicken-and-egg: the client cannot choose a model before it
    # can list one, and the docs tell people to point any OpenAI client at this base URL. Answer
    # it without a loaded model — every other path still requires one.
    discovery = path == "models" and request.method == "GET"
    if discovery and locked is not None:
        # A locked server serves exactly one model, so that is the whole listing — the backend's
        # own (which on an upstream router is every model on the host) is not this server's answer.
        return _models_response([locked])
    if discovery and manager.current is None and not manager.is_upstream:
        return await asyncio.to_thread(_library_as_models)

    body = await request.body()
    if locked is not None and request.method == "POST":
        body = _pin_model(body, path, _forward_name(manager, locked))
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
    # Reserve the runtime so a load/unload can't swap/kill it mid-proxy; read the URL
    # under the reservation (and re-check a model is loaded) and release it when the
    # proxied stream finishes.
    await _acquire_runtime(request)
    try:
        # Discovery is exempt: in upstream mode the remote answers /v1/models whether or not
        # a model is selected here, and a client must be able to list before it can choose.
        if manager.current is None and not discovery:
            raise HTTPException(status_code=409, detail="No model loaded")
        req = client.build_request(
            request.method,
            f"{manager.base_url}/v1/{path}",
            content=body,
            headers=headers,
            params=request.query_params,
        )
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        _release_runtime(request)
        raise HTTPException(status_code=502, detail=f"runtime unreachable: {exc}") from exc
    except BaseException:
        _release_runtime(request)
        raise

    resp_headers: dict[str, Any] = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_HEADERS}
    content_type = upstream.headers.get("content-type", "")
    source: AsyncIterator[bytes] = upstream.aiter_raw()
    if locked is not None:
        source = _renamed(source, locked, content_type)
    is_sse = content_type.startswith("text/event-stream")

    async def relay() -> AsyncGenerator[bytes, None]:
        # Hold the reservation for the whole proxied stream; release + close upstream
        # when it ends (or the client disconnects and Starlette closes the generator).
        try:
            async for chunk in source:
                yield chunk
        except httpx.HTTPError as exc:
            # The runtime died mid-reply. Ending the stream here would be indistinguishable from
            # a finished one — no [DONE], but a tolerant parser reads a clean close as completion
            # and shows the truncated answer as the model's whole answer. Say so instead: an
            # OpenAI-shaped error frame for SSE, and for any other body an abnormal close (the
            # half-written JSON is unparseable anyway, and an appended error object would only
            # make it invalid in a new way).
            if not is_sse:
                raise
            yield _error_frame(exc)
        finally:
            # Release even if aclose() raises — otherwise the reservation leaks and every
            # subsequent load/unload 409s permanently (V-10).
            try:
                await upstream.aclose()
            finally:
                _release_runtime(request)

    return StreamingResponse(relay(), status_code=upstream.status_code, headers=resp_headers)


# Registered per method rather than as one ``api_route(methods=["GET", "POST"])``: that form gives
# both methods a single operation id, which is a duplicate in the OpenAPI schema (a startup
# UserWarning, and a generated client with one of the two silently missing).
@router.get("/v1/{path:path}", operation_id="proxy_v1_get")
async def proxy_v1_get(
    path: str, request: Request, manager: ManagerDep, client: HttpDep, settings: ConfDep
) -> Response:
    """Proxy a GET to the loaded runtime (see :func:`proxy_v1`)."""
    return await proxy_v1(path, request, manager, client, settings)


@router.post("/v1/{path:path}", operation_id="proxy_v1_post")
async def proxy_v1_post(
    path: str, request: Request, manager: ManagerDep, client: HttpDep, settings: ConfDep
) -> Response:
    """Proxy a POST to the loaded runtime (see :func:`proxy_v1`)."""
    return await proxy_v1(path, request, manager, client, settings)
