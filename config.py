from __future__ import annotations

import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_DIR, "config.json")
VOICE_SAMPLE_PATH = os.path.join(_DIR, "voice_sample.wav")

DEFAULT_CONFIG: dict[str, str] = {
    "openai_api_key": "",
    "voice_sample_path": "",
    "mic_device": "",
    "loopback_device": "",
    "output_device": "",
    "vb_cable_device": "",
}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
        return {**DEFAULT_CONFIG, **stored}
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def auto_detect_devices(config: dict) -> dict:
    """Fill in missing audio devices automatically."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception:
        return config

    inputs = [d["name"] for d in devices if d["max_input_channels"] > 0]
    outputs = [d["name"] for d in devices if d["max_output_channels"] > 0]

    def _find(names: list[str], keywords: list[str]) -> str:
        for name in names:
            nl = name.lower()
            for kw in keywords:
                if kw in nl:
                    return name
        return ""

    def _find_real_mic(names: list[str]) -> str:
        blackhole_kw = ["blackhole", "virtual", "vb-cable", "teams audio"]
        for name in names:
            if not any(kw in name.lower() for kw in blackhole_kw):
                return name
        return names[0] if names else ""

    def _find_real_output(names: list[str]) -> str:
        blackhole_kw = ["blackhole", "virtual", "vb-cable", "teams audio"]
        for name in names:
            if not any(kw in name.lower() for kw in blackhole_kw):
                return name
        return names[0] if names else ""

    if not config.get("mic_device"):
        config["mic_device"] = _find_real_mic(inputs)
    if not config.get("loopback_device"):
        config["loopback_device"] = _find(inputs, ["blackhole 2ch", "blackhole", "loopback"])
    if not config.get("output_device"):
        config["output_device"] = _find_real_output(outputs)
    if not config.get("vb_cable_device"):
        config["vb_cable_device"] = _find(outputs, ["blackhole 16ch", "blackhole 16", "vb-cable"])

    return config


def is_configured() -> bool:
    cfg = load_config()
    return bool(cfg.get("openai_api_key"))
