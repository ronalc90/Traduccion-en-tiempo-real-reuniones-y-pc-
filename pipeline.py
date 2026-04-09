from __future__ import annotations

import io
import logging
import queue
import threading
import time
import wave

import numpy as np
import sounddevice as sd
from openai import OpenAI

from audio_buffer import AudioBuffer, SAMPLE_RATE, CHUNK_SIZE
from tts_engine import TTSEngine

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a professional simultaneous interpreter with expertise in business and "
    "technical conversations. Translate naturally and fluently — not word for word. "
    "Preserve the speaker's tone, intent, and register. If the speaker is formal, keep "
    "it formal. If casual, keep it casual. Never add explanations or notes, output only "
    "the translation."
)


def find_device_index(name: str, kind: str = "input") -> int | None:
    """Find audio device index by partial name match."""
    if not name:
        return None
    devices = sd.query_devices()
    name_lower = name.lower()
    for i, d in enumerate(devices):
        if name_lower in d["name"].lower():
            ch_key = "max_input_channels" if kind == "input" else "max_output_channels"
            if d[ch_key] > 0:
                return i
    return None


def list_devices() -> dict:
    """Return dict with 'input' and 'output' device lists as (index, name) tuples."""
    devices = sd.query_devices()
    inputs = []
    outputs = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            inputs.append((i, d["name"]))
        if d["max_output_channels"] > 0:
            outputs.append((i, d["name"]))
    return {"input": inputs, "output": outputs}


def _audio_to_wav(audio: np.ndarray) -> bytes:
    """Convert float32 mono array to WAV bytes at SAMPLE_RATE."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class TranslationPipeline:
    """Single direction: capture -> STT -> translate -> TTS (streaming) -> playback."""

    def __init__(
        self,
        config: dict,
        direction: str,
        on_text,
        on_status,
        tts_gate: threading.Event,
        tts_engine: TTSEngine,
        src_lang: str = "",
        tgt_lang: str = "",
    ):
        """
        direction:  'mine' (mic -> VB-Cable) or 'theirs' (loopback -> headphones)
        on_text:    callback(direction, original, translated)
        on_status:  callback(direction, status_string)
        tts_gate:   Event — set while TTS plays on headphones (pauses loopback capture)
        tts_engine: shared TTSEngine instance
        src_lang:   source language code (e.g. 'es', 'en')
        tgt_lang:   target language code (e.g. 'en', 'es')
        """
        self._direction = direction
        self._on_text = on_text
        self._on_status = on_status
        self._tts_gate = tts_gate
        self._tts = tts_engine

        self._openai = OpenAI(api_key=config["openai_api_key"])
        self._buffer = AudioBuffer()
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._running = False
        self._translation_paused = False
        self._mic_muted = False
        self._tts_muted = False
        self._stream_lock = threading.Lock()
        self._capture_thread: threading.Thread | None = None
        self._process_thread: threading.Thread | None = None

        self._src_lang = src_lang or ("es" if direction == "mine" else "en")
        self._tgt_lang = tgt_lang or ("en" if direction == "mine" else "es")

        if direction == "mine":
            self._capture_dev = find_device_index(config["mic_device"], "input")
            self._play_dev = find_device_index(config["vb_cable_device"], "output")
        else:
            # Capture ALL system audio via loopback (BlackHole 2ch)
            self._capture_dev = find_device_index(config["loopback_device"], "input")
            self._play_dev = find_device_index(config["output_device"], "output")

    # ── Properties ────────────────────────────────────────────────

    @property
    def translation_paused(self) -> bool:
        return self._translation_paused

    @translation_paused.setter
    def translation_paused(self, value: bool):
        self._translation_paused = value

    @property
    def mic_muted(self) -> bool:
        return self._mic_muted

    @mic_muted.setter
    def mic_muted(self, value: bool):
        self._mic_muted = value

    @property
    def tts_muted(self) -> bool:
        return self._tts_muted

    @tts_muted.setter
    def tts_muted(self, value: bool):
        self._tts_muted = value

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._buffer.reset()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._process_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._capture_thread.start()
        self._process_thread.start()

    def stop(self):
        self._running = False
        self._buffer.reset()
        # Wait for threads to finish closing their streams
        for t in (self._capture_thread, self._process_thread):
            if t and t.is_alive():
                t.join(timeout=3.0)

    # ── Capture thread ────────────────────────────────────────────

    def _capture_loop(self):
        def callback(indata, frames, time_info, status):
            if not self._running:
                return
            if status:
                log.warning("[%s] capture status: %s", self._direction, status)
            if self._mic_muted:
                return
            if self._direction == "theirs" and self._tts_gate.is_set():
                return
            chunk = indata[:, 0].copy()
            phrase = self._buffer.add_chunk(chunk)
            if phrase is not None:
                self._queue.put(phrase)

        try:
            log.info("[%s] opening capture device index=%s", self._direction, self._capture_dev)
            stream = sd.InputStream(
                device=self._capture_dev,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=CHUNK_SIZE,
                callback=callback,
            )
            stream.start()
            self._on_status(self._direction, "listening")
            while self._running:
                time.sleep(0.1)
            with self._stream_lock:
                stream.stop()
                stream.close()
        except Exception as exc:
            log.error("Capture error [%s]: %s", self._direction, exc)
            self._on_status(self._direction, "error")

    # ── Processing thread ─────────────────────────────────────────

    def _process_loop(self):
        while self._running:
            try:
                phrase = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Warn if pipeline is falling behind
            backlog = self._queue.qsize()
            if backlog > 2:
                log.warning("[%s] queue backlog: %d phrases", self._direction, backlog)

            self._on_status(self._direction, "transcribing")
            text = self._transcribe(phrase)
            if not text or not text.strip():
                self._on_status(self._direction, "listening")
                continue

            if self._translation_paused:
                self._on_text(self._direction, text, None)
                self._on_status(self._direction, "paused")
                continue

            self._on_status(self._direction, "translating")
            translated = self._translate(text)
            self._on_text(self._direction, text, translated)

            if not self._tts_muted:
                self._on_status(self._direction, "speaking")
                self._speak(translated)
            self._on_status(self._direction, "listening")

    # ── STT ───────────────────────────────────────────────────────

    def _transcribe(self, audio: np.ndarray) -> str:
        try:
            wav_bytes = _audio_to_wav(audio)
            buf = io.BytesIO(wav_bytes)
            buf.name = "audio.wav"
            result = self._openai.audio.transcriptions.create(
                model="gpt-4o-transcribe",
                file=buf,
                language=self._src_lang,
            )
            return result.text
        except Exception as exc:
            log.error("STT error: %s", exc)
            return ""

    # ── Translation ───────────────────────────────────────────────

    def _translate(self, text: str) -> str:
        lang_names = {
            "es": "Spanish", "en": "English", "pt": "Portuguese",
            "fr": "French", "de": "German", "it": "Italian",
            "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
            "ar": "Arabic", "hi": "Hindi", "ru": "Russian", "tr": "Turkish",
        }
        target = lang_names.get(self._tgt_lang, self._tgt_lang)
        try:
            resp = self._openai.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.1,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Translate to {target}: {text}"},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            log.error("Translation error: %s", exc)
            return f"[Error: {exc}]"

    # ── TTS streaming playback ────────────────────────────────────

    def _speak(self, text: str):
        if self._direction == "theirs":
            self._tts_gate.set()
        try:
            sr = self._tts.sample_rate
            stream = sd.OutputStream(
                device=self._play_dev,
                samplerate=sr,
                channels=1,
                dtype="float32",
            )
            stream.start()
            for chunk in self._tts.synthesize_stream(text, self._tgt_lang):
                if not self._running:
                    break
                stream.write(chunk.reshape(-1, 1))
            with self._stream_lock:
                stream.stop()
                stream.close()
        except Exception as exc:
            log.error("TTS playback error [%s]: %s", self._direction, exc)
        finally:
            if self._direction == "theirs":
                self._tts_gate.clear()
