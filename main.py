#!/usr/bin/env python3
"""Real-time bidirectional voice translator for professional meetings."""

import atexit
import logging
import signal
import subprocess
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

log = logging.getLogger(__name__)

# ── Safety net: always restore system audio ──────────────────────
_original_input = None
_original_output = None


def _save_original_audio():
    global _original_input, _original_output
    try:
        _original_input = subprocess.check_output(
            ["SwitchAudioSource", "-t", "input", "-c"], text=True,
        ).strip()
        _original_output = subprocess.check_output(
            ["SwitchAudioSource", "-t", "output", "-c"], text=True,
        ).strip()
    except Exception:
        pass


def _restore_audio():
    try:
        if _original_input:
            subprocess.run(
                ["SwitchAudioSource", "-t", "input", "-s", _original_input],
                capture_output=True,
            )
        if _original_output:
            subprocess.run(
                ["SwitchAudioSource", "-t", "output", "-s", _original_output],
                capture_output=True,
            )
        log.info("Audio restored: input=%s, output=%s", _original_input, _original_output)
    except Exception:
        pass


def _signal_handler(sig, frame):
    _restore_audio()
    sys.exit(0)


def launch_app():
    from app import TranslatorApp
    TranslatorApp().run()


def main():
    _save_original_audio()
    atexit.register(_restore_audio)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    from config import is_configured

    if not is_configured():
        from setup_wizard import SetupWizard
        SetupWizard(on_complete=launch_app).run()
    else:
        launch_app()


if __name__ == "__main__":
    main()
