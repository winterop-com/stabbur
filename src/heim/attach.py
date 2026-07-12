"""Attachment helpers shared by the CLI REPL and the Textual chat.

Turns files a user drags into the chat (their paths arrive as text) into the
right shape: images/audio become base64 ``data:`` URLs for a multimodal model,
while text/code/doc files are inlined into the message as fenced blocks so any
model can read them.
"""

import base64
import mimetypes
import shlex
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
# Text/doc files dragged in are inlined into the prompt (work with any model,
# unlike image/audio). Matched by extension (their MIME is often empty).
TEXT_EXTS = {
    ".txt", ".text", ".md", ".markdown", ".rst", ".json", ".jsonl", ".ndjson",
    ".csv", ".tsv", ".log", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".xml", ".html", ".htm", ".css", ".scss", ".py", ".pyi", ".js",
    ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".rb", ".java", ".kt",
    ".kts", ".scala", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".cs", ".php",
    ".swift", ".sql", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".r", ".lua",
    ".pl", ".pm", ".dart", ".ex", ".exs", ".clj", ".hs", ".ml", ".vue",
    ".svelte", ".tex", ".proto", ".graphql", ".gql",
}  # fmt: skip


def media_data_url(path: Path, default_mime: str) -> str:
    """Read a file into a base64 ``data:`` URL (mime guessed, else ``default_mime``)."""
    mime = mimetypes.guess_type(str(path))[0] or default_mime
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def inline_files(text: str, files: list[tuple[str, str]]) -> str:
    """Prepend attached text/doc files to a message as fenced blocks (context)."""
    if not files:
        return text
    blocks = "\n\n".join(f"Attached file: {name}\n```\n{content}\n```" for name, content in files)
    return f"{blocks}\n\n{text}" if text else blocks


def split_input_media(text: str) -> tuple[str, list[str], list[str], list[tuple[str, str]]]:
    """Pull dragged file paths out of a chat line (from a terminal drag-drop).

    Dragging a file into the terminal inserts its (possibly shell-escaped or
    quoted) path as text. Detect tokens that resolve to an existing image/audio
    file (attached as data URLs) or text/doc file (read as ``(name, contents)``
    for inlining), and return the remaining words as the message.
    """
    try:
        tokens = shlex.split(text)  # unescapes ``\ `` and quotes
    except ValueError:
        tokens = text.split()  # unbalanced quote (e.g. an apostrophe) -> plain split
    words: list[str] = []
    images: list[str] = []
    audios: list[str] = []
    files: list[tuple[str, str]] = []
    for tok in tokens:
        p = Path(tok).expanduser()
        ext = p.suffix.lower()
        if ext in IMAGE_EXTS and p.is_file():
            images.append(media_data_url(p, "image/png"))
        elif ext in AUDIO_EXTS and p.is_file():
            audios.append(media_data_url(p, "audio/wav"))
        elif ext in TEXT_EXTS and p.is_file():
            files.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
        else:
            words.append(tok)
    return " ".join(words), images, audios, files
