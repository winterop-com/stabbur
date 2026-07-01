"""Browse and pull models over HTTP."""

from fastapi import APIRouter, HTTPException

from kodo import catalog
from kodo.models import Catalog, ModelSource, PullResult

router = APIRouter(prefix="/models", tags=["models"])


# These handlers are deliberately sync (`def`, not `async def`): catalog scans
# filesystem trees and pulls do large disk copies / snapshot_download. FastAPI runs
# sync path operations in a worker thread, so they never block the event loop and
# stall unrelated status/proxy traffic.
@router.get("")
def list_models(source: ModelSource | None = None) -> Catalog:
    """List discovered models, optionally filtered to a single source."""
    return catalog.list_models(source)


@router.post("/{source}/pull")
def pull_model(source: ModelSource, name: str) -> PullResult:
    """Pull a single model from ``source`` into the library.

    Args:
        source: Which source the model belongs to.
        name: Model identifier (HF repo id, Ollama ``model:tag``, or LM Studio path).

    Returns:
        Details of what was written.
    """
    try:
        return catalog.pull(source, name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface download/copy failures to the client
        raise HTTPException(status_code=500, detail=str(exc)) from exc
