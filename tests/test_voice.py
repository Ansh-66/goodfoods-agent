"""The voice layer against a stand-in Sarvam client: which calls happen for which language."""

import asyncio
import base64
from types import SimpleNamespace

from goodfoods.voice import Voice


class FakeSarvam:
    def __init__(self, heard_language="kn-IN"):
        self.calls = []
        self.heard_language = heard_language
        self.speech_to_text = SimpleNamespace(transcribe=self._transcribe)
        self.text = SimpleNamespace(translate=self._translate)
        self.text_to_speech = SimpleNamespace(convert=self._convert)

    async def _transcribe(self, **kwargs):
        self.calls.append(("transcribe", kwargs["mode"]))
        return SimpleNamespace(transcript=" Table for two tomorrow ", language_code=self.heard_language)

    async def _translate(self, **kwargs):
        self.calls.append(("translate", kwargs["target_language_code"], kwargs["model"]))
        return SimpleNamespace(translated_text="<translated>")

    async def _convert(self, **kwargs):
        self.calls.append(("speak", kwargs["language_code"]))
        return SimpleNamespace(audios=[base64.b64encode(b"WAV").decode()])


def voice_with(fake):
    voice = Voice(api_key="test")
    voice.client = fake
    return voice


def test_speech_in_an_indian_language_round_trips():
    fake = FakeSarvam("kn-IN")
    voice = voice_with(fake)
    heard = asyncio.run(voice.listen(b"audio"))
    spoken = asyncio.run(voice.reply("Booked: GF7K2QXM at 20:00.", heard.language))
    assert (heard.english, heard.language) == ("Table for two tomorrow", "kn-IN")
    assert (spoken.text, spoken.audio) == ("<translated>", b"WAV")
    assert fake.calls == [("transcribe", "translate"), ("translate", "kn-IN", "mayura:v1"), ("speak", "kn-IN")]


def test_english_is_spoken_without_translation():
    fake = FakeSarvam()
    spoken = asyncio.run(voice_with(fake).reply("Done.", "en-IN"))
    assert spoken.text == "Done." and fake.calls == [("speak", "en-IN")]


def test_language_bulbul_cannot_speak_is_translated_but_not_spoken():
    fake = FakeSarvam()
    spoken = asyncio.run(voice_with(fake).reply("Done.", "ur-IN"))
    assert spoken.audio is None and fake.calls == [("translate", "ur-IN", "sarvam-translate:v1")]
