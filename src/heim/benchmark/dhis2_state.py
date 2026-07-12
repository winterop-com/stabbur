"""Live DHIS2 read-back + HEIM_ residue sweep for the stateful write benchmark.

The write suite (``tools-dhis2-write``) scores against REAL end-state: after a model drives a
create-then-delete lifecycle, we query the live instance to confirm the objects are gone (see
:func:`heim.benchmark.core.score_tool_stateful`). This module holds the concrete DHIS2 client
(:class:`Dhis2StateReader`, a :class:`~heim.benchmark.core.StateReader`) plus the between-models
residue sweep that deletes any ``HEIM_``-prefixed leftovers so a model that abandons a lifecycle
mid-way can't compound residue across the sweep.

Everything here is best-effort: an unreachable instance yields ``None`` counts / an empty sweep,
never an exception, so a run without a live DHIS2 degrades gracefully. Credentials come from the
``local_basic`` d2w profile (``~/.config/dhis2/profiles.toml``) or environment overrides — the
same instance the suite's bridge writes to.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

#: Every write-suite test object is prefixed with this; the sweep refuses to delete anything else.
HEIM_PREFIX = "HEIM_"

#: Where d2w stores its profiles (base URL + basic-auth credentials).
_PROFILES_PATH = Path.home() / ".config" / "dhis2" / "profiles.toml"

#: Parse ``DHIS2_PROFILE=<name>`` out of a suite server command (e.g. ``env DHIS2_PROFILE=x ...``).
_PROFILE_RE = re.compile(r"\bDHIS2_PROFILE=(\S+)")


class Dhis2Profile(BaseModel):
    """A resolved DHIS2 connection: base URL plus basic-auth credentials."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    username: str
    password: str


def profile_from_servers(servers: list[str]) -> str | None:
    """Pull the ``DHIS2_PROFILE`` name out of a problem's server command list (first match)."""
    for command in servers:
        match = _PROFILE_RE.search(command)
        if match:
            return match.group(1)
    return None


def load_profile(name: str | None, profiles_path: Path | None = None) -> Dhis2Profile | None:
    """Resolve a DHIS2 profile from env overrides, then the d2w profiles file.

    Environment overrides (win over the file so CI can point elsewhere): ``HEIM_BENCH_DHIS2_URL``,
    ``HEIM_BENCH_DHIS2_USER``, ``HEIM_BENCH_DHIS2_PASSWORD``. Otherwise reads
    ``[profiles.<name>]`` (base_url/username/password) from the d2w profiles TOML. Returns ``None``
    if nothing usable is found — the caller then skips the state check / sweep cleanly.
    """
    env_url = os.environ.get("HEIM_BENCH_DHIS2_URL")
    if env_url:
        return Dhis2Profile(
            base_url=env_url,
            username=os.environ.get("HEIM_BENCH_DHIS2_USER", "admin"),
            password=os.environ.get("HEIM_BENCH_DHIS2_PASSWORD", "district"),
        )
    if not name:
        return None
    path = profiles_path or _PROFILES_PATH
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return None
    entry = data.get("profiles", {}).get(name)
    if not isinstance(entry, dict):
        return None
    base_url, username, password = entry.get("base_url"), entry.get("username"), entry.get("password")
    if not (isinstance(base_url, str) and isinstance(username, str) and isinstance(password, str)):
        return None
    return Dhis2Profile(base_url=base_url.rstrip("/"), username=username, password=password)


class Dhis2StateReader:
    """A live DHIS2 read/delete client for verifying and cleaning ``HEIM_`` write residue.

    Best-effort by construction: every request is wrapped so a down or slow instance surfaces as
    ``None`` (reads) / ``False`` (deletes), never an exception. ``delete`` refuses any object whose
    name is not ``HEIM_``-prefixed, so the sweep can only ever touch benchmark test objects.
    """

    def __init__(self, profile: Dhis2Profile, timeout: float = 10.0) -> None:
        """Bind to ``profile``'s base URL + credentials with a per-request ``timeout`` (seconds)."""
        self._base = profile.base_url.rstrip("/")
        self._auth = (profile.username, profile.password)
        self._timeout = timeout

    def _find(self, resource: str, name: str) -> list[dict[str, str]] | None:
        """Objects of ``resource`` whose name ``like``-matches ``name``; ``[{id,name}]`` or None."""
        url = f"{self._base}/api/{resource}.json"
        params = {"filter": f"name:like:{name}", "fields": "id,name", "paging": "false"}
        try:
            response = httpx.get(url, params=params, auth=self._auth, timeout=self._timeout)
            response.raise_for_status()
            rows = response.json().get(resource, [])
        except Exception:  # noqa: BLE001 - best-effort reader: any failure (incl. a malformed base_url
            # raising httpx.InvalidURL, which is NOT an HTTPError) degrades to "unreachable", never crashes.
            return None
        return [{"id": str(r.get("id", "")), "name": str(r.get("name", ""))} for r in rows]

    def count(self, resource: str, name: str) -> int | None:
        """Number of ``resource`` objects matching ``name`` (``StateReader`` protocol); None if down."""
        found = self._find(resource, name)
        return None if found is None else len(found)

    def _delete(self, resource: str, uid: str) -> bool:
        """DELETE one object by UID; True on 2xx, False on any error."""
        try:
            response = httpx.delete(f"{self._base}/api/{resource}/{uid}", auth=self._auth, timeout=self._timeout)
        except Exception:  # noqa: BLE001 - best-effort: any request failure means "not deleted", never crashes
            return False
        return response.is_success

    def sweep(self, resource: str, prefix: str = HEIM_PREFIX) -> list[str]:
        """Delete every ``prefix``-named object of ``resource``; return the UIDs removed.

        Safe: the ``like`` filter is a substring match, so each candidate's name is re-checked to
        actually START with ``prefix`` before deletion. Returns ``[]`` if the instance is
        unreachable or nothing matched.

        Caveat: an object a model *renamed* out of the tracked prefix (instead of deleting it) is
        invisible to both this sweep and the absent-at-end check — suite authors must keep rename
        targets within the ``HEIM_`` prefix (the shipped suite does).
        """
        # Hard invariant: only ever sweep a HEIM_-scoped prefix. An empty / non-HEIM prefix (a future
        # caller's bug) would make ``startswith`` trivially true and turn the safety net into a no-op.
        if not prefix.startswith(HEIM_PREFIX):
            return []
        found = self._find(resource, prefix)
        if not found:
            return []
        removed: list[str] = []
        for obj in found:
            if obj["name"].startswith(prefix) and obj["id"] and self._delete(resource, obj["id"]):
                removed.append(obj["id"])
        return removed


def sweep_types(problems: list[object]) -> list[str]:
    """The distinct DHIS2 resource types a suite's problems create (from their ``expect_absent``)."""
    types: set[str] = set()
    for problem in problems:
        for spec in getattr(problem, "expect_absent", []) or []:
            types.add(spec.type)
    return sorted(types)


def sweep_residue(reader: Dhis2StateReader, resources: list[str], prefix: str = HEIM_PREFIX) -> dict[str, list[str]]:
    """Sweep ``HEIM_`` residue across ``resources``; return ``{resource: [deleted uids]}``.

    Order matters: groups/members reference elements, so sweep group-and-membership types before
    the elements they hold to avoid "still referenced" deletes. We simply sweep the ``*Group`` and
    ``*GroupSet`` resources first, then the rest.
    """
    ordered = sorted(resources, key=lambda r: (0 if "Group" in r else 1, r))
    return {resource: reader.sweep(resource, prefix) for resource in ordered}
