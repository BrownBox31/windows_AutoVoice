"""
autovoice/modules/denoiser.py
==============================
Audio denoising using noisereduce with spectral subtraction fallback.

Why noisereduce instead of DeepFilterNet
-----------------------------------------
DeepFilterNet requires Rust + MSVC C++ Build Tools to compile its Python
extension on Windows, and its strict packaging<24.0 pin conflicts with
modern pip tooling. noisereduce is pure Python/NumPy — one pip command,
zero compilation, zero packaging conflicts, works on Windows/Linux/macOS.

Enhancement pipeline
---------------------
1. FFmpeg extracts audio from any container (MP4, M4A, MP3, WAV, AAC …)
2. noisereduce removes stationary background noise using a noise profile
   sampled from the first 0.5 seconds of the recording
3. Spectral subtraction (second pass) cleans up residual noise
4. Band-pass filter isolates the human voice frequency range (80 Hz–8 kHz)
5. Light dynamic range compression reduces loud peaks
6. Output saved as MP3 (via lameenc) or WAV

Windows-specific behaviour
---------------------------
- subprocess.CREATE_NO_WINDOW: suppresses FFmpeg console flash
- Temp WAV written to tempfile.gettempdir(): avoids PermissionError in
  protected paths like Program Files
"""

import logging
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Optional

import librosa
import lameenc
import numpy as np
import scipy.signal
import soundfile as sf
from imageio_ffmpeg import get_ffmpeg_exe

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency — noisereduce
# Falls back to spectral subtraction if not installed
# ---------------------------------------------------------------------------
try:
    import noisereduce as nr
    _NOISEREDUCE_AVAILABLE = True
except ImportError:
    _NOISEREDUCE_AVAILABLE = False
    logger.warning(
        "noisereduce not installed — spectral subtraction only. "
        "Install with: pip install noisereduce"
    )

# ---------------------------------------------------------------------------
# Windows: suppress FFmpeg console window when called from API / GUI
# ---------------------------------------------------------------------------
_SUBPROCESS_FLAGS: dict = {}
if sys.platform == "win32":
    _SUBPROCESS_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW


class AudioDenoiser:
    """Audio denoiser: noisereduce (primary) + spectral subtraction (fallback / second pass).

    Parameters
    ----------
    use_noisereduce : bool
        Use noisereduce if available. False forces spectral subtraction only.
    prop_decrease : float
        Noise reduction aggressiveness (0.0–1.0).
        0.75 recommended for factory floors. Raise to 0.9 for very heavy
        machinery noise. Lower to 0.5 for quieter environments.
    stationary : bool
        True  = model background as constant (good for conveyor/HVAC hum).
        False = non-stationary mode for variable noise sources.
    """

    def __init__(
        self,
        use_noisereduce: bool = True,
        prop_decrease:   float = 0.75,
        stationary:      bool  = True,
    ) -> None:
        self.use_noisereduce = use_noisereduce and _NOISEREDUCE_AVAILABLE
        self.prop_decrease   = prop_decrease
        self.stationary      = stationary

        mode = f"noisereduce (stationary={stationary})" if self.use_noisereduce \
               else "spectral subtraction only"
        logger.info("AudioDenoiser: %s", mode)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tmp_wav(self, stem: str) -> Path:
        """Return a writable temp WAV path in the OS temp directory."""
        return Path(tempfile.gettempdir()) / f"autovoice_tmp_{stem}.wav"

    # ------------------------------------------------------------------
    # Step 1 — Extract audio with FFmpeg
    # ------------------------------------------------------------------

    def extract_audio(
        self,
        input_path:  str,
        out_wav_path: str,
        sample_rate: int = 48000,
        channels:    int = 1,
    ) -> bool:
        """Extract audio from any media file to a WAV using FFmpeg.

        Parameters
        ----------
        input_path   : Source file (mp4, m4a, mp3, wav, aac, mkv …).
        out_wav_path : Destination WAV file path.
        sample_rate  : Output sample rate in Hz (default 48000).
        channels     : Number of output channels (1 = mono).

        Returns
        -------
        True on success, False on FFmpeg error.
        """
        cmd = [
            get_ffmpeg_exe(),
            "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", input_path,
            "-vn",
            "-ac", str(channels),
            "-ar", str(sample_rate),
            "-f",  "wav", out_wav_path,
        ]
        try:
            subprocess.run(cmd, check=True, **_SUBPROCESS_FLAGS)
            return True
        except subprocess.CalledProcessError as exc:
            logger.error("FFmpeg extraction failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Step 2a — noisereduce (primary denoiser)
    # ------------------------------------------------------------------

    def _reduce_noise(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Apply noisereduce using the first 0.5 s as a noise profile.

        The first half-second of a factory floor recording typically
        contains background noise before the operator starts speaking,
        making it an ideal noise profile sample.
        """
        try:
            n_noise = int(0.5 * sr)
            noise_clip = audio[:n_noise] if len(audio) > n_noise else audio

            reduced = nr.reduce_noise(
                y                       = audio,
                sr                      = sr,
                y_noise                 = noise_clip,
                prop_decrease           = self.prop_decrease,
                stationary              = self.stationary,
                n_std_thresh_stationary = 1.5,
            )
            return reduced.astype(np.float32)
        except Exception as exc:
            logger.warning("noisereduce failed (%s) — using spectral subtraction.", exc)
            return self._spectral_subtraction(audio, sr)

    # ------------------------------------------------------------------
    # Step 2b — spectral subtraction (fallback / second pass)
    # ------------------------------------------------------------------

    def _spectral_subtraction(
        self,
        audio:       np.ndarray,
        sr:          int,
        alpha:       float = 2.0,
        floor_ratio: float = 0.02,
    ) -> np.ndarray:
        """Classic magnitude spectral subtraction.

        Parameters
        ----------
        alpha       : Over-subtraction factor. Higher = more aggressive.
        floor_ratio : Spectral floor to prevent musical noise artifacts.
        """
        n_fft, hop = 2048, 512
        D     = librosa.stft(audio, n_fft=n_fft, hop_length=hop, win_length=n_fft)
        mag   = np.abs(D)
        phase = np.exp(1j * np.angle(D))

        # Estimate noise PSD from the first ~1 second
        n_frames = max(1, int(sr / hop))
        noise    = np.mean(mag[:, :n_frames], axis=1, keepdims=True)
        enh_mag  = np.maximum(mag - alpha * noise, floor_ratio * mag)

        return librosa.istft(enh_mag * phase, hop_length=hop, win_length=n_fft)

    # ------------------------------------------------------------------
    # Step 2 — Combined enhancement chain
    # ------------------------------------------------------------------

    def enhance_audio(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Run full enhancement: noisereduce → spectral subtraction.

        Running both in sequence is better than either alone:
        - noisereduce removes the bulk of the stationary background
        - spectral subtraction cleans up residual artefacts
        """
        if self.use_noisereduce:
            audio = self._reduce_noise(audio, sr)

        # Second pass: light spectral subtraction
        audio = self._spectral_subtraction(audio, sr, alpha=1.0, floor_ratio=0.05)
        return audio

    # ------------------------------------------------------------------
    # Step 3 — Post-processing
    # ------------------------------------------------------------------

    def post_process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Normalise, band-pass filter, and lightly compress the audio."""

        # Peak normalise to –1 dBFS
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0:
            audio = audio / peak * 0.99

        # Band-pass 80 Hz – 8 kHz (human voice range)
        nyq = sr / 2.0
        lo  = 80.0  / nyq
        hi  = min(8000.0, sr * 0.45) / nyq
        if 0.0 < lo < hi < 1.0:
            b, a  = scipy.signal.butter(4, [lo, hi], btype="band")
            audio = scipy.signal.filtfilt(b, a, audio)

        # Gentle dynamic range compression (ratio 3:1 above threshold 0.3)
        thr, ratio = 0.3, 3.0
        over = np.abs(audio) > thr
        audio[over] = np.sign(audio[over]) * (thr + (np.abs(audio[over]) - thr) / ratio)

        return audio

    # ------------------------------------------------------------------
    # Step 4 — Save MP3
    # ------------------------------------------------------------------

    def save_mp3(
        self,
        waveform:     np.ndarray,
        sr:           int,
        out_path:     str,
        bitrate_kbps: int = 192,
    ) -> None:
        """Encode and write a mono float32 array as an MP3 file."""
        maxv  = max(1e-9, float(np.max(np.abs(waveform))))
        pcm16 = (waveform / maxv * 32767.0).astype(np.int16)

        enc = lameenc.Encoder()
        enc.set_bit_rate(bitrate_kbps)
        enc.set_in_sample_rate(int(sr))
        enc.set_channels(1)
        enc.set_quality(2)

        with open(out_path, "wb") as fh:
            fh.write(enc.encode(pcm16.tobytes()) + enc.flush())

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def denoise_file(self, input_path: str, output_path: str) -> bool:
        """Full pipeline: extract → denoise → post-process → save.

        Parameters
        ----------
        input_path  : Path to source audio/video file.
        output_path : Destination path (.mp3 or .wav).

        Returns
        -------
        True on success, False on any failure.
        """
        input_path  = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_wav = self._tmp_wav(input_path.stem)

        try:
            if not self.extract_audio(str(input_path), str(tmp_wav)):
                return False

            audio, sr = librosa.load(str(tmp_wav), sr=None, mono=True)

            enhanced = self.enhance_audio(audio, sr)
            enhanced = self.post_process(enhanced, sr)

            if output_path.suffix.lower() == ".mp3":
                self.save_mp3(enhanced.astype(np.float32), sr, str(output_path))
            else:
                sf.write(str(output_path), enhanced, sr)

            logger.info("Denoised → %s", output_path)
            return True

        except Exception as exc:
            logger.error("denoise_file failed for '%s': %s", input_path.name, exc)
            return False

        finally:
            try:
                tmp_wav.unlink(missing_ok=True)
            except Exception:
                pass
