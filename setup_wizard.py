from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import wave

import numpy as np
import sounddevice as sd

from config import load_config, save_config, VOICE_SAMPLE_PATH
from pipeline import list_devices, find_device_index

# ── Theme ─────────────────────────────────────────────────────────
BG = "#111827"
CARD = "#1f2937"
BLUE = "#38bdf8"
PINK = "#f472b6"
GREEN = "#4ade80"
FG = "#e5e7eb"
FG_DIM = "#9ca3af"
ENTRY_BG = "#374151"

LOOPBACK_KW = ["stereo mix", "vb-cable", "loopback", "virtual", "blackhole"]
VB_CABLE_KW = ["cable input", "vb-cable", "blackhole"]


class SetupWizard:
    """First-time config: API key, audio devices, voice sample recording."""

    def __init__(self, on_complete=None):
        self._on_complete = on_complete
        self._recording = False
        self._recorded_audio: np.ndarray | None = None
        self._record_start: float = 0
        self._stream = None

        self._root = tk.Tk()
        self._root.title("Traductor — Configuracion")
        self._root.configure(bg=BG)
        self._root.geometry("720x780")
        self._root.resizable(False, False)

        self._build_ui(load_config())

    # ── UI ─────────────────────────────────────────────────────────

    def _build_ui(self, cfg: dict):
        canvas = tk.Canvas(self._root, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self._root, orient="vertical", command=canvas.yview)
        self._frame = tk.Frame(canvas, bg=BG)
        self._frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        pad = {"padx": 20, "pady": (10, 0)}

        # Title
        tk.Label(
            self._frame, text="Configuracion del Traductor",
            font=("Helvetica", 18, "bold"), fg=BLUE, bg=BG,
        ).pack(pady=(20, 10))

        # ── API Key ──────────────────────────────────────────────
        self._section("OpenAI API Key")
        tk.Label(
            self._frame, text="Se usa para STT (gpt-4o-transcribe), traduccion (gpt-4o) y TTS fallback.",
            fg=FG_DIM, bg=BG, wraplength=660, justify="left",
        ).pack(padx=20, anchor="w")

        self._openai_var = tk.StringVar(value=cfg.get("openai_api_key", ""))
        self._entry(self._openai_var, show="*")

        # ── Devices ──────────────────────────────────────────────
        self._section("Dispositivos de Audio")

        devs = list_devices()
        in_names = [n for _, n in devs["input"]]
        out_names = [n for _, n in devs["output"]]

        tk.Label(self._frame, text="Microfono (tu voz)", fg=FG, bg=BG).pack(**pad, anchor="w")
        self._mic_var = tk.StringVar(value=cfg.get("mic_device", ""))
        self._combo(self._mic_var, in_names)

        tk.Label(
            self._frame, text="Loopback (audio del sistema / meet)", fg=FG, bg=BG,
        ).pack(**pad, anchor="w")
        self._loopback_var = tk.StringVar(
            value=cfg.get("loopback_device", "") or self._auto(in_names, LOOPBACK_KW),
        )
        self._combo(self._loopback_var, in_names)

        tk.Label(self._frame, text="Auriculares (escuchas espanol)", fg=FG, bg=BG).pack(**pad, anchor="w")
        self._output_var = tk.StringVar(value=cfg.get("output_device", ""))
        self._combo(self._output_var, out_names)

        tk.Label(
            self._frame, text="VB-Cable Input (el meet escucha ingles)", fg=FG, bg=BG,
        ).pack(**pad, anchor="w")
        self._vbcable_var = tk.StringVar(
            value=cfg.get("vb_cable_device", "") or self._auto(out_names, VB_CABLE_KW),
        )
        self._combo(self._vbcable_var, out_names)

        # ── Voice sample ─────────────────────────────────────────
        self._section("Muestra de Voz (Coqui XTTS-v2)")

        tk.Label(
            self._frame,
            text=(
                "Graba ~30-60 s de tu voz para clonarla localmente con XTTS-v2.\n"
                "Si no grabas, se usara la voz 'onyx' de OpenAI como fallback."
            ),
            fg=FG_DIM, bg=BG, wraplength=660, justify="left",
        ).pack(padx=20, pady=(5, 5), anchor="w")

        # Existing sample indicator
        has_sample = cfg.get("voice_sample_path", "") and os.path.exists(
            cfg.get("voice_sample_path", ""),
        )
        self._sample_status = tk.Label(
            self._frame,
            text="Muestra existente encontrada" if has_sample else "Sin muestra de voz",
            fg=GREEN if has_sample else FG_DIM, bg=BG,
        )
        self._sample_status.pack(padx=20, anchor="w")

        btn_frame = tk.Frame(self._frame, bg=BG)
        btn_frame.pack(padx=20, pady=8, anchor="w")

        self._rec_btn = tk.Button(
            btn_frame, text="Grabar muestra de voz",
            bg=PINK, fg="#111827", font=("Helvetica", 11, "bold"),
            activebackground="#f9a8d4", relief="flat", padx=12, pady=4,
            command=self._toggle_recording,
        )
        self._rec_btn.pack(side="left")

        self._timer_label = tk.Label(
            btn_frame, text="", fg=FG_DIM, bg=BG, font=("Helvetica", 11),
        )
        self._timer_label.pack(side="left", padx=10)

        # ── Save ─────────────────────────────────────────────────
        tk.Button(
            self._frame, text="Guardar y continuar",
            bg=BLUE, fg="#111827", font=("Helvetica", 13, "bold"),
            activebackground="#7dd3fc", relief="flat", padx=20, pady=8,
            command=self._save,
        ).pack(pady=30)

    # ── Helpers ───────────────────────────────────────────────────

    def _section(self, title: str):
        tk.Frame(self._frame, bg=FG_DIM, height=1).pack(fill="x", padx=20, pady=(15, 5))
        tk.Label(
            self._frame, text=title,
            font=("Helvetica", 13, "bold"), fg=BLUE, bg=BG,
        ).pack(padx=20, anchor="w")

    def _entry(self, var: tk.StringVar, show: str = ""):
        e = tk.Entry(
            self._frame, textvariable=var, width=72,
            bg=ENTRY_BG, fg=FG, insertbackground=FG,
            relief="flat", font=("Courier", 12),
        )
        if show:
            e.config(show=show)
        e.pack(padx=20, pady=(2, 5), anchor="w")

    def _combo(self, var: tk.StringVar, values: list[str]):
        ttk.Combobox(
            self._frame, textvariable=var, values=values,
            width=70, state="readonly",
        ).pack(padx=20, pady=(2, 5), anchor="w")

    @staticmethod
    def _auto(names: list[str], keywords: list[str]) -> str:
        for name in names:
            nl = name.lower()
            for kw in keywords:
                if kw in nl:
                    return name
        return ""

    # ── Recording ─────────────────────────────────────────────────

    def _toggle_recording(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        self._recording = True
        self._recorded_frames: list[np.ndarray] = []
        self._record_start = time.time()
        self._rec_btn.config(text="Detener grabacion", bg="#ef4444")

        mic = self._mic_var.get()
        dev_idx = find_device_index(mic, "input") if mic else None

        self._stream = sd.InputStream(
            device=dev_idx,
            samplerate=16000,
            channels=1,
            dtype="float32",
            callback=self._rec_cb,
        )
        self._stream.start()
        self._tick_timer()

    def _rec_cb(self, indata, frames, time_info, status):
        if self._recording:
            self._recorded_frames.append(indata.copy())

    def _tick_timer(self):
        if self._recording:
            elapsed = time.time() - self._record_start
            self._timer_label.config(text=f"{elapsed:.0f}s")
            self._root.after(200, self._tick_timer)

    def _stop_recording(self):
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._recorded_frames:
            self._recorded_audio = np.concatenate(self._recorded_frames)
            elapsed = time.time() - self._record_start
            self._timer_label.config(text=f"{elapsed:.0f}s grabados")
            self._save_wav()
            self._sample_status.config(text="Muestra guardada", fg=GREEN)
        else:
            self._timer_label.config(text="Sin audio")

        self._rec_btn.config(text="Grabar muestra de voz", bg=PINK)

    def _save_wav(self):
        """Persist recorded audio as voice_sample.wav next to the app."""
        if self._recorded_audio is None:
            return
        samples = (self._recorded_audio * 32767).astype(np.int16)
        with wave.open(VOICE_SAMPLE_PATH, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(samples.tobytes())

    # ── Save config ───────────────────────────────────────────────

    def _save(self):
        openai_key = self._openai_var.get().strip()
        if not openai_key:
            messagebox.showwarning("Campo requerido", "Ingresa tu OpenAI API Key.")
            return

        voice_path = VOICE_SAMPLE_PATH if os.path.exists(VOICE_SAMPLE_PATH) else ""

        config = {
            "openai_api_key": openai_key,
            "voice_sample_path": voice_path,
            "mic_device": self._mic_var.get(),
            "loopback_device": self._loopback_var.get(),
            "output_device": self._output_var.get(),
            "vb_cable_device": self._vbcable_var.get(),
        }
        save_config(config)
        self._root.destroy()
        if self._on_complete:
            self._on_complete()

    def run(self):
        self._root.mainloop()
