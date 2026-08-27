"""Transparent OpenAI /v1 proxy to the loaded runtime."""

import json
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from stabbur import library as library_ops
from stabbur.routers.serving._base import (  # shared router + request deps
    _DROP_HEADERS,
    HttpDep,
    ManagerDep,
    _acquire_runtime,
    _release_runtime,
    router,
)


def _library_as_models() -> Response:
    """The local library as an OpenAI ``/v1/models`` list.

    Local mode has no runtime to forward to until a model is loaded, but the library IS the
    answer to "what can I ask for" — so list it rather than 409. A client that then sends a
    completion still gets a clear "No model loaded"; discovery and use are different questions.
    """
    now = int(time.time())
    data = [
        {"id": m.name, "object": "model", "created": now, "owned_by": "stabbur"}
        for m in library_ops.scan()
        if m.generative and not m.is_ollama
    ]
    return Response(
        content=json.dumps({"object": "list", "data": data}),
        media_type="application/json",
    )


@router.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def proxy_v1(path: str, request: Request, manager: ManagerDep, client: HttpDep) -> Response:
    """Stream-proxy OpenAI `/v1/*` calls to the loaded runtime.

    A manual ``StreamingResponse`` (rather than a yielding path op) is used
    deliberately: a transparent proxy must forward the upstream status code and
    headers (e.g. ``text/event-stream`` for streaming), which the yield form
    cannot set. Bytes are forwarded verbatim, so SSE deltas stream through live.
    """
    # `GET /v1/models` is how an OpenAI client DISCOVERS what it may ask for, so refusing it
    # until something is loaded is a chicken-and-egg: the client cannot choose a model before it
    # can list one, and the docs tell people to point any OpenAI client at this base URL. Answer
    # it without a loaded model — every other path still requires one.
    discovery = path == "models" and request.method == "GET"
    if discovery and manager.current is None and not manager.is_upstream:
        return _library_as_models()

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
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

    async def relay() -> AsyncGenerator[bytes, None]:
        # Hold the reservation for the whole proxied stream; release + close upstream
        # when it ends (or the client disconnects and Starlette closes the generator).
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            # Release even if aclose() raises — otherwise the reservation leaks and every
            # subsequent load/unload 409s permanently (V-10).
            try:
                await upstream.aclose()
            finally:
                _release_runtime(request)

    return StreamingResponse(relay(), status_code=upstream.status_code, headers=resp_headers)
