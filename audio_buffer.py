from __future__ import annotations

import numpy as np
import time

SAMPLE_RATE = 16000
CHUNK_SECS = 0.4
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SECS)
SILENCE_THRESHOLD = 0.003
SILENCE_DURATION = 1.3
MAX_PHRASE_SECS = 8.0
MAX_PHRASE_SAMPLES = int(SAMPLE_RATE * MAX_PHRASE_SECS)


class AudioBuffer:
    """Accumulates audio chunks and detects phrase boundaries via silence."""

    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._silence_start: float | None = None
        self._has_speech: bool = False
        self._total_samples: int = 0

    def add_chunk(self, chunk: np.ndarray) -> np.ndarray | None:
        """Feed a mono float32 chunk. Returns complete phrase array or None."""
        rms = float(np.sqrt(np.mean(chunk ** 2)))

        if rms > SILENCE_THRESHOLD:
            self._has_speech = True
            self._silence_start = None
            self._chunks.append(chunk)
            self._total_samples += len(chunk)
        elif self._has_speech:
            self._chunks.append(chunk)
            self._total_samples += len(chunk)
            if self._silence_start is None:
                self._silence_start = time.time()
            elif time.time() - self._silence_start >= SILENCE_DURATION:
                return self._flush()

        if self._total_samples >= MAX_PHRASE_SAMPLES:
            return self._flush()

        return None

    def _flush(self) -> np.ndarray | None:
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks)
        self.reset()
        return audio

    def reset(self) -> None:
        self._chunks.clear()
        self._silence_start = None
        self._has_speech = False
        self._total_samples = 0
