"""The voice layer against a stand-in Sarvam client: which calls happen for which language."""

import asyncio
import base64
from types import SimpleNamespace

from sarvamai import BadRequestError

from goodfoods.voice import KEYTERMS, Voice


class FakeSarvam:
    def __init__(self, heard_language="kn-IN", reject_keyterms=False):
        self.calls = []
        self.reject_keyterms = reject_keyterms
        self.heard_language = heard_language
        self.speech_to_text = SimpleNamespace(transcribe=self._transcribe)
        self.text = SimpleNamespace(translate=self._translate)
        self.text_to_speech = SimpleNamespace(convert=self._convert)

    async def _transcribe(self, **kwargs):
        if self.reject_keyterms and "keyterms" in kwargs:
            raise BadRequestError(body="keyterms not allowed")
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
    text = asyncio.run(voice.translate("Booked: GF7K2QXM at 20:00.", heard.language))
    audio = asyncio.run(voice.speak(text, heard.language))
    assert (heard.english, heard.language, text, audio) == ("Table for two tomorrow", "kn-IN", "<translated>", b"WAV")
    assert fake.calls == [("transcribe", "translate"), ("translate", "kn-IN", "mayura:v1"), ("speak", "kn-IN")]


def test_english_is_not_translated():
    fake = FakeSarvam()
    assert asyncio.run(voice_with(fake).translate("Done.", "en-IN")) == "Done." and fake.calls == []


def test_language_bulbul_cannot_speak_is_translated_but_not_spoken():
    fake = FakeSarvam()
    voice = voice_with(fake)
    asyncio.run(voice.translate("Done.", "ur-IN"))
    assert asyncio.run(voice.speak("Done.", "ur-IN")) is None
    assert fake.calls == [("translate", "ur-IN", "sarvam-translate:v1")]


def test_area_names_are_sent_as_hints_and_dropped_if_rejected():
    assert "Koramangala" in KEYTERMS
    heard = asyncio.run(voice_with(FakeSarvam(reject_keyterms=True)).listen(b"audio"))
    assert heard.english == "Table for two tomorrow"
