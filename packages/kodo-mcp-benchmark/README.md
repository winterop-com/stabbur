# kodo-mcp-benchmark

Language + tool-use benchmarks for local models. A *suite* is a set of *problems*:

- **`code`** problems give a prompt; the model writes a full program that reads stdin
  and writes stdout, scored by comparing output. Python and Rust go through the same
  path (add a language with one `_RUNTIMES` entry in `core.py`).
- **`tool`** problems give a prompt plus MCP servers to attach; scored on whether the
  model calls the expected tool with the expected args **and** answers correctly.

Every `code` candidate runs in a **throwaway Docker container**: no network, read-only
rootfs (source mounted read-only, an exec tmpfs for `/tmp`), capped memory / CPU / pids,
and a wall-clock timeout. Model-generated code never touches the host.

Bundled suites: `python`, `rust` (tiered basics / intermediate / advanced) and
`tools-datetime` (tool use against `kodo-mcp-datetime`).

## Usage

```
kodo benchmark list
kodo benchmark run python --model Qwen3.5-4B-GGUF          # one model, one suite
kodo benchmark run tools-datetime --model <name>          # tool-use suite
kodo benchmark run python --all --save --skip-done        # every model, resumable
kodo benchmark leaderboard                                # regenerate docs/benchmarks.md
```

Results saved with `--save` land in `benchmarks/results/` as one JSON per (suite, model);
`kodo benchmark leaderboard` renders them into a Markdown leaderboard for the docs.

Also an MCP server (`kodo-mcp-benchmark` over stdio): `list_suites`, `get_problem`,
`evaluate`, `run_code` — for any MCP host or a model self-driving in chat.

## Suite format

```toml
name = "python"
language = "python"        # type defaults to "code"

[[problems]]
id = "sum-two"
difficulty = "basics"      # basics | intermediate | advanced
prompt = "Read two integers on a line and print their sum."
[[problems.tests]]
stdin = "2 3\n"
expected_stdout = "5"
```

A `tool` suite sets `type = "tool"` and each problem gives `servers`, `expect_tool`,
`expect_args`, and `expect_answer_contains` instead of `tests`.

This package follows the `kodo-mcp-datetime` template (src layout, `__init__` +
`__main__` + `app.py`); `core.py` holds the suites, executor, scoring, and leaderboard,
and `plugin.py` is the pluginkit extension contributing `kodo benchmark`.
