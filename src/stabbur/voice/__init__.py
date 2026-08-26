"""Voice models (TTS / STT) as a first-class category, separate from chat LLMs.

``registry`` declares the known models (and how their voices work); ``catalog`` finds where
they live (HF cache vs the library's ``voice/`` bucket). Runtime/serving and the web Voice
section build on these. Kokoro stays the lightweight in-chat voice; heavier models (Chatterbox, …)
are for the standalone Voice section.
"""

from stabbur.voice.catalog import VoicePresence, discover, hf_hub_cache, voice_dir
from stabbur.voice.registry import BUILTIN, Backend, VoiceKind, VoiceMode, VoiceModel, by_repo, chat_voice, get

__all__ = [
    "BUILTIN",
    "Backend",
    "VoiceKind",
    "VoiceMode",
    "VoiceModel",
    "VoicePresence",
    "by_repo",
    "chat_voice",
    "discover",
    "get",
    "hf_hub_cache",
    "voice_dir",
]
