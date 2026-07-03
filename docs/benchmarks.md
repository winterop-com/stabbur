# Benchmark leaderboard

How local models score on kodo's coding and tool-use benchmarks. Each cell is the
percentage of problems fully passed (a problem passes only if every check passes).

Regenerate with `kodo benchmark leaderboard` after `kodo benchmark run ... --save`.

| Rank | Model | python | rust | tools-datetime | tools-utils | Overall |
|---|---|---|---|---|---|---|
| 1 | `lmstudio-community/gemma-4-31B-it-QAT-GGUF` | 100% (11/11) | 100% (11/11) | 100% (4/4) | 100% (5/5) | **100%** (31/31) |
| 2 | `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit` | 100% (11/11) | 91% (10/11) | 100% (4/4) | 100% (5/5) | **97%** (30/31) |
| 3 | `mlx-community/Qwen3.6-27B-4bit` | 91% (10/11) | 100% (11/11) | 100% (4/4) | 100% (5/5) | **97%** (30/31) |
| 4 | `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | 100% (11/11) | 82% (9/11) | 100% (4/4) | 100% (5/5) | **94%** (29/31) |
| 5 | `unsloth/gpt-oss-20b-GGUF` | 100% (11/11) | 73% (8/11) | 100% (4/4) | 100% (5/5) | **90%** (28/31) |
| 6 | `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | 82% (9/11) | 82% (9/11) | 100% (4/4) | 100% (5/5) | **87%** (27/31) |
| 7 | `deepreinforce-ai/Ornith-1.0-9B-GGUF` | 91% (10/11) | 45% (5/11) | 100% (4/4) | 100% (5/5) | **77%** (24/31) |
| 8 | `unsloth/Qwen3.5-4B-GGUF` | 91% (10/11) | 45% (5/11) | 100% (4/4) | 80% (4/5) | **74%** (23/31) |
| 9 | `lmstudio-community/Qwen3.5-4B-MLX-4bit` | 91% (10/11) | 27% (3/11) | 100% (4/4) | 100% (5/5) | **71%** (22/31) |
| 10 | `TheDrummer/Cydonia-24B-v4.3-GGUF` | 82% (9/11) | 64% (7/11) | 0% (0/4) | 0% (0/5) | **52%** (16/31) |
| 11 | `TheDrummer/Rocinante-X-12B-v1-GGUF` | 82% (9/11) | 36% (4/11) | 0% (0/4) | 0% (0/5) | **42%** (13/31) |
| 12 | `mradermacher/MN-Violet-Lotus-12B-GGUF` | 73% (8/11) | 45% (5/11) | 0% (0/4) | 0% (0/5) | **42%** (13/31) |
| 13 | `lmstudio-community/Qwen3.6-27B-GGUF` | — | — | 100% (4/4) | 100% (5/5) | **100%** (9/9) |

## Performance

| Model | Avg load | Avg response / problem |
|---|---|---|
| `lmstudio-community/gemma-4-31B-it-QAT-GGUF` | 134.1s | 69.1s |
| `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit` | 200.5s | 6.6s |
| `mlx-community/Qwen3.6-27B-4bit` | 199.0s | 24.8s |
| `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | 166.0s | 2.7s |
| `unsloth/gpt-oss-20b-GGUF` | 122.1s | 8.8s |
| `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | 78.2s | 53.1s |
| `deepreinforce-ai/Ornith-1.0-9B-GGUF` | 56.4s | 10.4s |
| `unsloth/Qwen3.5-4B-GGUF` | 22.4s | 19.0s |
| `lmstudio-community/Qwen3.5-4B-MLX-4bit` | 43.6s | 5.7s |
| `TheDrummer/Cydonia-24B-v4.3-GGUF` | 185.6s | 12.5s |
| `TheDrummer/Rocinante-X-12B-v1-GGUF` | 67.1s | 4.4s |
| `mradermacher/MN-Violet-Lotus-12B-GGUF` | 130.1s | 6.0s |
| `lmstudio-community/Qwen3.6-27B-GGUF` | 192.8s | 35.7s |
