# Full project review — 2026-07-05

> **Remediation status (updated 2026-07-06).** Most of this review has been addressed; the
> findings below are the original audit, kept as the record. Summary of what's **done**:
>
> - **CI**: `.github/workflows/ci.yml` runs `make check` on ubuntu + macos and a frontend
>   tsc/build job on every push/PR — the gate that now enforces the rest.
> - **Serving (V-10..V-16)**: reservation-leak, backpressure, SSE flush, sampling, log-fd, and a
>   **bearer-token auth** + Origin-fallback CSRF guard (V-13/V-14).
> - **Frontend (F-1..F-13)**: all thirteen — persistence, routing, streaming isolation, mic/blob
>   leaks — verified against the running UI (Playwright).
> - **Packages / sandbox (P-M2, P-M3, P-L\*)**: Docker hardening proven live; exec/files/utils/
>   memory/weather fixes.
> - **Architecture**: **A2** (no `./data` default; one guarded `library.default_root()`), **A5**
>   (per-library `flock`), **A4** (process supervisor: group kill + pidfile + orphan sweep,
>   live-proven), **A1** (one `kodo.toml` parser + one validated writer), **A8** (import-time HF
>   constraint documented; serve env-API centralized), **A3** (bounded — see below). **I-8**
>   (benchmark is now an extra).
> - Plus the earlier batches (S-\*, C-\*, N-\*, VO-\*) — see git history.
>
> **A3 (done, bounded).** A3's concrete sub-bugs (C-6/C-12/S-N1/S-H2/N-H1/VO-M2/S-N5) were already
> fixed as instances in earlier batches, so A3's remaining value was structural. Delivered: a
> formal `ModelRef` (name+format) identity that `scan()` dedups on, and a single shared
> fault-isolation helper all bucket scanners funnel through, so one corrupt model on disk is
> skipped, never crashing the listing. The literal "merge all seven scanners into one" was
> deliberately **not** done: the list conflates distinct concerns (source-listing for pull vs
> on-drive library scanning) that shouldn't merge, and the per-bucket decomposition is sound — a
> ground-up merge would be high-risk churn for marginal, already-patched bug-prevention.
>
> **Remaining (deliberately deferred):** **A6** (generalize enforce-at-choke-point beyond the fixed
> instances), **A7** (split the 2.2k-line `cli.py`), **P-H1** (SSRF/DNS-rebinding — only matters if
> exposing beyond a trusted LAN, which isn't the model), and the audio-specialist bug (blocked on a
> small audio GGUF + likely an upstream llama.cpp issue). See `docs/architecture.md`.

A cross-cutting audit of the codebase: bugs, gaps, missing features, and infrastructure
issues, grouped by subsystem and ranked most severe first within each section.
Findings reference `file:line` as of commit `2275545`.

A second, independent pass (same day) verified every finding below against the code —
many empirically, including against the real library on `/Volumes/T9/Library` — and
added new sections at the end: the verification record (with corrections to a handful
of findings), additional findings, a review of the `packages/` workspace (not covered
by the first pass), a review of `CHROME.md`, and an architecture review. Start at
"Verification pass" if you have already read the first half.

## Top issues across the project

1. **LM Studio `pull --move` deletes the source after only an aggregate size check** — not
   the byte-for-byte verification the docstring promises. Data-loss risk on exFAT. (S-H1)
2. **A missing Ollama projector blob crashes every `library.scan()`** — including
   `kodo library verify`, the command meant to diagnose exactly that state. (S-H2)
3. **The `enabled_tools` allow-list is display-only** — a tool disabled in the UI is
   still executed if the model calls it. A consent control that doesn't enforce. (V-1)
4. **`voice.runtime.available()` raises instead of returning False when mlx-audio is
   absent** — every voice gate on Linux / no-extra installs crashes (traceback / 500)
   instead of degrading to the install hint / 503. (VO-H1)
5. **Malformed `kodo.toml` turns every kodo command into a raw traceback** — and
   `kodo mcp add` explicitly tells users to hand-edit that file. (C-1)
6. **`--shared` pulls bypass the strict-library guard** and silently download into
   `./data` when `KODO_LIBRARY_ROOT` is unset — the exact behavior the
   `LibraryNotConfigured` machinery exists to forbid. Same hole in the HTTP pull
   endpoint. (C-2, S-M6)
7. **MCP server connect has no timeout** — a wedged server hangs `kodo serve` startup
   (and the TUI's `on_mount`) forever. (V-2, VO-M1)
8. **mlx-audio synthesis returns only the first segment** — multi-sentence TTS is
   silently truncated to the first sentence. (VO-H2)
9. **No CI at all** — `make check` is a solid gate that nothing enforces, on a repo
   committed to `main` directly. (I-1)
10. **One pasted image can silently kill all web-UI conversation persistence**
    (localStorage quota exceeded; the error is swallowed). (F-1)

---

## Storage layer

`library.py`, `catalog.py`, `tags.py`, `cards.py`, `consumers.py`, `hfcache.py`, `sources/`

### High

- **S-H1. LM Studio `--move` is not byte-for-byte verified.**
  `src/kodo/sources/lmstudio.py:96-103`. The gate before `shutil.rmtree(src)` is a
  comparison of two *summed byte totals* from `dir_stats()` (which also skips
  `.cache`/`.kodo`/`._*`). No per-file comparison, no hashing. A same-size corrupt copy
  (bit-flip or silent write error on the no-journal exFAT drive) passes and the only good
  copy is deleted. Two different file sets with equal totals also pass. On a size
  mismatch the move is *silently* skipped — the user gets "Done" with no indication the
  source was kept. Contrast: the Ollama path sha256-verifies every blob.

- **S-H2. Missing Ollama projector blob crashes the entire scan.**
  `src/kodo/library.py:319` (`_scan_ollama`): `model_blob` is guarded with `is_file()`
  but `mmproj_blob.stat()` is unguarded. A manifest whose `.projector` blob is missing
  (drive corruption, partial backup, manual deletion) raises `FileNotFoundError` out of
  `scan()`, breaking every command that scans — including `verify` itself.

### Medium

- **S-M1. `copy_tree` can destroy the previous good backup on double failure.**
  `src/kodo/sources/base.py:96-107`. If the publish rename fails and the rollback rename
  also fails, the `finally: shutil.rmtree(tmp, ignore_errors=True)` deletes `previous` —
  the pre-existing good copy — along with the staging dir. Narrow window but real on
  transient IO/permission errors at publish time.

- **S-M2. Migration "dedup" deletes the `huggingface/` copy on mere existence of the
  bucket dir.** `src/kodo/library.py:503,527`. If `<format>/<repo>` exists but is a
  partial/interrupted pull (even empty), `apply_migration` deletes the complete
  `huggingface/` copy — data loss. Also a plan-to-apply race: a `dest` created in
  between makes `shutil.move` nest `src` inside it (`gguf/pub/Repo/Repo/...`).

- **S-M3. `verify` misses truncated and multi-shard-incomplete weights.**
  `src/kodo/library.py:551-568`. Only the single GGUF `load_target` is checked, and only
  for existence + zero size. A split model verifies "ok" with shards 2-5 deleted; a weight
  truncated to any non-zero size passes. The sidecar records `size_bytes`/`file_count`
  at pull time but `verify` never cross-checks them — a free truncation detector unused.
  (Cosmetic: `VerifyResult.checked` docstring says `"size+files+card"`; code emits
  `"weights+card"`.)

- **S-M4. `tags.json` read-modify-write has no locking; concurrent savers share one fixed
  temp file.** `src/kodo/tags.py:70-102` (and `save_registry` at 131-138). CLI + running
  `kodo serve --ui` writing concurrently is a classic lost update, and both writers use
  the same `tags.json.tmp`, so interleaved write + replace can publish mixed content.
  No fsync before the rename, so a power cut / unclean exFAT eject can leave an empty
  `tags.json`, which `load()` silently treats as `{}` — all tags gone, no warning.

- **S-M5. No fsync anywhere before `--move` deletes the source.**
  `src/kodo/sources/ollama.py:355-365` and `copy_tree`. Checksums are computed from the
  page cache, validating the copy operation, not the bytes on the platter. With no fsync
  before `remove()`, an unclean eject after `pull --move` loses data reported "verified".
  Given the project explicitly targets no-journaling exFAT, blobs should be fsync'ed
  before the source is deleted.

- **S-M6. HTTP pull endpoint and `catalog.pull` default silently write into `./data`.**
  `src/kodo/routers/catalog.py:22-33` calls `catalog.pull(source, name)` with no root;
  `src/kodo/catalog.py:59` defaults to `get_settings().library_root` = `Path("data")`
  when unconfigured — bypassing `LibraryNotConfigured`. Same for `kodo library pull
  --shared` (`cli.py:976`).

- **S-M7. A SIGKILL'd pull leaves a `.kodo-stage-*` dir that scan surfaces as a phantom
  model.** `src/kodo/sources/base.py:90` stages inside the bucket; cleanup is only in a
  `finally`. `_scan_dirs` (`library.py:241-255`) does not skip dot-prefixed dirs, so the
  next scan lists a bogus model named `.kodo-stage-abc123/Repo`, and nothing ever
  garbage-collects the stranded multi-GB copy.

### Low

- **S-L1.** `library.remove` uses `shutil.rmtree(..., ignore_errors=True)` then reports
  success unconditionally (`src/kodo/library.py:452`) — "Removed ... freed N GB" while the
  files remain (e.g. held open by a running llama-server).
- **S-L2.** Ollama `model:tag` names collide across registries
  (`src/kodo/sources/ollama.py:49-58,189-194`); `remove`/`pull` act on an arbitrary match.
- **S-L3.** `hfcache.configure` sets `HF_HOME` (not `HF_HUB_CACHE`), relocating the HF
  token path (`src/kodo/hfcache.py:53`) — a `huggingface-cli login` token stops being
  found; gated-repo pulls silently lose auth; later logins write the credential onto the
  shared external drive. Also the redirected hub cache on exFAT degrades hf_hub's
  symlink layout to full blob duplication.
- **S-L4.** `search` size estimates assume a one-quant pull
  (`src/kodo/sources/huggingface.py:23-44`) but plain `pull` with no `--include`
  downloads the whole repo — "4.5 GB" in search, 200 GB pulled.
- **S-L5.** `build_modelfile` only escapes `"` (`src/kodo/consumers.py:84-86`); a
  multi-line system prompt produces an invalid Modelfile (needs `"""` block syntax).

### Missing features / gaps

1. No content checksums for format-bucket models — only Ollama blobs are
   content-addressed; recording per-file sha256 at pull time (free via the Hub API)
   would give `verify` real integrity checking.
2. No garbage collection / repair command for stranded `.kodo-stage-*` dirs, `.partial`
   blobs, empty publisher dirs.
3. Removal doesn't clean derived state: stale `tags.json` entries, dangling
   `install_lmstudio` symlinks.
4. No `--move` for the HF source (`catalog.py:66-67` raises `NotImplementedError`) —
   the biggest local-disk consumer has no reclaim path.
5. No cross-process safety for library mutations (CLI + serve pulling/removing/migrating
   the same model concurrently).

---

## CLI and config layer

`cli.py`, `config.py`, `project.py`, `doctor.py`, `plugins.py`, `templates.py`, `arch.py`

### High

- **C-1. Malformed `kodo.toml` crashes every command with a raw traceback.**
  `src/kodo/project.py:48` — `tomllib.loads` with no error handling; a `TOMLDecodeError`,
  a `[[mcp]]` block missing `command` (KeyError at line 59), or a wrong-typed value
  (pydantic `ValidationError`) all propagate uncaught. `project.load()` runs on nearly
  every command; `main()` (`cli.py:2156`) catches only `LibraryNotConfigured`. One typo
  while hand-editing — which `kodo mcp add` explicitly instructs — breaks every
  invocation. No test covers a malformed manifest.

- **C-2. `--shared` pulls silently target `./data`, violating the strict-library rule.**
  `src/kodo/cli.py:976` (`pull`) and `cli.py:2044` (`voice import`): the `--shared`
  branch reads `get_settings().library_root` directly (default `Path("data")`),
  bypassing the `LibraryNotConfigured` guard in `library.roots()`. In a project with a
  local library but no `KODO_LIBRARY_ROOT`, `--shared` downloads gigabytes into `./data`.
  Same latent pattern in `_pull_voice_all` (`cli.py:865`). No test covers `--shared`.

### Medium

- **C-3. `kodo doctor` self-destructs in the exact misconfiguration it diagnoses.**
  `src/kodo/doctor.py:157` — `check_project` calls `library_ops.find`, which raises
  `LibraryNotConfigured`, aborting `run_checks` (`doctor.py:177`) before the report —
  including the purpose-built "not configured" check — is rendered.

- **C-4. `kodo serve` with a bad locked model dies with a uvicorn traceback.**
  `src/kodo/cli.py:1930-1933` sets `KODO_SERVE_MODEL` without resolving it; validation
  happens only in the app lifespan (`src/kodo/app.py:61-70`) as a `RuntimeError` →
  "Application startup failed" + stack trace after the pretty banner. `chat` validates
  via `_resolve_library_model` (`cli.py:1730`); `serve` should too, before `uvicorn.run`.

- **C-5. Doctor hints reference commands that don't exist.**
  `src/kodo/doctor.py:129` says `kodo pull` / `kodo sources`; both are registered only
  under the `library` group (`cli.py:926,997`). Pasting the doctor's own remediation
  yields "No such command". Related: `project show` (`cli.py:1398`) suggests
  "kodo project init" for a missing model, which would re-scaffold rather than pull.

### Low

- **C-6.** `library verify` dedupes by `(name, format)` (`library.py:399-408`), so a
  truncated shared-drive copy passes verify when a healthy project-local copy shadows
  it — while `rm` deliberately uses `find_copies` to hit all copies (`cli.py:645`).
- **C-7.** `library install --to ollama` silently overrides an explicit `--format mlx`
  (`cli.py:744`); should reject the contradictory flag.
- **C-8.** `voice speak --play` is silently ignored when `-o` is given (`cli.py:1652`);
  `--voice` silently wins over a simultaneously-passed `--model` (`cli.py:1599`).
- **C-9.** `voice import --all` silently ignores explicitly-passed model ids
  (`cli.py:2045-2047`), where `library pull` rejects the same combination with exit 2.
- **C-10.** Not-found errors print `get_settings().library_root` (`cli.py:1493,1514`) —
  `data` when unset, or the shared root when the models actually live in project-local
  libraries. Should show `library_ops.roots()`.
- **C-11.** Dead defensive `try/except` around `capabilities()` (documented
  never-raises) at `cli.py:277-280,608-611,2136-2139`.
- **C-12.** `library rm` leaves stale entries in `tags.json` and the tag registry
  (`library.py:435-453`); a later re-pull silently inherits the old tags.

### Test coverage gaps (CLI)

- `kodo library rm` — the destructive-by-default command — has zero CLI tests
  (confirmation prompt, `--yes`, ambiguity guard, multi-copy removal, `cli.py:633-670`).
  The ambiguity hint ("pass --format", `cli.py:654`) is also wrong when two publishers
  share a bare name in the same format.
- No test asserts the clean `main()` message when `KODO_LIBRARY_ROOT` is unset; nothing
  would have caught C-2.
- No malformed-`kodo.toml` test anywhere.
- `kodo doctor` has no CLI-level test (test_doctor.py stubs `library.find`).
- `library migrate --apply`, `library install`, `mcp add` (including
  `_add_pyproject_dep` regex surgery, `cli.py:431-453`), and `tag`/`tag-style` are
  untested at the CLI level.

### Missing features / gaps

1. No `kodo mcp remove` — `mcp add` appends `[[mcp]]` blocks and pins pyproject deps;
   the only way back is hand-editing two files.
2. No `--json` output on `library ls`/`sources`/`verify`, `doctor`, `project show` —
   scripting has to scrape ANSI tables.
3. No upward search for `kodo.toml` — running kodo from a project subdirectory silently
   loses model/tools/library context (unlike git/cargo/uv).
4. `library migrate --apply` deletes duplicate dirs with no confirmation prompt
   (`rm` sets the precedent of confirming deletions).
5. `library install` has no inverse (`uninstall`) and no inventory of which models are
   installed into which consumers.

---

## Serving and agent layer

`runtime.py`, `server.py`, `app.py`, `routers/serving.py`, `agent.py`, `tools.py`,
`mcp_catalog.py`, `capabilities.py`, `sampling.py`

### High

- **V-1. `MCPToolset.subset()` does not restrict execution — the `enabled_tools`
  allow-list is display-only.** `src/kodo/tools.py:119-124` — `subset()` copies the
  full `_owner` map and only filters `schemas`; `call()` (`tools.py:126-137`) routes
  via `_owner`. A user disables a dangerous tool in the UI; the model — which has seen
  the tool name in earlier conversation history or hallucinates it — emits a call and
  `toolset.call()` executes it anyway. Only `use_tools=False` (fresh empty toolset)
  actually enforces. A security/consent control that silently doesn't enforce.

- **V-2. MCP server connect has no timeout — a wedged server hangs `kodo serve`
  startup forever.** `src/kodo/tools.py:164-167` enters `Client(transport)` with no
  `init_timeout`; fastmcp 3.4.2's default `client_init_timeout` is `None` (wait
  forever). A `[[mcp]]` command that starts but never completes the MCP initialize
  handshake blocks lifespan startup indefinitely — uvicorn sits at "waiting for
  application startup", no error. The per-server `try/except` (`tools.py:171`) catches
  failures, not hangs. Violates the "install hint, not a hang" rule for the
  wedged-binary case (the missing-binary case is handled).

- **V-3. Locked-mode lifespan orphans the spawned llama-server if startup fails after
  `manager.load()`.** `src/kodo/app.py:71-91` — the runtime is spawned at line 71;
  `manager.stop()` lives in the `finally` after `yield`. Anything raising between them
  (malformed `kodo.toml`, `[[mcp]]` entry missing `command`, `mcp_tools.connect`
  failure) aborts startup, uvicorn exits, and the multi-GB llama-server stays resident
  holding the runtime port. Compounds with V-2.

### Medium

- **V-4. `/v1` proxy has no read timeout — a hung runtime pins the reservation and
  blocks load/unload indefinitely.** `src/kodo/app.py:121` creates the shared client
  with `timeout=None`; `proxy_v1` (`serving.py:798-829`) holds `active_generations`
  for the whole stream. If llama-server wedges after accepting the connection, every
  `/api/load` / `/api/unload` returns 409 until the client disconnects — no way to
  force-stop from the API.
- **V-5. No orphan protection for the runtime child on hard kill of the parent.**
  `src/kodo/runtime.py:177` and `src/kodo/server.py:147` `Popen` with no process
  group, no atexit, no parent-death watch. SIGKILL/OOM of the CLI leaves a 10-20 GB
  llama-server running and holding the pinned port; the next `kodo serve` load fails
  address-in-use.
- **V-6. Runtime-port TOCTOU with an unbounded window.** `src/kodo/server.py:37` —
  the port is picked at app creation but nothing binds it until the first `/api/load`,
  potentially hours later; a second `kodo serve` can pick the same port. Surfaced only
  as `last_error` after the fact.
- **V-7. Malformed tool-call JSON is silently coerced to `{}` and the tool executed
  anyway.** `src/kodo/agent.py:187-190` — on `JSONDecodeError` the tool runs with
  empty args instead of feeding the parse error back to the model; for tools with
  all-optional params the model gets a plausible-but-wrong result.
- **V-8. Mid-stream runtime death yields silent truncation.** `src/kodo/agent.py:70-104`
  — a connection closing without `[DONE]` ends `aiter_lines()` quietly and the partial
  content is recorded as a complete reply; llama-server's SSE error objects (no
  `choices`) are silently skipped, yielding an empty reply with no explanation.
- **V-9. MLX runtime routing keys on `vision` only — audio-capable MLX checkpoints go
  to text-only `mlx_lm.server`.** `src/kodo/runtime.py:70`; `audio_config` is detected
  (`capabilities.py:197`) but unused for routing. Known/scoped in recent commits —
  listed so it doesn't get lost.

### Low

- **V-10.** Reservation leak if `upstream.aclose()` raises: `_release_runtime` runs
  after the close in `relay()`'s `finally` (`serving.py:822-827`) — an exception skips
  the release and load/unload 409s permanently.
- **V-11.** `/api/chat` releases the reservation without awaiting producer
  cancellation (`serving.py:509-511`) — benign races against the old runtime.
- **V-12.** Unbounded queue in `/api/chat` — no backpressure (`serving.py:450`); a
  slow SSE consumer buffers the whole reply in memory.
- **V-13.** The cross-site guard treats a missing `Sec-Fetch-Site` header as
  non-browser (`app.py:50-51`) — bypassable by Safari < 16.4 and embedded WebViews.
- **V-14.** `--host 0.0.0.0` exposes unauthenticated model control + MCP tool
  execution to the LAN with no warning and no auth token anywhere (`cli.py:1902`).
- **V-15.** `runtime.start()` leaks the log fd + tempdir if `Popen` raises
  (`runtime.py:173-177`).
- **V-16.** `sampling.recommended()` overrides an explicit `repetition_penalty: 1.0`
  to 1.1 (`src/kodo/sampling.py:75`), contradicting "explicit model value wins".

### Verified OK

- Missing runtime binary yields a clean `RuntimeError` with install hint (CLI and
  server); no hang. Agent loop is bounded (`max_rounds=8`, 120s per-tool timeout).
- Wildcard CORS does not exempt mutating calls; default bind is 127.0.0.1.
- Tool-marker detection requires a tool-calling marker (not bare "tools");
  mmproj vision/audio disambiguation correct.
- `manager.current` reaps dead children; load/stop thread-serialized; proxy uses
  `aiter_raw()` correctly; graceful shutdown stops the runtime.

### Test coverage gaps (serving)

1. No tests for the `/v1` proxy streaming path (reservation release on stream
   end/disconnect, 502 path, header forwarding) — where V-4/V-10 live.
2. No tests for `agent._stream_turn` SSE parsing (tool_call delta assembly, usage
   chunk, malformed tool-arg JSON, streams ending without `[DONE]`) — all agent tests
   monkeypatch `_stream_turn` away.
3. No unit tests for `runtime.py` process lifecycle.
4. No test that `subset()`/`enabled_tools` restricts `call()` — would have caught V-1.
5. No test that a `/api/chat` client disconnect cancels the producer and restores
   `active_generations` to 0.

### Missing features / gaps

1. No force-stop for a stuck generation — only recourse is killing the whole server;
   an `/api/stop-generation` or reservation timeout would close the loop with V-4.
2. No orphan-runtime hygiene — pidfile or `os.setsid` + process-group kill, plus a
   startup sweep for stale llama-server children.
3. Tool results fed back to the model unbounded (`agent.py:199`) — one tool returning
   megabytes blows the context window; only the UI event is truncated.
4. `POST /models/{source}/pull` is a synchronous request-scoped download
   (`routers/catalog.py:21-37`) — an hours-long pull occupies an anyio threadpool
   token, has no progress reporting, and doesn't stop on client disconnect.
5. No `Cache-Control: no-cache` / anti-buffering headers on the `/api/chat` SSE
   response — matters the moment this sits behind any proxy (the stated
   Chrome-extension/desktop directions).

---

## Voice subsystem and terminal TUI

`voice/` (registry, importer, catalog, runtime, audio, dac), `kokoro.py`, `tts.py`,
`chat_tui.py`

### High

- **VO-H1. `available()` raises `ModuleNotFoundError` instead of returning False when
  mlx-audio is not installed.** `src/kodo/voice/runtime.py:22` —
  `importlib.util.find_spec("mlx_audio.tts.generate")` imports the parent package to
  resolve the dotted name; when `mlx_audio` is absent it raises rather than returning
  None (verified empirically). On Linux or any install without the `voice` extra,
  every gate crashes instead of degrading: `kodo voice speak --model dia`
  (`cli.py:1609`) tracebacks instead of showing the install hint; `/v1/audio/speech`
  (`serving.py:711`) and `/v1/audio/transcriptions` (`serving.py:768`) return 500
  instead of the intended 503. Fix: check `find_spec("mlx_audio")` first or wrap in
  `try/except ModuleNotFoundError`. (`kokoro.py:119` uses a top-level name and is safe.)

- **VO-H2. mlx-audio synthesis returns only the first audio segment — multi-sentence
  text is silently truncated.** `src/kodo/voice/runtime.py:61-79` — `generate_audio`
  is called without `join_audio=True`; the installed mlx-audio writes one file per
  segment (`out_000.wav`, `out_001.wav`, ...) and kodo reads only `out[0]`. Any
  paragraph-length synthesis via `/v1/audio/speech` or `kodo voice speak --model
  dia|soprano|chatterbox|spark` returns the first sentence only. Fix: pass
  `join_audio=True` (joined output is `out.<fmt>`) or concatenate all globbed files.

- **VO-H3. `mkstemp` file descriptor leaked on every synthesis call.**
  `src/kodo/kokoro.py:216` and `src/kodo/tts.py:87` — `Path(tempfile.mkstemp(...)[1])`
  discards the open fd. A long-running `kodo serve` leaks one fd per TTS request on
  the kokoro / llama-tts paths; with macOS's default soft limit (256) the server hits
  `EMFILE` after a few hundred requests, taking down unrelated endpoints.
  (`serving.py:717-718,773-774` do it correctly with `os.close(fd)`.)

### Medium

- **VO-M1. MCP connect is awaited inside the TUI's `on_mount`, freezing the app while
  servers spawn; `tools.connect` has no timeout.** `src/kodo/chat_tui.py:311-314` —
  the UI paints then ignores all input while awaiting `mcp_tools.connect` (a cold
  `npx`/`uv` server takes 10+ s; a hung one, forever — no `/exit` possible). Should be
  `self.run_worker(...)` like `_reconnect_mcp` (`chat_tui.py:655`).
- **VO-M2. Kokoro registry presence tracking is disconnected from the assets the
  ONNX backend actually uses.** `voice/registry.py:83-96` + `voice/catalog.py:94-111`
  — the entry declares an MLX repo but the backend loads GitHub-release ONNX files
  from `<library_root>/tts/kokoro` (`kokoro.py:139-170`). `kodo voice list` shows
  Kokoro "not downloaded" while chat speech works; `kodo library pull voice kokoro`
  downloads a ~310 MB MLX repo nothing can run (routing for id `kokoro` always goes
  to the ONNX backend, `serving.py:703`).
- **VO-M3. `VoiceModel.supported=False` (Qwen3-TTS) is enforced only by the web UI.**
  `serving.py:687-743`, `cli.py:1596-1622` — `POST /v1/audio/speech` or `kodo voice
  speak --model qwen3-tts` loads the multi-GB model and fails late with "produced no
  audio" instead of an immediate 422.
- **VO-M4. Voice importer's cache prune is gated on a >= 95 percent byte-count
  heuristic**, not the "byte-for-byte verified copy" its docstring claims
  (`voice/importer.py:59-62`) — this is the delete-the-source path for `pull --move`.
  Tighten the check or fix the docs.
- **VO-M5. The `pcm` response format returns a RIFF/WAV container, not raw samples.**
  `voice/audio.py:26` + `serving.py:672` — OpenAI's `pcm` means headerless 16-bit
  24 kHz; clients get 44 bytes of WAV header as samples plus a sample-rate mismatch
  for 44.1 kHz models like Dia. Related: `registry.sample_rate` is dead metadata.

### Low

- **VO-L1.** A prompt submitted while a model switch is in flight is destroyed —
  input cleared before the "hold on" notify, not queued (`chat_tui.py:731-734`),
  unlike the busy path at line 738.
- **VO-L2.** ESC between widget mount and `agent.run` leaks the thinking spinner —
  `CancelledError` at `chat_tui.py:836` bypasses the inner cleanup; the timer
  animates "Percolating..." forever. Cleanup belongs in the outer `finally`.
- **VO-L3.** `on_unmount` awaits `self._stack.aclose()` unprotected
  (`chat_tui.py:316-317`) — a dead MCP server turns quit into a teardown traceback
  (`_reconnect_mcp` wraps the same call for exactly this reason).
- **VO-L4.** Quitting mid-switch can orphan a freshly spawned llama-server —
  `_reserve` runs in a thread; if the app exits before `_do_switch` rebinds
  `self._runtime`, the `finally` stops the already-stopped old proc
  (`chat_tui.py:512-521,990`).
- **VO-L5.** Temp WAV leaked on synthesis failure (created before the engine runs;
  caller-side unlink only on success), and `tts.py:92` runs `llama-tts` with no
  timeout — a hung binary blocks the request thread indefinitely.
- **VO-L6.** Seed reproducibility and thread safety are racy under concurrency —
  `mx.random.seed()` is process-global while synthesis runs via `asyncio.to_thread`
  with no lock; two concurrent requests can interleave seed-then-generate. `_load`'s
  `lru_cache(maxsize=4)` also silently pins up to 4 multi-GB models in memory.
- **VO-L7.** `hfcache.configure()` runs once at import; if the drive is unmounted at
  that moment, a later Dia synthesis fetches the DAC codec into `~/.cache/huggingface`
  — the exact gotcha the module exists to fix — with no warning. Worth a `doctor`
  check or a re-check at synth time.

### Known-gotcha verification (from CLAUDE.md)

- Dia seeds via `mx.random.seed()`: handled (`voice/runtime.py:53-60`), modulo VO-L6.
- Leading `[S1]` degrades Dia: handled by omission — kodo never injects speaker tags.
- Qwen3-TTS unsupported: flag exists but only the web UI respects it (VO-M3).
- DAC codec portability: now addressed via `hfcache.configure()` + `kodo voice setup`,
  with the residual hole in VO-L7. The CLAUDE.md note "not drive-portable yet" is
  stale relative to the code.

### Test coverage gaps and missing features (voice/TUI)

1. No tests at all for `voice/audio.py`, `voice/runtime.py`, `voice/dac.py`, or any
   of `/api/speak`, `/v1/audio/speech`, `/v1/audio/transcriptions` — VO-H1/H2/H3
   would all have been caught by a thin mocked test layer.
2. `test_chat_tui.py` covers only happy paths — no ESC-cancel rollback, error path,
   `/clear`, autocomplete, queue-drop-on-cancel, or submission-during-switch.
3. The CLI cannot select preset voices for mlx-audio preset models (soprano/
   chatterbox/spark): `--voice` unconditionally routes to Kokoro (`cli.py:1599`) and
   the mlx path passes no `voice=` (`cli.py:1620`); the HTTP API supports it.
4. `_switchable_models` is cached for the whole TUI session (`chat_tui.py:447-454`)
   — a model pulled after startup never appears in `/model`; no refresh command.
5. Unbounded transcript growth: 4 widgets mounted per turn, never pruned; the 0.08 s
   full-Markdown re-parse of the growing answer makes long replies progressively
   laggier.

---

## Frontend SPA

`frontend/src` (Vite + React + Tailwind v4 + shadcn/ui), served by `kodo serve --ui`

Handled well: the SSE loop buffers partial lines correctly across network chunks
(`api.ts:337-343`), and model switch/eject mid-stream is guarded server-side —
`/api/load` and `/api/unload` return 409 while `active_generations > 0`
(`serving.py:124-131,546,661`).

### High

- **F-1. Media attachments silently kill all conversation persistence.**
  `frontend/src/lib/store.ts:68-74` + `frontend/src/App.tsx:165`. Every conversation,
  including full image/audio data URLs, is serialized to a single localStorage key on
  every state change. One pasted photo (2-8 MB base64) exceeds the ~5 MB quota;
  `saveConversations` swallows `QuotaExceededError`, so from that moment nothing
  persists — the user keeps chatting, reloads, and loses everything since the first
  oversized save, with no warning.

### Medium

- **F-2. Unmounting mid-recording leaks the live microphone.**
  `frontend/src/components/Composer.tsx:129,207` (no unmount cleanup for
  `recRef`/`dictRef`); same in `VoiceView.tsx:205,577`. Start a recording, send the
  first message (the empty-state Composer unmounts) or navigate away: MediaRecorder,
  VAD `AudioContext`, and mic tracks keep running until page reload.
- **F-3. SettingsPanel shows stale sampling/context values after a conversation
  switch.** `frontend/src/components/SettingsPanel.tsx:112-115` — local state seeded
  from `settings` once at mount; the panel stays mounted across conversation switches,
  and the next keystroke pushes stale values into the new conversation's settings.
- **F-4. Navigating to a deleted conversation id makes `send` stream into nowhere.**
  `frontend/src/App.tsx:187-191` — the `hashchange` handler doesn't validate the id
  (the initial-load path does). With a dangling `activeId`, the user's message vanishes
  and tokens stream server-side into a conversation that doesn't render. Repro: delete
  a chat, press browser Back, type, send.
- **F-5. Library fetch failure renders as "No chat models yet".**
  `frontend/src/App.tsx:206-212` sets `libraryLoaded=true` in `finally` even on error;
  the error banner exists only on the Chat surface, so LibraryView shows a misleading
  empty state when the server is restarting or the drive is unmounted.
- **F-6. Dropping a file with no model loaded navigates the browser away.**
  `frontend/src/components/Composer.tsx:245-256` — no `preventDefault()` when
  `canAttach` is false; the browser opens the dropped file, killing in-flight state.
- **F-7. Streaming state is global and bleeds into other conversations.**
  `frontend/src/App.tsx:161,929` — switch to conversation B while A streams: B renders
  a streaming cursor, B's composer shows Stop, and pressing it aborts A invisibly.

### Low

- **F-8.** Regenerate's empty-assistant filter is dead code (`App.tsx:563-577` —
  `prior` filters `kept`, only used when `kept` is empty, so never applies); aborted
  empty turns persist as permanent "..." ghosts and are replayed to the model, as are
  error-banner messages.
- **F-9.** `streamChat` never processes leftover buffer after `done` and never flushes
  the decoder (`api.ts:338-361`) — on truncated connections the terminal `error`/`done`
  event is exactly what gets lost; the `catch { continue }` silently swallows malformed
  complete lines.
- **F-10.** Voice studio blob-URL leaks: last clip's URL never revoked on unmount;
  `createObjectURL` inside `setState` updaters double-fires in StrictMode
  (`VoiceView.tsx:104,170-173,244-247`). `SpeakButton.tsx` does it correctly.
- **F-11.** Concurrent TTS playback with no coordination — Listen on a second reply
  plays over the first; stop-after-pause re-synthesizes from scratch.
- **F-12.** `await rec.stop()` can reject with no catch in VoiceView
  (`VoiceView.tsx:214-215,602-603`); a non-numeric seed becomes `NaN` → serialized as
  `"seed": null` (`VoiceView.tsx:242`, `api.ts:234`).
- **F-13.** With tag filters active, the Chat section heading mixes an unfiltered model
  count with a filtered size total (`LibraryView.tsx:517-519,444-448`).

### Backend serving / packaging notes

- SPA mount is correct (`app.py:154-164`; API routes take precedence, favicon shim,
  clean "UI not built" warning).
- CORS handling is notably good: no middleware unless configured, plus a
  `Sec-Fetch-Site` guard blocking cross-site mutating calls (`app.py:26-52`).
- Packaging gap: `uv_build` packages only `src/`, so a wheel install has no
  `frontend/dist` — `serve --ui` degrades to API-only. Fine for the current editable
  install; the UI ships only from a source checkout.

### Missing features / gaps

1. Conversation storage needs IndexedDB or attachment stripping (plus a visible
   "persistence failed" warning) — with vision/audio models, F-1 is inevitable.
2. No stream watchdog — a hung runtime leaves the UI streaming forever; no first-token
   or idle timeout.
3. No global error surface — Library/Voice views swallow every fetch failure.
4. `/api/voice` fetched three times independently with no shared cache.
5. Accessibility: hover-only action rows are invisible to keyboard users (no
   `focus-within` style); no `aria-live` region, so streamed replies are silent for
   screen readers.

---

## Infrastructure, tests, packaging, docs

`make check` passes end-to-end (ruff format/check, mypy, pyright, 282 tests in ~5s).
The foundation is solid; these are the gaps.

### High

- **I-1. No CI at all.** No `.github/` directory, no workflows anywhere. `make check`
  is a well-built gate that nothing enforces — and the repo is committed to `main`
  directly, making CI the only possible safety net.
- **I-2. The real pull path has zero test coverage — all source tests are mocked.**
  `tests/test_sources.py` (679 lines): 34 mock/monkeypatch uses, zero HTTP (not even a
  mock transport). The core promise — pull from HF/Ollama/LM Studio, byte-for-byte
  verified, `--move` deletes source — is never exercised against even fake-HTTP data.
  The only non-mocked tests are the 5 opt-in slow e2e tests, which cover serve/stream
  only and never run automatically.

### Medium

- **I-3. Zero-coverage modules:** `templates.py` (480 lines — the single largest
  untested module; a broken template silently ships broken `project new` scaffolds),
  `cards.py` (sidecar writing, part of every pull), `mcp_catalog.py`.
- **I-4. Thin tests relative to the code covered:** `test_cli.py` (295 lines) vs
  `cli.py` (2166) — `serve`, interactive `chat`, `doctor`, voice, tags, attach commands
  untested; `test_api.py` stubs the runtime, so proxy/load error paths are untested;
  `test_server.py` (70 lines) vs `server.py` — ready/timeout/log-tail paths untested;
  voice tests ~200 lines vs ~1000 lines of code; `runtime.py` has no dedicated test
  file. The serve/chat surface — the half users touch — is the least tested half.
- **I-5. The frontend is entirely outside the quality gate.** `frontend/package.json`
  has no `tsc` type-check (vite/esbuild strips types without checking), no eslint, no
  tests; `make check` never touches `frontend/`. TS type errors don't even fail the
  build.

### Low

- **I-6.** `README.md:35` documents `make install-voice`, which doesn't exist (real
  path: `uv sync --extra voice`). Docs are otherwise impressively fresh.
- **I-7.** mypy reports unused override sections (`pyproject.toml:174-183`:
  `misaki.*`, `mlx_lm.*`, `mlx_vlm.*` — spawned as processes, never imported) on every
  run — noise that trains people to ignore mypy output.
- **I-8.** Built wheels are uninstallable outside the workspace: `pyproject.toml:13-20`
  hard-depends on `kodo-benchmark` + 7 `kodo-mcp-*` workspace-only packages. Also
  `kodo-benchmark` is documented as dev-only but is a hard runtime dependency.
- **I-9.** Type/lint strictness is weaker than CLAUDE.md claims: mypy sets individual
  flags but not `strict = true`; pyright strict disables 7 `reportUnknown*` rules;
  ruff selects only `E,W,F,I,D` (no `B` bugbear, `UP`, `SIM`, `RUF`).

### Checked, no issue found

- Extras correctness: `mlx` / `mlx-audio` correctly env-marker-gated for
  darwin/arm64; Linux installs won't break. Entry point matches the strict-library
  design. All deps `>=`-pinned with `uv.lock` committed.
- `.gitignore`/secrets: tight — `.env*` ignored with `!.env.example` (placeholders
  only), `/data/` ignored, zero weight files tracked, `.env` never committed in
  history.
- `dist/` contents are gitignored stale local wheels; `frontend/dist` gitignored
  (build-on-install, intentional); `benchmarks/results/` committed deliberately.

### Missing infrastructure

1. A CI workflow running `make check` (ubuntu + macos matrix would also verify the mlx
   extras' platform gating actually no-ops on Linux — currently unverifiable locally).
2. Frontend type-check: add `tsc --noEmit` to the build and a `make check` step.
3. Coverage gate: config exists and `make coverage` works, but no `fail_under` and
   `make check` skips it.
4. Any automated execution of the slow e2e suite (scheduled/manual CI job or a
   pre-release checklist) — the only tests of the real serve path require remembering
   `make test-slow`.
5. Pull-path integration tests with a fake HTTP layer (respx / local fixture repos) —
   the missing middle tier between fully-mocked unit tests and live e2e.

---

# Verification pass — 2026-07-05 (second, independent review)

Every finding above was re-verified against the code at `2275545`; the high-impact
ones were reproduced empirically (scratch library roots, fake manifests, malformed
configs, a fake SSE server, an in-process fake MCP client, `tsc --noEmit`, and
read-only checks against the real library). Verdict summary:

- **Confirmed as written:** S-H1, S-H2 (reproduced), S-M1, S-M2, S-M4, S-M5, S-M7
  (reproduced), S-L1, S-L2, S-L4, S-L5; C-1 through C-12 (C-1..C-5 and C-9
  reproduced); V-1 (reproduced: a subset view executed a non-subset tool), V-2
  (fastmcp 3.4.2 `client_init_timeout` defaults to `None` → `fail_after(None)`),
  V-3..V-7 (V-7 reproduced), V-9..V-16; VO-H1 (reproduced: `find_spec` on a dotted
  name raises `ModuleNotFoundError` when the top-level parent is absent), VO-H2,
  VO-H3, VO-M1..VO-M5, VO-L1, VO-L3..VO-L7; F-1, F-3..F-13; I-1, I-4..I-9. All four
  serving "Verified OK" bullets, the CLI/serving/voice test-gap lists, and the
  missing-features lists hold up.
- **Real library sanity check:** 20/20 `verify` ok, `doctor` all good; the S-M3
  cosmetic note confirmed live (`CHECKED` column prints `weights+card`).

## Corrections (the record to trust where it differs from above)

- **VO-L2 — refuted.** There is no await point between the spinner's
  `set_interval` (`chat_tui.py:861`) and `agent.run` (`:921`), and the inner
  `except asyncio.CancelledError` does call `_stop_think()` — the
  "Percolating... forever" leak is impossible. The kernel of a *different* small
  bug: an ESC landing on the mount await at `:836` skips `del self.messages[mark:]`,
  leaving the user message in history with no reply.
- **V-8 — partial.** Abrupt mid-stream runtime death does *not* truncate silently:
  it raises `httpx.RemoteProtocolError`, surfaced as an SSE `error` event
  (reproduced against a fake SSE server). What stands: a *cleanly closed* stream
  without `[DONE]` records the partial text as a complete reply, and llama-server
  SSE error objects (no `choices`) are silently skipped, yielding an empty reply.
- **I-2 — overstated.** Ollama and LM Studio pulls have no HTTP in production
  either, and `test_sources.py` exercises them against real `tmp_path` files —
  including corrupt-same-size re-copy, `--move`-removes-source, shared-blob
  preservation, and manifest-not-published-on-blob-failure. The valid residue is
  narrower: the *HF network path* is mocked at the `snapshot_download` seam and
  never sees even fake-HTTP data.
- **I-3 — wrong on two of three.** Measured coverage: `cards.py` 100% and
  `templates.py` 100% (only 24 statements; mostly string constants), both executed
  indirectly by existing tests; `mcp_catalog.py` 43% with `uninstalled_optional`
  directly tested. The substantive residue stands: no test selects a template or
  asserts scaffold content (`TEMPLATES` is consumed only at `cli.py:1190`).
- **F-2 — bounded, not unbounded.** The VAD closure captured at `startRecording`
  still fires `onSilence` / the 60 s `MAX_MS` cap after unmount and stops the
  tracks (`recorder.ts:99-118`). Real leak, worst case ~60 s of live mic — not
  "until page reload".
- **S-L3 — one detail wrong.** The `HF_HOME` token-relocation and exFAT
  symlink-degradation halves hold, but "later logins write the credential onto the
  drive" does not: kodo mutates only its own process env; a `huggingface-cli login`
  in the user's shell still writes to `~/.cache`.
- **S-M6 — narrowed.** `kodo serve` calls `library_ops.roots()` at startup
  (`cli.py:1920`), so the fully-unconfigured HTTP case fails early. The reachable
  case is a project with local `libraries` and no `KODO_LIBRARY_ROOT`: `roots()`
  passes, the HTTP pull still defaults to `./data`.
- **C-2 — understated.** `--shared` targets `./data` in *any* unconfigured
  directory, project or not (reproduced: `pull --shared` printed `-> data` and
  created `./data/` before the network failure).
- **S-H1 — worse than stated.** On a size mismatch the CLI *still prints*
  `" (local copy removed)"` — the suffix is keyed on the `move` flag alone
  (`cli.py:994-995`), so the user is affirmatively told the source was removed
  when it was kept.
- **C-3 — nuance.** The abort is a clean `LibraryNotConfigured` message, not a
  traceback, and plain free-play unconfigured renders the report fine (including
  the "not configured" row). The report is lost only when a `kodo.toml` binds a
  model — which is precisely a config doctor should survive. Also: doctor's
  `@shared` warning (`doctor.py:146`) is unreachable in the implicit-default case
  because the crash happens first.
- **C-5 — one more instance.** `doctor.py:163` hints `kodo pull huggingface
  {model}`, also nonexistent.
- **VO-H2 — precision.** mlx-audio segments per *newline* (`split_pattern="\n"`),
  not per sentence: single-line multi-sentence input is fine; multi-*paragraph*
  text truncates to the first line. Chatterbox yields a single segment and is
  unaffected.
- **VO-M4 — attribution.** The "byte-for-byte" promise is CLAUDE.md's `pull
  --move` policy; the importer docstring itself says "byte total". Its own "never
  delete before a good copy" is still contradicted by the 5% slack.
- **Voice/TUI test-gap item 1 — partial.** `voice/dac.py` *is* tested
  (`tests/test_hfcache.py:68-86`); the rest of the list holds.
- **I-5 note.** `npx tsc --noEmit` currently exits 0 — the gate gap is real but
  no type errors exist today.

---

# Additional findings (second pass)

New issues found while verifying, plus two fresh reviews of areas the first pass
covered lightly (`config.py`, `models.py`, `attach.py`, `chatui.py`, `plugins.py`,
`arch.py`, `capabilities.py`, `sampling.py`, `cards.py`) or not at all
(`packages/`, next section). Empirically reproduced unless noted.

## Second-pass additions to the top-issues list

- A non-dict `config.json` in any MLX/safetensors model dir crashes every
  `library.scan()` (N-H1) — same blast radius as S-H2, different file.
- An unreadable or just-deleted file dragged into the TUI tears down the whole
  app and loses the session (N-H2; found independently by two reviewers).
- Every interactive TUI message is shlex-mangled, and merely *mentioning* an
  existing filename — including `.env` — silently inlines that file into the
  prompt (N-M1).
- `kodo library rm` prints "Removed ... freed ..." when deletion failed
  (C-N2; reproduced with a read-only dir) — on an unmounted or read-only external
  drive, every `rm` "succeeds".
- A loose weight file at the bucket or library root creates a nameless model
  whose removal would `rmtree` the entire bucket or library (S-N1).
- `KODO_CORS_ORIGINS` set as a plain (non-JSON) env var crashes every kodo
  command (N-M3) — the exact override the Chrome-extension work invites.
- The `kodo-mcp-web` SSRF guard is bypassable by DNS rebinding (P-H1).

## Storage

- **S-N1 (High). Loose weight file → nameless model spanning the bucket/root;
  `rm` would delete everything under it.** A stray `.gguf` at `<root>/gguf/`
  yields a scan entry with `name=""` and `path=<root>/gguf` (at `<root>`, the
  whole library): `_scan_dirs` adds `weights.parent` unconditionally
  (`library.py:248-255`) and `_clean_name` strips to `""` (`library.py:110-115`);
  `find_copies("")` matches it (`library.py:428`), so `kodo library rm ""` →
  `shutil.rmtree` of the bucket or root (`library.py:452`). Scan should skip
  entries whose cleaned name is empty.
- **S-N2 (Medium).** `pull --move` success text is keyed on the flag, not the
  outcome (`cli.py:994-995`) — any skipped removal is reported as removed
  (aggravates S-H1, VO-M4).
- **S-N3 (Low).** `library.remove` derives the Ollama store root from the first
  `"manifests"` component of the absolute path (`library.py:447-448`) — a library
  root whose own path contains a `manifests` directory computes the wrong store.
  `verify` does it correctly via `model.library_root / "ollama"` (`library.py:580`).
- **S-N4 (Medium).** `lmstudio.pull` accepts a publisher-level name: the whole
  publisher is copied into an `unknown/` bucket (`_classify` sees no top-level
  weights), and `--move` then deletes *every* model under that publisher from LM
  Studio in one shot (`lmstudio.py:92-103`).
- **S-N5 (Medium).** Voice presence discovery ignores the legacy `tts/` bucket
  that `library.scan()` still reads: `voice/catalog._library_dir` checks only
  `<root>/voice/<repo>` (`voice/catalog.py:46-49`). Live on the real library:
  OuteTTS at `tts/OuteAI/OuteTTS-0.2-500M-GGUF` is verified (20/20) but appears in
  *neither* `library ls` section, and `voice ls` reports it "not downloaded" —
  `kodo library pull voice outetts` would download a ~500 MB duplicate into
  `voice/`. Same family as VO-M2.

## CLI and config

- **C-N1 (High). Malformed `kodo.toml` has a second, independent crash site.**
  `Settings` parses the same file via `TomlConfigSettingsSource`
  (`config.py:44-71`); `get_settings()` alone raises `TOMLDecodeError` (verified).
  Fixing C-1 in `project.load` is insufficient — nearly every command calls
  `get_settings()`, and `kodo.app` calls it at import.
- **C-N2 (High). `library rm` false-success.** `shutil.rmtree(...,
  ignore_errors=True)` then unconditional counts (`library.py:452`,
  `cli.py:666-670`). Reproduced: with the model dir `chmod 555`, `rm --yes`
  printed "Removed pub/model-b — freed 17.0 B (1 files)" while the weights
  remained.
- **C-N3 (Medium).** `project init/new --model <ollama-model> --local` crashes
  with `NotADirectoryError`: `_copy_model_local` runs `shutil.copytree` on the
  Ollama manifest *file* (`cli.py:1151-1158`); the interactive picker filters
  `is_ollama` but the explicit `--model` path doesn't (`cli.py:1227`) — and even a
  successful copy would take the manifest without its blobs.
- **C-N4 (Medium).** `_add_pyproject_dep`'s already-present check regex-scans the
  *whole* pyproject (`cli.py:441`) — a package named in a dev group, a
  `[tool.uv.sources]` key, or a comment silently skips the runtime pin, and the
  MCP server then fails at spawn time.
- **N-M3 (Medium).** `KODO_CORS_ORIGINS=chrome-extension://abc` (plain string, as
  any user would write it) raises `SettingsError` from `get_settings()` on every
  command (`config.py:97` requires JSON for list fields from the env source;
  verified). The config docstring's "every value can be overridden with a KODO_*
  env var" is false for list fields unless written as `'["..."]'`.
- **N-M4 (Medium).** The "one broken plugin can't take down the CLI" guarantee
  covers import only: `_mount_plugins()` runs at `cli` import for every command
  (`cli.py:2144-2152`) and pluginkit invokes hooks with no error handling — a
  plugin whose `commands()` raises at call time breaks every invocation including
  `kodo --help`; a plugin returning a bad `mcp_servers()` entry raises
  `ValidationError` out of `kodo mcp list` / `chat --mcp` (`plugins.py:118-140`).

## Serving and agent

- **V-N1 (Medium).** `POST /api/tags` on a nonexistent model silently writes a
  phantom entry: `root = matches[0].library_root if matches else
  settings.library_root` (`serving.py:269-271`) — should 404.
- **V-N2 (Low).** `agent.py:71` calls `raise_for_status()` on a streamed response
  without reading the body — llama-server's JSON error detail (e.g. context
  overflow) is discarded; the UI gets only httpx's generic status line.
- **V-N3 (Low).** `proxy_v1` acquires the reservation eagerly in the handler but
  releases inside the `relay()` generator (`serving.py:798,819-827`); a client
  disconnect before the body starts iterating can skip both the upstream close and
  the release. Same family as V-10; acquiring inside the generator (as `/api/chat`
  does) closes both.

## Core modules (fresh pass)

- **N-H1 (High). Non-dict `config.json` crashes every scan.**
  `arch.config_is_generative` catches only `OSError`/`JSONDecodeError`, then calls
  `data.get(...)` (`arch.py:29-33`); valid-but-non-object JSON (`[]`, a bare
  string — truncation repair, corruption on no-journal exFAT) raises
  `AttributeError` out of `library.scan()` (called unguarded at `library.py:300`),
  breaking `ls`/`chat`/`serve`/`verify`. `capabilities._read_json` guards
  `isinstance(dict)` (`capabilities.py:149-155`); arch.py should too.
- **N-H2 (High). Unreadable attachment kills the TUI.**
  `attach.split_input_media` gates on `is_file()` then reads with no error
  handling (`attach.py:63-68`); a permission-denied file (verified) or a file
  deleted between check and read raises out of `_generate`'s worker
  (`chat_tui.py:798`), and Textual's default `exit_on_error=True` tears down the
  app mid-conversation, losing the transcript.
- **N-M1 (Medium). The TUI message path shlex-mangles input and auto-inlines any
  mentioned file.** Every submitted line is `shlex.split` and rejoined
  (`attach.py:52-71`): quotes stripped, backslashes eaten (verified: `explain
  "import os" and C:\temp` → `explain import os and C:temp`); any bare token that
  resolves to an existing file is silently consumed as an attachment — `please
  summarize notes.md for me` becomes `please summarize for me` + the file inlined.
  `.env` is in `TEXT_EXTS` (`attach.py:21`), so mentioning `.env` in a project dir
  inlines secrets into the prompt. The transcript displays `raw_text`
  (`chat_tui.py:809`), so the user never sees what the model received.
- **N-M2 (Medium). No attachment size limit anywhere.** `media_data_url` reads and
  base64s whole files in memory (`attach.py:30-33`); `read_text` inlines unbounded
  text (`attach.py:67-68`); the CLI flags path checks only `is_file()`
  (`cli.py:1663-1677`). Also `.ts` in `TEXT_EXTS` collides with MPEG
  transport-stream video (megabytes of mojibake).
- **N-L1 (Low).** `_read_gguf_string` trusts a uint64 length from the file and
  `fh.read(length)` (`capabilities.py:54-56`) — a bit-flipped length can slurp a
  multi-GB weight into RAM before the parse fails; cap it (the KV block is at the
  head).
- **N-L2 (Low).** `cards.py:28,36` `write_text` with no `encoding=` — on a
  POSIX-locale Linux host a non-ASCII Ollama card raises `UnicodeEncodeError`
  *after* the multi-GB copy succeeded. Same latent pattern in `project.py:48` /
  `arch.py:29` reads. `encoding="utf-8"` throughout.
- Clean: no `@dataclass` anywhere in `src/` (rule 4 holds); no emoji; `chatui` and
  TUI sampling paths consistent; `_project_toml` escapes prompts via `json.dumps`.

## Voice

- **VO-N1 (Medium).** An interrupted voice import is permanently treated as
  complete: a partial `copytree` leaves real files, so `_library_dir` reports
  `in_library=True` and both importers return `already_present=True` forever
  (`voice/importer.py:42-45,94-95`; `voice/catalog.py:46-49`) — never repaired.
- **VO-N2 (Low).** `kodo voice speak` leaks the intermediate WAV on every
  successful run (`cli.py:1607,1634` never unlink; the serving counterparts do).
- **VO-N3 (Low).** VO-L3 aggravation: after `/mcp reconnect` the new stack was
  entered in a *worker* task, so the pump-task `on_unmount` `aclose()` risks
  anyio's cross-task cancel-scope error even with healthy servers.

## Frontend

- **F-N1 (Medium). Dictation clobbers text typed during recording.**
  `finishDictation` closes over `value` from the render where recording started
  (`Composer.tsx:219,231`); the VAD auto-stop then resets the composer to
  recording-start text + transcript, silently discarding interim typing.
- **F-N2 (Medium). Per-token full-store serialization.** Every streamed token
  triggers the persistence effect (`App.tsx:165`), which `JSON.stringify`s *all*
  conversations including base64 attachments (`store.ts:70`) — multi-MB
  main-thread stringify at token rate once any media is stored; visible jank that
  compounds F-1.
- **F-N3 (Low).** VoiceView fetch failure renders "No voice models yet"
  (`VoiceView.tsx:691-695`) — F-5's twin.
- **F-N4 (Low).** Pasting an image a text-only model can't read is silently
  discarded (`Composer.tsx:345-348`), while the drop path shows the rejection
  hint.

## Infrastructure

- **I-N1 (Medium).** The coverage `source` list (`pyproject.toml:146`) omits
  `kodo_mcp_search`, `kodo_mcp_utils`, `kodo_mcp_web` — three workspace members
  with tests whose coverage is silently excluded from `make coverage`.
- **I-N2 (Low).** The docs build (`mkdocs` + `mkdocstrings`, which imports the
  code) is outside `make check` — a moved symbol breaks docs silently.
- **I-N3 (Low).** `make frontend` uses `npm install`, not `npm ci`
  (`Makefile:95`), despite the committed `package-lock.json` — builds can mutate
  the lockfile and don't fail on drift.

---

# Workspace packages (`packages/`) — not covered by the first pass

`kodo-benchmark`, `kodo-sandbox`, and the eight `kodo-mcp-*` servers. These tools
are *executed by the model* in the agent loop, so input is adversarial by
construction (prompt injection reaches tool arguments).

### High

- **P-H1. `kodo-mcp-web`'s SSRF guard is bypassable by DNS rebinding.**
  `_guard_url` resolves via `getaddrinfo` and rejects blocked IPs, but
  `httpx.AsyncClient.get` then performs its *own* DNS resolution to connect
  (`kodo-mcp-web/.../app.py:82-111,224-240`) — a hostname that resolves public for
  the guard and `127.0.0.1`/`169.254.169.254` for the fetch reaches internal
  services. The browser path (`_route_is_blocked`, `:114-130`) has the same shape
  (Chromium re-resolves). Fix: resolve once and connect to the pinned IP.

### Medium

- **P-M1.** The IP blocklist misses shared-address space `100.64.0.0/10` (CGNAT —
  verified all `is_private/is_loopback/...` return False). Use `not is_global`
  instead of enumerating ranges.
- **P-M2.** The exec sandbox runs model code as root-in-container with default
  capabilities: `--network=none --read-only --memory --pids-limit` are set, but no
  `--user`, no `--cap-drop=ALL`, no `--security-opt=no-new-privileges`, no
  `--memory-swap` (`kodo-sandbox/__init__.py:95-101`). This is the highest-stakes
  surface in the repo; drop privileges as defense-in-depth.
- **P-M3.** `run_python` accepts an unbounded model-supplied `timeout_s` and
  buffers the container's entire stdout in *host* memory
  (`kodo-mcp-exec/app.py:21-34` → `subprocess.run(capture_output=True)`) — the
  container memory cap doesn't bound the streamed bytes. Cap the timeout
  server-side and truncate output.
- **P-M4.** The web static fetch materializes the full response body before
  extraction (`app.py:232-240`); `max_chars` caps only the *output*. Stream with a
  max-bytes abort.
- **P-M5.** The benchmark counts cold Docker image pulls inside the per-problem
  timeout (`kodo-benchmark/core.py:211-227`; rust suite `timeout_s=20` vs a
  hundreds-of-MB `rust:1-slim` pull) — a model scores FAIL for an infrastructure
  delay. Pre-pull `RUNTIMES[*].image` before timing.
- **P-M6.** `kodo-mcp-files` `list_files`/`search` crash on any directory entry
  that symlinks outside the root (`_rel`'s `relative_to` raises `ValueError` for
  the *whole* listing, `core.py:63-83`), and `search` reads out-of-root file bytes
  before failing (`:107-127`; no exfiltration — the `Match` construction aborts —
  but the read happens). The actual escape is correctly blocked in
  `safe_join`/`read_text`; skip such entries instead of raising.

### Low

- **P-L1.** `kodo-mcp-memory` raises `AttributeError` on a hand-edited
  `notes.json` with non-object values (`core.py:82-101`; hand-editing is invited
  by its own docs). **P-L2.** Same store: unbounded growth, non-atomic
  read-modify-write under FastMCP's threadpool. **P-L3.** Weather: `series[0]`
  `IndexError` on an empty timeseries; coordinates unvalidated
  (`kodo-mcp-weather-yr/core.py:158-160`). **P-L4.** `calc` leaks a bare
  `ZeroDivisionError` where every other path wraps errors
  (`kodo-mcp-utils/app.py:186-207`). **P-L5.** The sandbox's post-timeout
  `docker rm -f` cleanup has no timeout — a wedged daemon blocks the tool call
  forever (`kodo-sandbox/__init__.py:106`).

### Handled well

`kodo-mcp-utils`'s AST-walking `calc` (no eval, bounded `**`/factorial);
`kodo-mcp-datetime`'s strict ISO/DST handling; the sandbox baseline
(network-none, read-only rootfs, tmpfs, cpu/mem/pids caps, name-based kill);
`kodo-mcp-web`'s per-hop redirect re-guarding and off-loop DNS; `kodo-mcp-files`'s
`safe_join` containment; benchmark scoring discipline (all-or-nothing,
normalization, arg-superset tool matching).

---

# CHROME.md review

The document's self-description holds: a claims audit against the code found **no
outright false statement** — every endpoint, request field, SSE event name, the
`cors_origins`/cross-site-guard split, the `KODO_SERVE_MODEL` lock flow, the
stateless `/api/chat` contract, and the dhis2w bridge env vars are exact. Three
items are stale or too conservative, and the second-pass findings add two
kodo-side work items it should list.

## Corrections to the document

1. **The 409-recovery recipe is wrong for exactly the servers Phase 1 targets.**
   `POST /api/load/{name}` returns 409 "Server is locked to a single model"
   whenever `KODO_SERVE_MODEL` is set (`serving.py:525-526`) — i.e. on every
   locked project server. The load-then-poll recipe only applies when
   `status.locked` is false (the web UI already skips auto-load when locked,
   `App.tsx:328`). In locked mode the model loads in the lifespan; if that load
   *failed*, the error surfaces in `status.error` and there is **no API path to
   recover** — restart kodo. Either fix the doc (load only when unlocked; treat
   locked+error as "restart kodo" state) or change kodo to allow re-loading the
   locked model itself as a recovery action.
2. **"Image parts schema-accepted but runtime-unverified" is too conservative.**
   The shipped web UI already sends `image_url` content parts through `/api/chat`
   (`api.ts:52`, `App.tsx:433`), and the CLI passes images too. The genuinely
   unverified leg is only mlx-vlm end-to-end.
3. **"Only a suggested `dhis2` MCP preset string" is stale.** There is also a full
   catalog entry (`mcp_catalog.py:19-23`) and a `--template dhis2` project
   template (`cli.py:1296+`). The boundary claim itself still holds — zero DHIS2
   *behavior* in kodo core.

## Additions for "Net-new kodo work"

- **N-M3 is a Phase-1 landmine:** `KODO_CORS_ORIGINS=chrome-extension://<id>` as a
  plain env var — the natural way to follow the doc's own allow-list advice —
  crashes every kodo command (pydantic-settings demands JSON for list fields).
  The `kodo.toml` route works (verified: `Settings` does read top-level
  `port`/`cors_origins` from `kodo.toml`). Fix the env parsing (a
  `NoDecode`/comma-split validator) before extension setup docs tell users to set
  it.
- **SSE anti-buffering headers** (`Cache-Control: no-cache`, `X-Accel-Buffering:
  no`) on `/api/chat` — already listed as serving gap 5 above; becomes user-facing
  the moment the extension talks to kodo through anything but a direct socket.

## Assessment of the options

The architecture is right, and notably disciplined about boundaries:

- **Option A (kodo → d2w MCP bridge) as the primary tool channel — correct.** It
  reuses the exact loop kodo already runs, keeps credentials in d2w profiles,
  works from CLI/TUI/bench identically, and the read-only enforcement lives
  server-side in the bridge (fail-closed allowlist) rather than in extension code.
- **The PAT-minting verdict is the strongest part of the document.** It converts
  "use my live login" from a fragile ambient-cookie design into a one-time,
  explicitly-confirmed credential issuance that lands in the existing profile
  machinery — same UX, auditable channel, works on `SameSite=Lax`. The detail
  that the mint call itself must run in the DHIS2-tab content script is the kind
  of thing that would otherwise be discovered late.
- **Option B (narrow tab-context reads) — correct as a Phase 2/3 supplement**, and
  the fixed-endpoint (never arbitrary-URL) constraint on the content script is
  the right containment. Worth adding one line: the same prompt-injection concern
  that motivates read-only MCP applies to page *context* too — DOM text fed to
  the model is untrusted input.
- **Options C/C2 (DHIS2 `/api/routes` proxy) — correctly deferred.** The NAT
  direction problem alone disqualifies it for a local-first product.
- **Option D (cookie relay) — correctly rejected**, and the measured
  `HttpOnly; SameSite=lax` evidence makes the rejection empirical, not
  ideological.
- **`GET /api/assistant` (generic, not `/api/dhis2/target`) — the right call.**
  Opaque project metadata + an MCP-resource proxy keeps kodo domain-free; this is
  the same boundary discipline that keeps the whole design coherent.
- **One challenge worth recording:** the accepted-risk note (any local process can
  drive `/api/chat` and thus the tool loop) is accurate today and acceptable for
  read-only DHIS2 — but the packages review shows the *local* tool surface
  already includes exec-in-sandbox and filesystem servers. "Blast radius is
  reads" is only true of the DHIS2 tools; a shared bearer token is worth
  implementing *before* the extension ships, not "if writes are added".

---

# Architecture review

Bottom line: **the shape of the system is right.** The layering direction is
clean (verified: no module below imports `cli`/`app`/`routers`/`chat_tui`; no
top-level cycles), and the five load-bearing decisions — format-centric storage,
runtimes as external processes, the server-owned agent loop with thin clients,
web-first UI with the TUI as a peer surface, DHIS2 knowledge exiled to d2w — are
individually sound and mutually consistent. The CHROME.md exercise is the proof:
a whole new surface (extension) attaches with near-zero net-new kodo work, which
is exactly what "the server owns the loop, clients are thin" is supposed to buy.
Pydantic-everywhere holds (zero `@dataclass`), the plugin Protocol correctly
forbids plugins importing kodo, and `sources/base.py` gives pulls a shared
staged-copy discipline.

The problems are not the decomposition; they are **cross-cutting invariants that
no single module owns**. Most of the bug clusters above are symptoms of eight
structural issues:

- **A1. `kodo.toml` has two parsers and a string-templating writer.**
  `Settings` reads it via `TomlConfigSettingsSource` (`config.py:44-71`) while
  `project.load` hand-parses the same file (`project.py:44-66`), and the CLI
  *edits* it by appending text blocks (`mcp add`, `cli.py:447-451`) and
  generating it by string interpolation (`_project_toml`). That's why malformed
  TOML crashes twice (C-1, C-N1) and why machine settings (`port`,
  `cors_origins`) and the portable project manifest (`[project]`, `[[mcp]]`,
  `libraries`) are conflated in one file with different validation on each read.
  Fix: one loader module that parses once into two Pydantic models (machine
  settings vs project manifest), returns typed errors, and owns writes
  (round-trip or regenerate — never append).

- **A2. The strict-library invariant is enforced at entry points, not at the
  boundary.** `library.roots()` raises `LibraryNotConfigured`, but the unsafe
  primitive — `get_settings().library_root` with its `Path("data")` default —
  stays public and is used in 8+ places, four of them bypassing the guard
  (C-2, S-M6, V-N1, `kokoro._assets_dir`). An invariant that must be re-remembered
  at every call site will keep regressing. Fix: make `library_root` optional with
  *no* default, make the accessor raise, and route every consumer through
  `roots()`. The `./data` default should not exist anywhere.

- **A3. "What models exist" has seven scanner implementations and no identity
  concept.** `_scan_dirs`/`_scan_voice`/`_scan_ollama`, three `sources/*`
  listers, and `voice/catalog.discover` (which re-implements HF-cache resolution
  separately from `hfcache.py`) each read different buckets with different rules.
  Identity is a name *string*: tags key on it (stale-tag inheritance C-12), an
  empty cleaned name is a valid model (S-N1), verify dedupes `(name, format)`
  while `rm` uses `find_copies` (C-6), and voice presence disagrees with the
  scanner about the same file on disk (VO-M2, S-N5). Fix: one scan module owning
  bucket layout + a `ModelRef` identity used by tags/verify/rm/presence alike,
  with per-item fault isolation — a model that fails to parse becomes a
  `status=unreadable` entry, never an exception out of `scan()` (S-H2, N-H1).

- **A4. Process lifecycle has no owner.** Runtime spawning is implemented twice
  (`runtime.start` for the CLI, `ServerManager.ensure` for serve — two parallel
  Popen/ready/stop paths), and no spawn site sets a process group, pidfile, or
  parent-death handling — hence the orphan family (V-3, V-5, VO-L4) and the
  port-TOCTOU (V-6). Fix: one supervisor module (setsid + process-group kill,
  pidfile or startup sweep, spawn-to-ready-to-stop as a context manager) used by
  both CLI and server.

- **A5. The system is multi-process in practice but single-actor by
  assumption.** CLI and `kodo serve` are *expected* to run concurrently against
  the same library, yet there is no inter-process locking anywhere (verified:
  no fcntl/flock/filelock in the tree) — tags lost-updates (S-M4), concurrent
  pull/rm/migrate races (storage gap 5). A single `<root>/.kodo/lock` file lock
  around mutations would cover the realistic two-process case cheaply.

- **A6. Controls live in the UI; enforcement belongs at the choke point.**
  `enabled_tools` filters what's *displayed* while `call()` executes anything
  (V-1); `supported=False` is enforced only by a React component (VO-M3); doctor
  hints and success messages assert outcomes the code didn't check (C-5, S-N2,
  C-N2). The pattern fix: the object that *performs* the action validates it —
  `toolset.call` checks the subset, the synthesis entry checks `supported`,
  `remove` reports what it actually deleted.

- **A7. `cli.py` is a 2,166-line god module (22% of the package), and
  `routers/serving.py` (829 lines) is heading the same way.** Real business
  logic lives in command bodies: kodo.toml generation/editing, pyproject regex
  surgery, project scaffolding with model copying, TTS backend dispatch, and a
  135-line scripted agent loop that parallels the TUI's. That's why the CLI
  layer is at 33% coverage and grows bugs like C-7..C-10, C-N3, C-N4 — the logic
  is only reachable through Typer. Fix mechanically: move logic to core modules
  (`project_io`, `scaffold`, `voice/dispatch`), leave argument parsing and
  echoing in cli.py; split serving.py by resource (chat/voice/library/tags).

- **A8. Import-time side effects and env vars as an internal API.**
  `import kodo.<anything>` resolves settings and mutates `HF_HOME`/
  `HF_XET_HIGH_PERFORMANCE` (`__init__.py:9,18`) — which is why an unmounted
  drive at import time silently mis-caches (VO-L7) and why an init-order cycle
  needs a lazy import inside `hfcache`. The CLI communicates with the app
  factory by writing `KODO_SERVE_*` env vars and clearing the settings cache
  (`cli.py:1925-1939`). Env handoff is defensible for uvicorn-reload workers,
  but it currently carries *all* serve parameters. Fix: move `hfcache.configure`
  into the entry points (`cli.main`, `create_app`), and pass serve config
  explicitly to `create_app`, keeping env only for the reload path.

Two smaller notes: the packaging boundary contradicts the plugin design — kodo
never imports the MCP packages (correct) but hard-depends on all of them at
install time (I-8), so the wheel is uninstallable and "plugins" are compulsory;
make them extras or standalone. And `library` ↔ `voice` is a genuine two-way
dependency suppressed by lazy imports on both sides — the registry lookup that
`library` needs from `voice` could move down into a shared module to restore a
one-way flow.

None of this argues for a rewrite. A1–A3 (config unification, one guarded
library-root accessor, one scanner with identity + fault isolation) would fix or
prevent roughly half the confirmed bugs in this file; A4–A6 close the
data-loss/orphan/enforcement classes; A7–A8 are hygiene that makes the rest
testable. The order above is the recommended order of work.
