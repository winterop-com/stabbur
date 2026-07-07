"""Transparent OpenAI /v1 proxy to the loaded runtime."""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from kodo.routers.serving._base import (  # shared router + request deps
    _DROP_HEADERS,
    HttpDep,
    ManagerDep,
    _acquire_runtime,
    _release_runtime,
    router,
)


@router.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def proxy_v1(path: str, request: Request, manager: ManagerDep, client: HttpDep) -> StreamingResponse:
    """Stream-proxy OpenAI `/v1/*` calls to the loaded runtime.

    A manual ``StreamingResponse`` (rather than a yielding path op) is used
    deliberately: a transparent proxy must forward the upstream status code and
    headers (e.g. ``text/event-stream`` for streaming), which the yield form
    cannot set. Bytes are forwarded verbatim, so SSE deltas stream through live.
    """
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
    # Reserve the runtime so a load/unload can't swap/kill it mid-proxy; read the URL
    # under the reservation (and re-check a model is loaded) and release it when the
    # proxied stream finishes.
    await _acquire_runtime(request)
    try:
        if manager.current is None:
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
