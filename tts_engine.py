from __future__ import annotations

import logging
import os
import threading

import numpy as np

log = logging.getLogger(__name__)

XTTS_SR = 24000
OPENAI_TTS_SR = 24000


class TTSEngine:
    """Coqui XTTS-v2 (streaming, voice cloning) + OpenAI TTS-HD fallback.

    A single instance is shared between both translation pipelines.
    All public methods are thread-safe via an internal lock.
    """

    def __init__(self, config: dict):
        self._openai_key = config["openai_api_key"]
        self._voice_sample = config.get("voice_sample_path", "")
        self._lock = threading.Lock()
        self._coqui_ready = False
        self._sample_rate = OPENAI_TTS_SR

        # Internal refs set by _init_coqui
        self._model = None
        self._gpt_cond_latent = None
        self._speaker_embedding = None

        if self._voice_sample and os.path.exists(self._voice_sample):
            try:
                import TTS  # noqa: F401
                self._init_coqui()
            except ImportError:
                log.info("Coqui TTS not installed — using OpenAI TTS")
            except Exception as exc:
                log.warning("Coqui XTTS-v2 init failed — using OpenAI fallback: %s", exc)

    # ── Coqui bootstrap ───────────────────────────────────────────

    def _init_coqui(self):
        try:
            import torch
            from TTS.api import TTS
        except ImportError:
            log.warning("TTS package not installed. Run: pip install TTS")
            return

        log.info("Loading XTTS-v2 (first run downloads ~2 GB)...")
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")

        device = "cpu"
        try:
            if torch.cuda.is_available():
                tts.to("cuda")
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                tts.to("mps")
                device = "mps"
        except Exception as exc:
            log.warning("GPU move failed (%s), staying on CPU", exc)

        log.info("TTS device: %s", device)

        self._model = tts.synthesizer.tts_model
        self._gpt_cond_latent, self._speaker_embedding = (
            self._model.get_conditioning_latents(audio_path=[self._voice_sample])
        )
        self._sample_rate = tts.synthesizer.output_sample_rate
        self._coqui_ready = True
        log.info("XTTS-v2 ready  sr=%d  device=%s", self._sample_rate, device)

    # ── Public API ────────────────────────────────────────────────

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def has_voice_cloning(self) -> bool:
        return self._coqui_ready

    def synthesize_stream(self, text: str, language: str = "en"):
        """Yield float32 numpy chunks. Lock held for entire generation."""
        with self._lock:
            if self._coqui_ready:
                yield from self._coqui_stream(text, language)
            else:
                audio, _ = self._openai_tts(text)
                yield audio

    def synthesize(self, text: str, language: str = "en") -> tuple[np.ndarray, int]:
        """Return (float32_array, sample_rate)."""
        with self._lock:
            if self._coqui_ready:
                return self._coqui_full(text, language)
            return self._openai_tts(text)

    # ── Coqui internals ──────────────────────────────────────────

    def _coqui_stream(self, text: str, language: str):
        chunks = self._model.inference_stream(
            text,
            language,
            self._gpt_cond_latent,
            self._speaker_embedding,
            stream_chunk_size=20,
        )
        for chunk in chunks:
            yield chunk.cpu().numpy().squeeze().astype(np.float32)

    def _coqui_full(self, text: str, language: str) -> tuple[np.ndarray, int]:
        out = self._model.inference(
            text,
            language,
            self._gpt_cond_latent,
            self._speaker_embedding,
        )
        return np.array(out["wav"], dtype=np.float32), self._sample_rate

    # ── OpenAI fallback ──────────────────────────────────────────

    def _openai_tts(self, text: str) -> tuple[np.ndarray, int]:
        from openai import OpenAI

        client = OpenAI(api_key=self._openai_key)
        resp = client.audio.speech.create(
            model="tts-1-hd",
            voice="onyx",
            input=text,
            response_format="pcm",
        )
        raw = resp.read()
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, OPENAI_TTS_SR
