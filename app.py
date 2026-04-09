from __future__ import annotations

import logging
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

from config import auto_detect_devices, load_config
from pipeline import TranslationPipeline
from tts_engine import TTSEngine

log = logging.getLogger(__name__)

# ── Theme ─────────────────────────────────────────────────────────
BG = "#111827"
CARD = "#1f2937"
BLUE = "#38bdf8"
PINK = "#f472b6"
GREEN = "#4ade80"
YELLOW = "#facc15"
RED = "#f87171"
FG = "#e5e7eb"
FG_DIM = "#9ca3af"

STATUS_COLORS = {
    "listening": GREEN,
    "transcribing": YELLOW,
    "translating": BLUE,
    "speaking": PINK,
    "paused": YELLOW,
    "stopped": FG_DIM,
    "loading": BLUE,
    "error": RED,
}

STATUS_LABELS = {
    "listening": "Escuchando",
    "transcribing": "Transcribiendo...",
    "translating": "Traduciendo...",
    "speaking": "Reproduciendo TTS",
    "paused": "Traduccion pausada",
    "stopped": "Detenido",
    "loading": "Cargando modelo TTS...",
    "error": "Error",
}

SUBTITLE_MODES = ["Ambos", "Solo Izq", "Solo Der", "Ninguno"]

LANGUAGES = {
    "Español": "es",
    "English": "en",
    "Português": "pt",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
    "日本語": "ja",
    "中文": "zh",
    "한국어": "ko",
}
LANG_NAMES = list(LANGUAGES.keys())


class TranslatorApp:
    """Main translator window with subtitle panels and controls."""

    def __init__(self):
        self._config = load_config()
        self._tts_gate = threading.Event()
        self._tts_engine: TTSEngine | None = None
        self._tts_loading = False
        self._pipeline_mine: TranslationPipeline | None = None
        self._pipeline_theirs: TranslationPipeline | None = None
        self._running = False
        self._translation_count = 0
        self._original_input: str | None = None
        self._original_output: str | None = None
        self._passthrough_active = False
        self._passthrough_stream = None

        self._root = tk.Tk()
        self._root.title("Traductor en Tiempo Real — Detenido")
        self._root.configure(bg=BG)
        self._root.geometry("900x620")
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.92)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._bind_shortcuts()

        # Pre-load TTS model in background so it's ready when user clicks Start
        self._preload_tts()

    # ── UI ─────────────────────────────────────────────────────────

    def _build_ui(self):
        # Banner (hidden by default)
        self._banner = tk.Label(
            self._root, text="⏸ Traduccion pausada",
            bg=YELLOW, fg="#111827", font=("Helvetica", 12, "bold"), pady=4,
        )

        # ── Language selectors ─────────────────────────────────────
        lang_frame = tk.Frame(self._root, bg=CARD, pady=6)
        lang_frame.pack(fill="x", padx=10, pady=(10, 0))

        # Left side: my settings
        left_lang = tk.Frame(lang_frame, bg=CARD)
        left_lang.pack(side="left", padx=10)

        tk.Label(left_lang, text="Yo hablo en:", fg=FG_DIM, bg=CARD,
                 font=("Helvetica", 10)).pack(side="left", padx=(0, 4))
        self._my_lang_var = tk.StringVar(value="Español")
        ttk.Combobox(left_lang, textvariable=self._my_lang_var,
                     values=LANG_NAMES, width=10, state="readonly").pack(side="left")

        tk.Label(left_lang, text="  Ellos me escuchan en:", fg=FG_DIM, bg=CARD,
                 font=("Helvetica", 10)).pack(side="left", padx=(8, 4))
        self._they_hear_var = tk.StringVar(value="English")
        ttk.Combobox(left_lang, textvariable=self._they_hear_var,
                     values=LANG_NAMES, width=10, state="readonly").pack(side="left")

        # Right side: their settings
        right_lang = tk.Frame(lang_frame, bg=CARD)
        right_lang.pack(side="right", padx=10)

        tk.Label(right_lang, text="Ellos hablan en:", fg=FG_DIM, bg=CARD,
                 font=("Helvetica", 10)).pack(side="left", padx=(0, 4))
        self._their_lang_var = tk.StringVar(value="English")
        ttk.Combobox(right_lang, textvariable=self._their_lang_var,
                     values=LANG_NAMES, width=10, state="readonly").pack(side="left")

        tk.Label(right_lang, text="  Yo escucho en:", fg=FG_DIM, bg=CARD,
                 font=("Helvetica", 10)).pack(side="left", padx=(8, 4))
        self._i_hear_var = tk.StringVar(value="Español")
        ttk.Combobox(right_lang, textvariable=self._i_hear_var,
                     values=LANG_NAMES, width=10, state="readonly").pack(side="left")

        # ── Subtitle panels ──────────────────────────────────────
        self._panels_frame = tk.Frame(self._root, bg=BG)
        self._panels_frame.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        self._left_panel = self._make_panel(
            self._panels_frame, "🎤 Yo", PINK,
        )
        self._left_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self._right_panel = self._make_panel(
            self._panels_frame, "🔊 Ellos", BLUE,
        )
        self._right_panel.pack(side="right", fill="both", expand=True, padx=(5, 0))

        self._left_text = self._left_panel.text_widget
        self._right_text = self._right_panel.text_widget

        # Status bar
        status_frame = tk.Frame(self._root, bg=CARD, pady=6)
        status_frame.pack(fill="x", padx=10, pady=(8, 0))

        self._status_dot = tk.Label(
            status_frame, text="●", fg=FG_DIM, bg=CARD, font=("Helvetica", 14),
        )
        self._status_dot.pack(side="left", padx=(10, 4))

        self._status_label = tk.Label(
            status_frame, text="Detenido", fg=FG_DIM, bg=CARD, font=("Helvetica", 11),
        )
        self._status_label.pack(side="left")

        # TTS mode indicator
        self._tts_badge = tk.Label(
            status_frame, text="", fg=FG_DIM, bg=CARD, font=("Helvetica", 9),
        )
        self._tts_badge.pack(side="left", padx=(12, 0))

        # Translation counter
        self._counter_label = tk.Label(
            status_frame, text="0 traducciones", fg=FG_DIM, bg=CARD, font=("Helvetica", 9),
        )
        self._counter_label.pack(side="left", padx=(12, 0))

        # Subtitle selector (right side of status bar)
        tk.Label(
            status_frame, text="Subtitulos:", fg=FG_DIM, bg=CARD, font=("Helvetica", 10),
        ).pack(side="right", padx=(0, 4))

        self._subtitle_var = tk.StringVar(value="Ambos")
        sub_menu = ttk.Combobox(
            status_frame, textvariable=self._subtitle_var,
            values=SUBTITLE_MODES, width=10, state="readonly",
        )
        sub_menu.pack(side="right", padx=(0, 10))
        sub_menu.bind("<<ComboboxSelected>>", lambda _: self._apply_subtitle_mode())

        # Controls
        ctrl_frame = tk.Frame(self._root, bg=BG, pady=8)
        ctrl_frame.pack(fill="x", padx=10)

        self._start_btn = self._btn(ctrl_frame, "▶  Iniciar", GREEN, self._start)
        self._start_btn.pack(side="left", padx=4)

        self._stop_btn = self._btn(ctrl_frame, "■  Detener", RED, self._stop)
        self._stop_btn.pack(side="left", padx=4)
        self._stop_btn.config(state="disabled")

        self._pause_btn = self._btn(
            ctrl_frame, "⏸  Pausar Traduccion", YELLOW, self._toggle_pause,
        )
        self._pause_btn.pack(side="left", padx=4)
        self._pause_btn.config(state="disabled")

        self._mute_btn = self._btn(ctrl_frame, "🎤  Mutear Mic", FG_DIM, self._toggle_mute)
        self._mute_btn.pack(side="left", padx=4)
        self._mute_btn.config(state="disabled")

        self._mute_them_btn = self._btn(ctrl_frame, "🔇  Silenciar Voz", FG_DIM, self._toggle_mute_them)
        self._mute_them_btn.pack(side="left", padx=4)
        self._mute_them_btn.config(state="disabled")

        self._passthrough_btn = self._btn(ctrl_frame, "🔈  Oir Original", FG_DIM, self._toggle_passthrough)
        self._passthrough_btn.pack(side="left", padx=4)
        self._passthrough_btn.config(state="disabled")

        self._cfg_btn = self._btn(ctrl_frame, "⚙️  Config", FG_DIM, self._open_config)
        self._cfg_btn.pack(side="right", padx=4)

    def _bind_shortcuts(self):
        """Bind keyboard shortcuts for quick control."""
        self._root.bind("<Command-Return>", lambda _: self._start())
        self._root.bind("<Command-period>", lambda _: self._stop())
        self._root.bind("<Command-m>", lambda _: self._toggle_mute())
        self._root.bind("<Command-s>", lambda _: self._toggle_mute_them())
        self._root.bind("<Command-o>", lambda _: self._toggle_passthrough())
        self._root.bind("<Command-p>", lambda _: self._toggle_pause())

    def _make_panel(self, parent, title: str, color: str) -> tk.Frame:
        frame = tk.Frame(
            parent, bg=CARD, bd=0, highlightthickness=1, highlightbackground="#374151",
        )
        tk.Label(
            frame, text=title, fg=color, bg=CARD,
            font=("Helvetica", 12, "bold"), anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 4))

        text = tk.Text(
            frame, bg=CARD, fg=FG, font=("Helvetica", 12),
            wrap="word", relief="flat", height=16,
            insertbackground=FG, state="disabled",
            highlightthickness=0, padx=10, pady=4,
        )
        text.tag_configure("original", foreground=FG)
        text.tag_configure("translated", foreground=color)
        text.tag_configure("paused", foreground=YELLOW)
        text.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        frame.text_widget = text  # type: ignore[attr-defined]
        return frame

    @staticmethod
    def _btn(parent, text: str, color: str, command) -> tk.Button:
        return tk.Button(
            parent, text=text, bg=color, fg="#111827",
            font=("Helvetica", 11, "bold"), activebackground=color,
            relief="flat", padx=12, pady=4, command=command,
        )

    # ── TTS preload ───────────────────────────────────────────────

    def _preload_tts(self):
        self._tts_loading = True
        self._set_status("loading")

        def _load():
            try:
                self._tts_engine = TTSEngine(self._config)
                mode = "XTTS-v2 (voz clonada)" if self._tts_engine.has_voice_cloning else "OpenAI TTS-HD"
                self._root.after(0, lambda: self._tts_badge.config(text=f"TTS: {mode}"))
            except Exception as exc:
                log.error("TTS preload failed: %s", exc)
            finally:
                self._tts_loading = False
                self._root.after(0, lambda: self._set_status("stopped"))

        threading.Thread(target=_load, daemon=True).start()

    # ── System audio routing ─────────────────────────────────────

    def _switch_system_audio(self):
        """Redirect system input/output to BlackHole and restart coreaudiod."""
        try:
            self._original_input = subprocess.check_output(
                ["SwitchAudioSource", "-t", "input", "-c"], text=True,
            ).strip()
            self._original_output = subprocess.check_output(
                ["SwitchAudioSource", "-t", "output", "-c"], text=True,
            ).strip()

            vb = self._config.get("vb_cable_device", "BlackHole 16ch")
            lb = self._config.get("loopback_device", "BlackHole 2ch")
            subprocess.run(["SwitchAudioSource", "-t", "input", "-s", vb], check=True)
            subprocess.run(["SwitchAudioSource", "-t", "output", "-s", lb], check=True)

            # Force ALL apps to reconnect to the new audio devices
            subprocess.run([
                "osascript", "-e",
                'do shell script "killall coreaudiod" with administrator privileges',
            ], check=True)

            import time
            time.sleep(3)

            # Re-apply after coreaudiod restart
            subprocess.run(["SwitchAudioSource", "-t", "input", "-s", vb], check=True)
            subprocess.run(["SwitchAudioSource", "-t", "output", "-s", lb], check=True)
            log.info("System audio switched — input: %s, output: %s", vb, lb)
        except Exception as exc:
            log.error("Failed to switch system audio: %s", exc)

    def _restore_system_audio(self):
        """Restore original system audio devices."""
        try:
            if self._original_input:
                subprocess.run(
                    ["SwitchAudioSource", "-t", "input", "-s", self._original_input],
                    capture_output=True,
                )
            if self._original_output:
                subprocess.run(
                    ["SwitchAudioSource", "-t", "output", "-s", self._original_output],
                    capture_output=True,
                )
            log.info("System audio restored — input: %s, output: %s",
                     self._original_input, self._original_output)
        except Exception as exc:
            log.error("Failed to restore system audio: %s", exc)
        finally:
            self._original_input = None
            self._original_output = None

    # ── Actions ───────────────────────────────────────────────────

    def _start(self):
        if self._running:
            return
        if self._tts_loading:
            self._set_status("loading")
            return

        self._config = auto_detect_devices(load_config())
        self._running = True

        self._switch_system_audio()

        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._pause_btn.config(state="normal")
        self._mute_btn.config(state="normal")
        self._mute_them_btn.config(state="normal")
        self._passthrough_btn.config(state="normal")

        self._clear_text(self._left_text)
        self._clear_text(self._right_text)

        # Resolve language codes from UI
        my_lang = LANGUAGES.get(self._my_lang_var.get(), "es")
        they_hear = LANGUAGES.get(self._they_hear_var.get(), "en")
        their_lang = LANGUAGES.get(self._their_lang_var.get(), "en")
        i_hear = LANGUAGES.get(self._i_hear_var.get(), "es")

        # Ensure TTS engine exists
        if self._tts_engine is None:
            self._tts_engine = TTSEngine(self._config)

        self._pipeline_mine = TranslationPipeline(
            self._config, "mine", self._on_text, self._on_status,
            self._tts_gate, self._tts_engine,
            src_lang=my_lang, tgt_lang=they_hear,
        )
        self._pipeline_theirs = TranslationPipeline(
            self._config, "theirs", self._on_text, self._on_status,
            self._tts_gate, self._tts_engine,
            src_lang=their_lang, tgt_lang=i_hear,
        )
        # Default: theirs TTS muted (subtitles only), user can enable
        self._pipeline_theirs.tts_muted = True
        self._mute_them_btn.config(text="🔊  Solo Subtitulos", bg=BLUE)

        self._pipeline_mine.start()
        self._pipeline_theirs.start()

    def _stop(self):
        self._running = False
        pipelines = (self._pipeline_mine, self._pipeline_theirs)
        self._pipeline_mine = None
        self._pipeline_theirs = None

        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="disabled")
        self._pause_btn.config(state="disabled", text="⏸  Pausar Traduccion")
        self._mute_btn.config(state="disabled", text="🎤  Mutear Mic", bg=FG_DIM)
        self._mute_them_btn.config(state="disabled", text="🔇  Silenciar Voz", bg=FG_DIM)
        self._passthrough_btn.config(state="disabled", text="🔈  Oir Original", bg=FG_DIM)
        self._stop_passthrough()
        self._banner.pack_forget()
        self._set_status("stopped")

        def _shutdown():
            for p in pipelines:
                if p:
                    p.stop()
            self._restore_system_audio()
            self._root.after(0, lambda: self._start_btn.config(state="normal"))

        threading.Thread(target=_shutdown, daemon=True).start()

    def _toggle_mute(self):
        if not self._running or not self._pipeline_mine:
            return
        muted = not self._pipeline_mine.mic_muted
        self._pipeline_mine.mic_muted = muted
        if muted:
            self._mute_btn.config(text="🎤  Mic Muteado", bg=RED)
        else:
            self._mute_btn.config(text="🎤  Mutear Mic", bg=FG_DIM)

    def _toggle_mute_them(self):
        if not self._running or not self._pipeline_theirs:
            return
        muted = not self._pipeline_theirs.tts_muted
        self._pipeline_theirs.tts_muted = muted
        if muted:
            self._mute_them_btn.config(text="🔊  Solo Subtitulos", bg=BLUE)
        else:
            self._mute_them_btn.config(text="🔇  Silenciar Voz", bg=FG_DIM)

    def _toggle_passthrough(self):
        """Toggle pass-through: pipe system audio (BlackHole 2ch) → speakers."""
        if not self._running:
            return
        if self._passthrough_active:
            self._stop_passthrough()
            self._passthrough_btn.config(text="🔈  Oir Original", bg=FG_DIM)
        else:
            self._start_passthrough()
            self._passthrough_btn.config(text="🔇  Cortar Original", bg=GREEN)

    def _start_passthrough(self):
        import sounddevice as sd
        from pipeline import find_device_index
        lb = find_device_index(self._config.get("loopback_device", "BlackHole 2ch"), "input")
        out = find_device_index(self._config.get("output_device", "MacBook Pro (bocinas)"), "output")
        if lb is None or out is None:
            return
        try:
            self._passthrough_stream = sd.Stream(
                device=(lb, out),
                samplerate=48000,
                channels=2,
                dtype="float32",
                callback=lambda indata, outdata, frames, time, status: (
                    outdata.__setitem__(slice(None), indata)
                ),
            )
            self._passthrough_stream.start()
            self._passthrough_active = True
        except Exception as exc:
            log.error("Passthrough error: %s", exc)

    def _stop_passthrough(self):
        if self._passthrough_stream:
            try:
                self._passthrough_stream.stop()
                self._passthrough_stream.close()
            except Exception:
                pass
            self._passthrough_stream = None
        self._passthrough_active = False

    def _toggle_pause(self):
        if not self._running:
            return
        paused = not (self._pipeline_mine and self._pipeline_mine.translation_paused)
        if self._pipeline_mine:
            self._pipeline_mine.translation_paused = paused
        if self._pipeline_theirs:
            self._pipeline_theirs.translation_paused = paused

        if paused:
            self._pause_btn.config(text="▶  Reanudar")
            self._banner.pack(fill="x", before=self._panels_frame)
        else:
            self._pause_btn.config(text="⏸  Pausar Traduccion")
            self._banner.pack_forget()

    def _open_config(self):
        from setup_wizard import SetupWizard
        self._stop()
        self._root.withdraw()

        def on_done():
            self._config = load_config()
            # Reload TTS engine with new config
            self._tts_engine = None
            self._preload_tts()
            self._root.deiconify()

        SetupWizard(on_complete=on_done).run()

    # ── Callbacks (from worker threads → main thread) ─────────────

    def _on_text(self, direction: str, original: str, translated: str | None):
        self._root.after(0, self._update_text, direction, original, translated)

    def _on_status(self, direction: str, status: str):
        self._root.after(0, self._set_status, status)

    # ── UI updates (main thread only) ─────────────────────────────

    def _update_text(self, direction: str, original: str, translated: str | None):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        widget = self._left_text if direction == "mine" else self._right_text
        widget.config(state="normal")
        widget.insert("end", f"[{ts}] {original}\n", "original")
        if translated:
            widget.insert("end", f"→ {translated}\n\n", "translated")
            self._translation_count += 1
            self._counter_label.config(text=f"{self._translation_count} traducciones")
        else:
            widget.insert("end", "→ [pausado]\n\n", "paused")
        widget.see("end")
        widget.config(state="disabled")

    def _set_status(self, status: str):
        color = STATUS_COLORS.get(status, FG_DIM)
        label = STATUS_LABELS.get(status, status)
        self._status_dot.config(fg=color)
        self._status_label.config(text=label, fg=color)

    def _clear_text(self, widget: tk.Text):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.config(state="disabled")

    def _apply_subtitle_mode(self):
        mode = self._subtitle_var.get()
        self._left_panel.pack_forget()
        self._right_panel.pack_forget()
        if mode == "Ambos":
            self._left_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))
            self._right_panel.pack(side="right", fill="both", expand=True, padx=(5, 0))
        elif mode == "Solo Izq":
            self._left_panel.pack(side="left", fill="both", expand=True, padx=0)
        elif mode == "Solo Der":
            self._right_panel.pack(side="right", fill="both", expand=True, padx=0)

    # ── Lifecycle ─────────────────────────────────────────────────

    def _on_close(self):
        self._stop()
        self._root.destroy()

    def run(self):
        self._root.mainloop()
