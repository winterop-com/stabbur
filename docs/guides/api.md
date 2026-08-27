# Using stabbur's API

`sb serve` is an HTTP service, not only a UI host. Anything the browser app does,
your own code can do — and for work that runs per request (moderation, captioning,
extraction), calling the API is the right shape. `sb chat -p` spawns a process and
resolves the library on every invocation, which is fine for sweeping a directory by
hand and wrong for a path a user waits on.

```bash
sb serve --port 2222                              # local runtimes
sb serve --port 2222 --upstream http://box:1234   # models run on another machine
```

Both expose the same surface. In upstream mode stabbur's agent loop, tools and confirm
gate still run locally; only the weights are elsewhere.

## Two endpoints, and which to use

| | `/v1/*` | `/api/chat` |
|---|---|---|
| Shape | OpenAI-compatible, byte-for-byte proxied | stabbur's own SSE event stream |
| Tools | no | yes — stabbur runs the MCP agent loop |
| Clients | any OpenAI SDK | hand-rolled |
| Use it for | classification, extraction, anything stateless | an assistant that must call tools |

**Reach for `/v1` unless you need tools.** It is a transparent stream-proxy to whatever
backend is loaded, so every OpenAI client library works unmodified — point its base URL
at `http://127.0.0.1:2222/v1` and give it any API key, which is ignored on a loopback
server.

Neither endpoint stores anything. A conversation is whatever messages you send, every
time; stabbur keeps no session state, so you own the history and there is nothing to clean
up server-side.

## A request with an image

Images ride inline as base64 data URLs in the standard `image_url` content part. There
is no upload endpoint and no file handle — the image is part of the message.

```bash
curl -s http://127.0.0.1:2222/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{
    "model": "<the loaded model>",
    "max_tokens": 16,
    "chat_template_kwargs": {"enable_thinking": false},
    "messages": [{"role": "user", "content": [
      {"type": "text", "text": "What colour is this image? One word."},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0..."}}
    ]}]
  }'
```

The model must actually have vision — a projector (`mmproj`) loaded alongside the
weights. `GET /api/status` names the loaded model and `GET /api/library` reports each
model's `vision` flag; sending an image to a text-only model wastes the call and
returns something confidently unrelated.

### Disable thinking, or a short answer comes back empty

This is the trap worth knowing before you build on it. A reasoning model spends
completion tokens thinking *before* it answers. With a tight `max_tokens` the budget is
gone before any content is produced, and you get a perfectly valid response with an
empty string in it:

```
max_tokens 24, thinking on   ->  content: ""     completion_tokens: 24
max_tokens 16, thinking off  ->  content: "Red"  completion_tokens: 2
```

Same model, same image. So for anything with a small expected answer:

- send `"chat_template_kwargs": {"enable_thinking": false}` (llama.cpp's dialect), and
- **assert the content is non-empty** and treat empty as a failure.

An empty string is not "no". Reading it as one is a fail-open bug wearing a 200.

## Structured output

Constrain the reply to a JSON schema instead of parsing prose. Send OpenAI's
`response_format`; stabbur passes it to the runtime verbatim, on `/v1` (proxied) and on
`/api/chat` alike.

```python
SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "confidence": {"type": "number"},
    },
    "required": ["sentiment", "confidence"],
    "additionalProperties": False,
}

r = httpx.post(
    f"{BASE}/chat/completions",
    timeout=60,
    json={
        "model": model,
        "messages": [{"role": "user", "content": "Classify: 'I love this drive'"}],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "sentiment", "strict": True, "schema": SCHEMA},
        },
    },
)
answer = json.loads(r.json()["choices"][0]["message"]["content"])
```

Three things about this are worth knowing before you rely on it.

**Use `json_schema`, not `json_object`.** llama-server enforces `{"type": "json_schema", …}`
and returns schema-conforming JSON. It **silently ignores** `{"type": "json_object"}` — you
get ordinary prose and a `JSONDecodeError` at the line above, with nothing anywhere saying
the constraint was dropped.

**It cannot be combined with tools.** The runtime compiles one grammar per request and
rejects the pair with `400 Failed to initialize samplers: failed to parse grammar`, which
names neither feature. On `/api/chat`, stabbur refuses first with a message that names the
fix: send `"use_tools": false`. On `/v1` there are no tools, so nothing to disable.

**A reasoning model still needs thinking off**, or the budget goes into a thought and the
constrained answer never arrives — see the section above.

## Classification, done defensively

The whole point of a filter is what it does when something goes wrong, so decide that
first: stabbur down, the model swapped out from under you, a timeout, a blank answer.
Failing open means unmoderated content ships the first time the box hiccups — which is
exactly when nobody is watching.

```python
import base64, httpx

BASE = "http://127.0.0.1:2222/v1"


def classify(path: str, model: str, prompt: str) -> str:
    """Return the model's one-word verdict, or raise. Never returns a default."""
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    r = httpx.post(
        f"{BASE}/chat/completions",
        timeout=60,
        json={
            "model": model,
            "max_tokens": 8,
            "temperature": 0,  # a classifier should not be creative
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ],
        },
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    if not text:  # thinking ate the budget, or the model refused
        raise RuntimeError(f"empty verdict for {path}")
    return text
```

Let it raise, and let the caller decide the disposition. A function that returns
`"safe"` when it could not reach the model is worse than one that fails.

Two things that make a batch faster: keep the text prompt **byte-identical** across
calls so the prefix stays cached (`usage.prompt_tokens_details.cached_tokens` shows it
working), and vary only the image. And do not ask a general vision model for a
calibrated score — it does not have one. An LLM verdict is a label, not a probability;
if you need a threshold you can tune, put a purpose-built classifier in front and use
the model only for the ambiguous middle.

## Tools, when you need them

`POST /api/chat` runs stabbur's agent loop: the model may call the MCP servers this stabbur
has attached, stabbur executes them, feeds the results back, and streams the whole thing
as SSE. Fields worth knowing: `use_tools`, `enabled_tools` (an allow-list of namespaced
names — `[]` means none, omitting it means all), `system_prompt`, `reasoning`, and the
sampling parameters. `GET /api/tools` lists what is attached.

This is stabbur's own event format, not OpenAI's — you parse `token`, `tool_call`,
`confirm`, `usage` and `done` events yourself. Use it when the tools are the point;
use `/v1` otherwise.

## Speech

`POST /v1/audio/speech` and `POST /v1/audio/transcriptions` follow the OpenAI shapes,
so an existing client works. See [Voice](voice.md).

## Access

A loopback server is reachable by anything on the machine. `sb config set host` and
`--host` control the bind address; a non-loopback bind should carry a token, and the
browser guard that protects `/api` against drive-by cross-site calls is described in
[the architecture notes](../architecture.md). Do not expose an unauthenticated stabbur to a
network you do not control — it can load models, run MCP tools, and read whatever those
tools reach.
