# Benchmark leaderboard

How local models score on kodo's coding and tool-use benchmarks. Each cell is the
percentage of problems fully passed (a problem passes only if every check passes).

Regenerate with `kodo benchmark leaderboard` after `kodo benchmark run ... --save`.

| Rank | Model | python | rust | tools-datetime | tools-dhis2 | tools-utils | Overall |
|---|---|---|---|---|---|---|---|
| 1 | `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | 100% (11/11) | 82% (9/11) | 100% (4/4) | 92% (11/12) | 100% (5/5) | **93%** (40/43) |
| 2 | `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | 82% (9/11) | 82% (9/11) | 100% (4/4) | 100% (12/12) | 100% (5/5) | **91%** (39/43) |
| 3 | `unsloth/gpt-oss-20b-GGUF` | 100% (11/11) | 73% (8/11) | 100% (4/4) | 92% (11/12) | 100% (5/5) | **91%** (39/43) |
| 4 | `deepreinforce-ai/Ornith-1.0-9B-GGUF` | 91% (10/11) | 45% (5/11) | 100% (4/4) | 100% (12/12) | 100% (5/5) | **84%** (36/43) |
| 5 | `unsloth/Qwen3.5-4B-GGUF` | 91% (10/11) | 45% (5/11) | 100% (4/4) | 92% (11/12) | 80% (4/5) | **79%** (34/43) |
| 6 | `lmstudio-community/gemma-4-31B-it-QAT-GGUF` | 100% (11/11) | 100% (11/11) | 100% (4/4) | — | 100% (5/5) | **100%** (31/31) |
| 7 | `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit` | 100% (11/11) | 91% (10/11) | 100% (4/4) | — | 100% (5/5) | **97%** (30/31) |
| 8 | `mlx-community/Qwen3.6-27B-4bit` | 91% (10/11) | 100% (11/11) | 100% (4/4) | — | 100% (5/5) | **97%** (30/31) |
| 9 | `lmstudio-community/Qwen3.5-4B-MLX-4bit` | 91% (10/11) | 27% (3/11) | 100% (4/4) | — | 100% (5/5) | **71%** (22/31) |
| 10 | `lmstudio-community/Qwen3.6-27B-GGUF` | — | — | 100% (4/4) | 100% (12/12) | 100% (5/5) | **100%** (21/21) |
| 11 | `TheDrummer/Cydonia-24B-v4.3-GGUF` | 82% (9/11) | 64% (7/11) | 0% (0/4) | — | 0% (0/5) | **52%** (16/31) |
| 12 | `TheDrummer/Rocinante-X-12B-v1-GGUF` | 82% (9/11) | 36% (4/11) | 0% (0/4) | 0% (0/12) | 0% (0/5) | **30%** (13/43) |
| 13 | `mradermacher/MN-Violet-Lotus-12B-GGUF` | 73% (8/11) | 45% (5/11) | 0% (0/4) | 0% (0/12) | 0% (0/5) | **30%** (13/43) |

## Performance

| Model | Avg load | Avg response / problem |
|---|---|---|
| `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | 133.7s | 3.7s |
| `lmstudio-community/gemma-4-12B-it-QAT-GGUF` | 78.1s | 57.3s |
| `unsloth/gpt-oss-20b-GGUF` | 121.3s | 13.3s |
| `deepreinforce-ai/Ornith-1.0-9B-GGUF` | 56.2s | 10.9s |
| `unsloth/Qwen3.5-4B-GGUF` | 18.3s | 16.6s |
| `lmstudio-community/gemma-4-31B-it-QAT-GGUF` | 134.1s | 69.1s |
| `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit` | 200.5s | 6.6s |
| `mlx-community/Qwen3.6-27B-4bit` | 199.0s | 24.8s |
| `lmstudio-community/Qwen3.5-4B-MLX-4bit` | 43.6s | 5.7s |
| `lmstudio-community/Qwen3.6-27B-GGUF` | 181.2s | 33.0s |
| `TheDrummer/Cydonia-24B-v4.3-GGUF` | 185.6s | 12.5s |
| `TheDrummer/Rocinante-X-12B-v1-GGUF` | 68.1s | 4.2s |
| `mradermacher/MN-Violet-Lotus-12B-GGUF` | 131.0s | 5.6s |
