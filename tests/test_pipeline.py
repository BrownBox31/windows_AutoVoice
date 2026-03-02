"""
tests/test_pipeline.py
========================
Smoke tests for AutoVoice modules.

Run with:
    pip install pytest
    pytest tests/ -v
"""

import os
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
import pytest

# Ensure the package is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_test_wav(duration_s: float = 2.0, sr: int = 16000) -> str:
    """Write a short WAV file of white noise + a 440 Hz tone."""
    n = int(sr * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    tone  = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    noise = (0.05 * np.random.randn(n)).astype(np.float32)
    signal = np.clip(tone + noise, -1.0, 1.0)
    pcm16  = (signal * 32767).astype(np.int16)

    fd, path = tempfile.mkstemp(suffix=".wav", prefix="autovoice_test_")
    os.close(fd)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    return path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_config_imports():
    """config.py must be importable and expose required attributes."""
    from autovoice import config
    required = [
        "PLC_HOST", "PLC_PORT", "PART_BIT_DEVICE",
        "ENGINE_WORD_DEVICE", "MODEL_WORD_DEVICE",
        "WHISPER_MODEL", "BACKEND_URL", "OUTPUT_DIR",
        "SILENCE_THRESHOLD", "SILENCE_DURATION_S",
        "PROP_DECREASE", "USE_NOISEREDUCE",
    ]
    for attr in required:
        assert hasattr(config, attr), f"config.{attr} missing"


# ---------------------------------------------------------------------------
# Postprocessor
# ---------------------------------------------------------------------------

def test_postprocessor_basic():
    from autovoice.modules.postprocessor import TextPostProcessor

    pp     = TextPostProcessor()
    result = pp.process("  Hello world  ", "test.wav")

    assert result["output"]                   == "Hello world"
    assert result["metadata"]["word_count"]   == 2
    assert result["metadata"]["char_count"]   == 11
    assert "timestamp_utc"                    in result["metadata"]


def test_postprocessor_with_plc_data():
    from autovoice.modules.postprocessor import TextPostProcessor

    pp       = TextPostProcessor()
    part     = {"engine_number": 12345, "model_code": 1, "model_name": "Pulsar 125"}
    result   = pp.process("Bolts checked.", "eng.wav", part_data=part)

    assert "plc_data"                          in result
    assert result["plc_data"]["engine_number"] == 12345
    assert result["plc_data"]["model_name"]    == "Pulsar 125"


def test_postprocessor_empty_text():
    from autovoice.modules.postprocessor import TextPostProcessor

    pp     = TextPostProcessor()
    result = pp.process("", "empty.wav")

    assert result["output"]                 == ""
    assert result["metadata"]["word_count"] == 0


# ---------------------------------------------------------------------------
# Denoiser
# ---------------------------------------------------------------------------

def test_denoiser_spectral_subtraction():
    """Spectral subtraction must run without error on a synthetic WAV."""
    from autovoice.modules.denoiser import AudioDenoiser

    wav_path = make_test_wav()
    try:
        d = AudioDenoiser(use_noisereduce=False)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            out_path = tmp.name

        ok = d.denoise_file(wav_path, out_path)
        assert ok, "denoise_file returned False"
        assert Path(out_path).exists()
        assert Path(out_path).stat().st_size > 0
    finally:
        Path(wav_path).unlink(missing_ok=True)
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass


def test_denoiser_noisereduce():
    """noisereduce denoiser must complete without crashing."""
    pytest.importorskip("noisereduce", reason="noisereduce not installed")

    from autovoice.modules.denoiser import AudioDenoiser

    wav_path = make_test_wav()
    try:
        d = AudioDenoiser(use_noisereduce=True, prop_decrease=0.75, stationary=True)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name

        ok = d.denoise_file(wav_path, out_path)
        assert ok
        assert Path(out_path).stat().st_size > 0
    finally:
        Path(wav_path).unlink(missing_ok=True)
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass


def test_denoiser_enhance_audio():
    """enhance_audio must return an ndarray of the same length."""
    import librosa
    from autovoice.modules.denoiser import AudioDenoiser

    wav_path = make_test_wav()
    try:
        audio, sr = librosa.load(wav_path, sr=None, mono=True)
        d         = AudioDenoiser(use_noisereduce=False)
        enhanced  = d.enhance_audio(audio, sr)
        assert isinstance(enhanced, np.ndarray)
        # Length may differ slightly due to STFT framing; allow 5% tolerance
        assert abs(len(enhanced) - len(audio)) / len(audio) < 0.05
    finally:
        Path(wav_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PLC client (offline / mock tests)
# ---------------------------------------------------------------------------

def test_plc_client_not_connected():
    """PLCClient methods should handle the disconnected state gracefully."""
    from autovoice.modules.plc_client import PLCClient

    plc = PLCClient(host="127.0.0.1", port=19999)
    assert not plc.is_connected
    assert plc.read_part_data() is None
    assert plc.wait_for_part(timeout_s=0.05) is None


def test_plc_model_code_map():
    from autovoice.modules.plc_client import MODEL_CODE_MAP

    assert 1 in MODEL_CODE_MAP
    assert isinstance(MODEL_CODE_MAP[1], str)


# ---------------------------------------------------------------------------
# AutoVoice class
# ---------------------------------------------------------------------------

def test_autovoice_instantiation():
    """AutoVoice must instantiate without error (Whisper loads on demand)."""
    from autovoice import AutoVoice

    # Use tiny model so this test doesn't download 809 MB
    av = AutoVoice(
        whisper_model = "tiny",
        backend_url   = "",       # disable backend POST in tests
        output_dir    = tempfile.mkdtemp(),
    )
    assert av is not None
    assert av.denoiser is not None
    assert av.postprocessor is not None


def test_autovoice_process_file():
    """process_file must return a successful result dict for a valid WAV."""
    from autovoice import AutoVoice

    wav_path = make_test_wav(duration_s=1.5)
    out_dir  = tempfile.mkdtemp()
    try:
        av = AutoVoice(
            whisper_model    = "tiny",
            use_noisereduce  = False,   # faster for test
            backend_url      = "",
            output_dir       = out_dir,
        )
        result = av.process_file(wav_path, output_dir=out_dir)

        assert result["success"] is True
        assert Path(result["denoised_audio"]).exists()
        assert Path(result["transcript_file"]).exists()
        assert "processed_result" in result
        assert "output" in result["processed_result"]
    finally:
        Path(wav_path).unlink(missing_ok=True)
