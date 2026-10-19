"""Voice and Indian languages through Sarvam AI, as a layer around the agent.

    guest speech, any Indian language
      -> Saaras (speech-to-text, translate mode): English text + detected language
      -> the agent, unchanged: same loop, prompt, tools and eval as typed English
      -> Sarvam-Translate: the English reply in the guest's language, line by line
      -> Bulbul (text-to-speech): that reply as audio, only when the guest asks to hear it

Keeping language at the edges means the agent, its prompt and its evaluation
stay English-only, and a new language costs nothing behind this module.
"""

import asyncio
import base64
import os
import re
from dataclasses import dataclass

from sarvamai import AsyncSarvamAI, BadRequestError, UnprocessableEntityError

from .generate_data import AREAS, CONCEPTS

ENGLISH = "en-IN"
# Bulbul speaks these 11 languages. Saaras and Sarvam-Translate cover 22;
# for the others the reply is translated but not spoken.
SPOKEN_LANGUAGES = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}
TTS_MAX_CHARS = 1500
# Words a general speech model mishears ("Koramangala" -> "Porumangala", "Italian" -> "Italy"):
# sent as recognition hints.
KEYTERMS = ["GoodFoods", *(a[0] for a in AREAS), *sorted({c for concept in CONCEPTS for c in concept[1]})]

LIST_MARKER = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$")
ODD_SPACES = re.compile(r"[\u00a0\u2009\u202f]")  # the model writes "8\u202fpm"; translators mangle it


def plain(text: str) -> str:
    """Text as a translator or speech engine should see it: no markdown bold, ordinary spaces."""
    return ODD_SPACES.sub(" ", text).replace("**", "")


@dataclass
class Heard:
    english: str  # what the guest said, translated to English for the agent
    language: str  # BCP-47 code of the language they spoke, e.g. "kn-IN"


def language_name(code: str) -> str:
    return SPOKEN_LANGUAGES.get(code, code)


class Voice:
    def __init__(self, api_key: str | None = None):
        self.client = AsyncSarvamAI(api_subscription_key=api_key or os.environ.get("SARVAM_API_KEY"))

    async def listen(self, audio: bytes) -> Heard:
        request = {"file": ("guest.wav", audio, "audio/wav"), "model": "saaras:v3", "mode": "translate"}
        try:
            result = await self.client.speech_to_text.transcribe(**request, keyterms=KEYTERMS)
        except (BadRequestError, UnprocessableEntityError):  # hints rejected: recognise without them
            result = await self.client.speech_to_text.transcribe(**request)
        return Heard(result.transcript.strip(), result.language_code or ENGLISH)

    async def translate(self, english: str, language: str) -> str:
        """Translate line by line, keeping list markers and blank lines, so a numbered list of
        outlets stays a list instead of collapsing into one paragraph."""
        if language == ENGLISH:
            return english

        async def line(text: str) -> str:
            match = LIST_MARKER.match(text)
            marker, body = (match[1], match[2]) if match else ("", text)
            if not body.strip():
                return text
            result = await self.client.text.translate(
                input=plain(body),
                source_language_code=ENGLISH,
                target_language_code=language,
                model="sarvam-translate:v1",  # native script throughout, all 22 languages
                numerals_format="international",  # keep times, ids and party sizes in digits
            )
            return marker + result.translated_text

        return "\n".join(await asyncio.gather(*(line(t) for t in english.split("\n"))))

    async def speak(self, text: str, language: str) -> bytes | None:
        """WAV audio of `text`, or None when Bulbul does not speak `language`."""
        if language not in SPOKEN_LANGUAGES:
            return None
        speech = await self.client.text_to_speech.convert(
            text=plain(text)[:TTS_MAX_CHARS], language_code=language, model="bulbul:v3"
        )
        return base64.b64decode(speech.audios[0])
