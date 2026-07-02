# kodo-mcp-benchmark

Language coding benchmarks for local models. A *suite* is a set of coding
*problems*; each problem gives a prompt plus hidden test cases. A candidate
solution is a full program that reads stdin / argv and writes stdout — so Python
and Rust are scored through the exact same path.

Every candidate runs in a **throwaway Docker container**: no network, read-only
rootfs (source mounted read-only, an exec tmpfs for `/tmp`), capped memory / CPU /
pids, and a wall-clock timeout. Model-generated code never touches the host.

## Two ways in

- **`kodo benchmark <suite>`** (the driver in kodo) prompts a served model once per
  problem, extracts the code, runs it in the sandbox, and prints a scored report.
  This is the deterministic, repeatable path.
- **MCP tools** (`kodo-mcp-benchmark` over stdio): `list_suites`, `get_problem`,
  `evaluate`, `run_code` — for any MCP host, or a model self-driving in chat.

```
kodo benchmark python-basics --model Qwen3.5-4B
kodo-mcp-benchmark                     # or: python -m kodo_mcp_benchmark
```

## Suites

Bundled: `python-basics`, `rust-basics` (same problems, so scores compare across
languages). Suites are TOML under `src/kodo_mcp_benchmark/suites/`:

```toml
name = "python-basics"
language = "python"

[[problems]]
id = "sum-two"
prompt = "Read two integers on a line and print their sum."
[[problems.tests]]
stdin = "2 3\n"
expected_stdout = "5"
```

Add a language by adding one entry to `_RUNTIMES` in `core.py` (its image + how to
build/run the source); everything else — scoring, the driver, the tools — is shared.

This package follows the `kodo-mcp-datetime` template (src layout, `__init__` +
`__main__` + `app.py`), with `core.py` holding the suite/executor logic.
