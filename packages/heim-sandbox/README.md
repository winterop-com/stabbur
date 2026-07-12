# heim-sandbox

A tiny library: run a code snippet in a locked-down throwaway Docker container (no network,
read-only rootfs, capped memory/CPU/pids, wall-clock timeout). Shared by `heim-benchmark` (to
score candidate solutions) and `heim-mcp-exec` (the assistant's Python scratchpad).

```python
from heim_sandbox import run_code, docker_available
if docker_available():
    print(run_code("python", "print(6 * 7)").stdout)  # -> "42\n"
```
