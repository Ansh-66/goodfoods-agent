"""The voice layer against a stand-in Sarvam client: which calls happen for which language."""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from sarvamai import BadRequestError

from goodfoods.voice import ENGLISH, KEYTERMS, SPOKEN_LANGUAGES, Voice


class FakeSarvam:
    def __init__(self, heard_language="kn-IN", reject_keyterms=False):
        self.calls = []
        self.inputs = []
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
        self.inputs.append(kwargs["input"])
        return SimpleNamespace(translated_text=f"<{kwargs['input']}>")

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
    assert (heard.english, heard.language, audio) == ("Table for two tomorrow", "kn-IN", b"WAV")
    assert text == "<Booked: GF7K2QXM at 20:00.>"
    assert fake.calls == [
        ("transcribe", "translate"),
        ("translate", "kn-IN", "sarvam-translate:v1"),
        ("speak", "kn-IN"),
    ]


def test_english_is_not_translated():
    fake = FakeSarvam()
    assert asyncio.run(voice_with(fake).translate("Done.", "en-IN")) == "Done." and fake.calls == []


def test_language_bulbul_cannot_speak_is_translated_but_not_spoken():
    fake = FakeSarvam()
    voice = voice_with(fake)
    asyncio.run(voice.translate("Done.", "ur-IN"))
    assert asyncio.run(voice.speak("Done.", "ur-IN")) is None
    assert fake.calls == [("translate", "ur-IN", "sarvam-translate:v1")]


REPLY = (
    "Here are three options near Koramangala:\n"
    "\n"
    "1. GoodFoods Trattoria, Jayanagar \u2013 3\u202fkm away, 4.7\u202f\u2605, free at 8\u202fpm.\n"
    "2. GoodFoods Trattoria, Ulsoor \u2013 5.7\u202fkm away.\n"
    "- Your booking id is **GFEEREKE**.\n"
    "Which one would you like?"
)


@pytest.mark.parametrize("language", [code for code in SPOKEN_LANGUAGES if code != ENGLISH] + ["ur-IN"])
def test_translation_keeps_lists_and_clean_spacing_in_every_language(language):
    fake = FakeSarvam()
    translated = asyncio.run(voice_with(fake).translate(REPLY, language))
    assert translated.split("\n") == [
        "<Here are three options near Koramangala:>",
        "",
        "1. <GoodFoods Trattoria, Jayanagar \u2013 3 km away, 4.7 \u2605, free at 8 pm.>",
        "2. <GoodFoods Trattoria, Ulsoor \u2013 5.7 km away.>",
        "- <Your booking id is GFEEREKE.>",
        "<Which one would you like?>",
    ]
    assert not any("\u202f" in text or "**" in text for text in fake.inputs)


def test_area_names_are_sent_as_hints_and_dropped_if_rejected():
    assert {"Koramangala", "Italian"} <= set(KEYTERMS)
    assert len(KEYTERMS) <= 50 and all(len(term) <= 64 for term in KEYTERMS)  # the API's limits
    heard = asyncio.run(voice_with(FakeSarvam(reject_keyterms=True)).listen(b"audio"))
    assert heard.english == "Table for two tomorrow"
