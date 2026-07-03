# Benchmark leaderboard

How local models score on kodo's coding and tool-use benchmarks. Each cell is the
percentage of problems fully passed (a problem passes only if every check passes).

Regenerate with `kodo benchmark leaderboard` after `kodo benchmark run ... --save`.

| Rank | Model | python | rust | tools-datetime | tools-utils | Overall |
|---|---|---|---|---|---|---|
| 1 | `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | 100% (6/6) | 100% (6/6) | 100% (4/4) | 100% (5/5) | **100%** (21/21) |
| 2 | `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit` | 100% (6/6) | 100% (6/6) | 100% (4/4) | 100% (5/5) | **100%** (21/21) |
| 3 | `lmstudio-community/gemma-4-31B-it-QAT-GGUF` | 100% (6/6) | 100% (6/6) | 100% (4/4) | 100% (5/5) | **100%** (21/21) |
| 4 | `mlx-community/Qwen3.6-27B-4bit` | 100% (6/6) | 100% (6/6) | 100% (4/4) | 100% (5/5) | **100%** (21/21) |
| 5 | `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | 100% (6/6) | 100% (6/6) | 100% (4/4) | 100% (5/5) | **100%** (21/21) |
| 6 | `unsloth/gpt-oss-20b-GGUF` | 100% (6/6) | 100% (6/6) | 100% (4/4) | 100% (5/5) | **100%** (21/21) |
| 7 | `unsloth/Qwen3.5-4B-GGUF` | 100% (6/6) | 83% (5/6) | 100% (4/4) | 80% (4/5) | **90%** (19/21) |
| 8 | `deepreinforce-ai/Ornith-1.0-9B-GGUF` | 100% (6/6) | 50% (3/6) | 100% (4/4) | 100% (5/5) | **86%** (18/21) |
| 9 | `lmstudio-community/Qwen3.5-4B-MLX-4bit` | 100% (6/6) | 33% (2/6) | 100% (4/4) | 100% (5/5) | **81%** (17/21) |
| 10 | `lmstudio-community/Qwen3.6-27B-GGUF` | 67% (4/6) | 50% (3/6) | 100% (4/4) | 100% (5/5) | **76%** (16/21) |
| 11 | `TheDrummer/Cydonia-24B-v4.3-GGUF` | 100% (6/6) | 100% (6/6) | 0% (0/4) | 0% (0/5) | **57%** (12/21) |
| 12 | `mradermacher/MN-Violet-Lotus-12B-GGUF` | 100% (6/6) | 100% (6/6) | 0% (0/4) | 0% (0/5) | **57%** (12/21) |
| 13 | `TheDrummer/Rocinante-X-12B-v1-GGUF` | 100% (6/6) | 83% (5/6) | 0% (0/4) | 0% (0/5) | **52%** (11/21) |

## Performance

| Model | Avg load | Avg response / problem |
|---|---|---|
| `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | 60.5s | 27.6s |
| `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit` | 203.0s | 4.9s |
| `lmstudio-community/gemma-4-31B-it-QAT-GGUF` | 259.0s | 41.4s |
| `mlx-community/Qwen3.6-27B-4bit` | 200.6s | 24.9s |
| `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | 166.1s | 1.8s |
| `unsloth/gpt-oss-20b-GGUF` | 119.6s | 4.9s |
| `unsloth/Qwen3.5-4B-GGUF` | 22.3s | 12.7s |
| `deepreinforce-ai/Ornith-1.0-9B-GGUF` | 56.3s | 8.9s |
| `lmstudio-community/Qwen3.5-4B-MLX-4bit` | 33.2s | 4.6s |
| `lmstudio-community/Qwen3.6-27B-GGUF` | 146.3s | 127.8s |
| `TheDrummer/Cydonia-24B-v4.3-GGUF` | 178.9s | 7.6s |
| `mradermacher/MN-Violet-Lotus-12B-GGUF` | 129.7s | 4.2s |
| `TheDrummer/Rocinante-X-12B-v1-GGUF` | 66.8s | 2.8s |
