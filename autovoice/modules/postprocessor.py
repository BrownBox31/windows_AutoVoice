"""
autovoice/modules/postprocessor.py
=====================================
Structures raw Whisper transcripts into a clean result dict.

Attaches PLC metadata (engine number, model name) when provided,
and computes basic statistics (word count, character count).

The output dict is what gets POSTed to the backend API.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class TextPostProcessor:
    """Post-process a raw Whisper transcript into a structured result.

    Optionally attaches PLC data (engine number, model name) when the
    result came from a conveyor-triggered cycle.
    """

    def clean_text(self, text: str) -> str:
        """Normalise whitespace and strip leading/trailing space."""
        # Collapse multiple consecutive spaces / newlines
        import re
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip()

    def process(
        self,
        raw_text:       str,
        input_filename: str,
        part_data:      Optional[Dict] = None,
    ) -> Dict:
        """Build a structured result dict from a raw transcript.

        Parameters
        ----------
        raw_text       : Raw text returned by Whisper.
        input_filename : Name of the source audio file.
        part_data      : Dict with keys engine_number, model_code, model_name
                         (from PLCClient.wait_for_part()). Pass None for
                         non-PLC file uploads.

        Returns
        -------
        Dict with keys: input_file, output, metadata, plc_data (if PLC).

        Example output
        --------------
        {
            "input_file": "eng12345_Pulsar_125.wav",
            "output":     "Engine mounting bolts checked. No defects found.",
            "metadata": {
                "word_count":         7,
                "char_count":         47,
                "timestamp_utc":      "2025-03-02T10:35:00+00:00",
                "processing_version": "2.1.0",
            },
            "plc_data": {
                "engine_number": 12345,
                "model_code":    1,
                "model_name":    "Pulsar 125",
            },
        }
        """
        cleaned = self.clean_text(raw_text)

        result: Dict = {
            "input_file": input_filename,
            "output":     cleaned,
            "metadata": {
                "word_count":         len(cleaned.split()) if cleaned else 0,
                "char_count":         len(cleaned),
                "timestamp_utc":      datetime.now(timezone.utc).isoformat(),
                "processing_version": "2.1.0",
            },
        }

        if part_data:
            result["plc_data"] = {
                "engine_number": part_data.get("engine_number"),
                "model_code":    part_data.get("model_code"),
                "model_name":    part_data.get("model_name"),
            }
            logger.info(
                "Processed: engine=%s model=%s words=%d",
                part_data.get("engine_number"),
                part_data.get("model_name"),
                result["metadata"]["word_count"],
            )
        else:
            logger.info(
                "Processed: file=%s words=%d",
                input_filename,
                result["metadata"]["word_count"],
            )

        return result
