"""
autovoice/modules/transcriber.py
==================================
Speech-to-text using OpenAI Whisper (CPU inference).

Model sizes and trade-offs
---------------------------
Model   Params    VRAM     Relative speed
------  --------  -------  --------------
tiny     39 M     ~1 GB    ~32x
base     74 M     ~1 GB    ~16x
small   244 M     ~2 GB    ~6x
medium  769 M     ~5 GB    ~2x
large  1550 M     ~10 GB   1x
turbo   809 M     ~3 GB    ~8x  ← recommended (best speed/accuracy balance)

All models run on CPU — no GPU required.

Windows note
-------------
Models are cached to C:\\Users\\<you>\\.cache\\whisper via an explicit
download_root to avoid the occasional path-resolution bug on Windows
when the default cache directory differs from the user home.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

import whisper

logger = logging.getLogger(__name__)

# Default prompt tuned for Indian automotive inspection vocabulary
_DEFAULT_PROMPT = (
    "You are a mechanic logging issues found in inspections of vehicles "
    "for an Indian automotive manufacturer. Transcribe the audio clearly "
    "so it reads like a vehicle issue report. Do not make anything up. "
    "If words are unclear, use the closest automotive term that fits the context. "
    "Common terms: bearing, bolt, axle, mounting, shock absorber, engine, "
    "transmission, electrical, cable, tightening, defect, torque, clearance, "
    "Pulsar 125, Pulsar N160, Dominar, dynamometer. "
    "Operators use 'Start' and 'Stop' as verbal checkpoints."
)


def _whisper_cache_dir() -> str:
    """Return (and create) the Whisper model cache directory.

    Uses an explicit path to avoid Windows path-resolution bugs with
    the default cache location.
    """
    cache = Path(os.path.expanduser("~")) / ".cache" / "whisper"
    cache.mkdir(parents=True, exist_ok=True)
    return str(cache)


class AudioTranscriber:
    """Whisper-based speech-to-text transcriber.

    Parameters
    ----------
    model_name : str
        Whisper model to load. Choices: tiny, base, small, medium, large, turbo.
        Default: turbo (best speed/accuracy for factory floor use).
    """

    def __init__(self, model_name: str = "turbo") -> None:
        logger.info("Loading Whisper model '%s' (this may take a moment) …", model_name)
        self.model_name = model_name
        self.model = whisper.load_model(
            model_name,
            device="cpu",
            download_root=_whisper_cache_dir(),
        )
        logger.info("Whisper model '%s' ready.", model_name)

    def transcribe(
        self,
        audio_path: str,
        prompt:     Optional[str] = None,
        language:   Optional[str] = None,
    ) -> str:
        """Transcribe an audio file to text.

        Parameters
        ----------
        audio_path : Path to the audio file (WAV, MP3, M4A …).
        prompt     : Optional initial prompt to guide transcription.
                     Uses automotive default prompt if None.
        language   : BCP-47 language code (e.g. "en", "hi").
                     Auto-detected if None.

        Returns
        -------
        Transcribed text as a string.
        """
        kwargs: Dict = {
            "initial_prompt": prompt or _DEFAULT_PROMPT,
        }
        if language:
            kwargs["language"] = language

        result = self.model.transcribe(audio_path, **kwargs)
        text   = result.get("text", "").strip()
        logger.info(
            "Transcribed '%s': %d words",
            Path(audio_path).name,
            len(text.split()),
        )
        return text

    def transcribe_with_segments(
        self,
        audio_path: str,
        prompt:     Optional[str] = None,
        language:   Optional[str] = None,
    ) -> Dict:
        """Transcribe and return full Whisper result including segments.

        Returns the raw dict from whisper.model.transcribe(), which includes:
          - text      : full transcript string
          - segments  : list of {start, end, text} dicts with timestamps
          - language  : detected language code
        """
        kwargs: Dict = {
            "initial_prompt": prompt or _DEFAULT_PROMPT,
        }
        if language:
            kwargs["language"] = language

        return self.model.transcribe(audio_path, **kwargs)
