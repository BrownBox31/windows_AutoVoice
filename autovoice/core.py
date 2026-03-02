"""
autovoice/core.py
==================
Main AutoVoice pipeline — ties all modules together.

Two operating modes
--------------------
1. **Conveyor loop** (run_conveyor_loop):
   Connects to Mitsubishi iQ-R PLC → waits for part-present trigger →
   records operator voice → denoises → transcribes → POSTs result to backend.
   Repeats indefinitely until Ctrl+C.

2. **File processing** (process_file / process_directory):
   Accepts any pre-recorded audio/video file → denoises → transcribes →
   saves transcript TXT. No PLC or microphone required.

Backend integration
--------------------
After each cycle, AutoVoice POSTs a JSON payload to BACKEND_URL:

    POST <BACKEND_URL>
    {
        "transcript":      "Engine bolts checked. No defects.",
        "plc_data":        {"engine_number": 12345, "model_name": "Pulsar 125", ...},
        "denoised_audio":  "./output/eng12345_denoised.mp3",
        "transcript_file": "./output/eng12345_transcript.txt",
        "metadata":        {"word_count": 5, ...}
    }

Your backend only needs to accept this one POST endpoint.
Set BACKEND_URL="" in config.py to disable posting (file-only mode).
"""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from . import config
from .modules.denoiser      import AudioDenoiser
from .modules.plc_client    import PLCClient
from .modules.postprocessor import TextPostProcessor
from .modules.recorder      import AudioRecorder
from .modules.transcriber   import AudioTranscriber

logger = logging.getLogger(__name__)


class AutoVoice:
    """AutoVoice pipeline: PLC trigger → record → denoise → transcribe → POST.

    All parameters default to values from config.py, which in turn
    reads from environment variables.  Pass explicit values to override.

    Parameters
    ----------
    use_noisereduce    : Enable noisereduce (True) or spectral subtraction only (False).
    prop_decrease      : Noise reduction aggressiveness 0.0–1.0 (default 0.75).
    stationary_noise   : True for constant background (factory hum, HVAC).
    whisper_model      : Whisper model name (tiny/base/small/medium/large/turbo).
    whisper_prompt     : Custom transcription prompt. Uses automotive default if None.
    plc_host           : Mitsubishi PLC IP address.
    plc_port           : SLMP TCP port.
    part_bit_device    : Bit device for part-present trigger (e.g. "M0").
    engine_word_device : Word device for engine number (e.g. "D100").
    model_word_device  : Word device for model code (e.g. "D101").
    silence_threshold  : Microphone RMS silence threshold (0.0–1.0).
    silence_duration_s : Seconds of silence before auto-stop.
    min_record_s       : Minimum recording duration before silence kicks in.
    max_record_s       : Hard maximum recording duration.
    backend_url        : URL to POST results to. "" = disabled.
    backend_timeout_s  : Timeout in seconds for backend POST.
    output_dir         : Directory for denoised MP3 and transcript TXT files.
    """

    def __init__(
        self,
        # Denoiser
        use_noisereduce:    bool  = config.USE_NOISEREDUCE,
        prop_decrease:      float = config.PROP_DECREASE,
        stationary_noise:   bool  = config.STATIONARY_NOISE,
        # Whisper
        whisper_model:      str            = config.WHISPER_MODEL,
        whisper_prompt:     Optional[str]  = None,
        # PLC
        plc_host:           str   = config.PLC_HOST,
        plc_port:           int   = config.PLC_PORT,
        part_bit_device:    str   = config.PART_BIT_DEVICE,
        engine_word_device: str   = config.ENGINE_WORD_DEVICE,
        model_word_device:  str   = config.MODEL_WORD_DEVICE,
        # Recorder
        silence_threshold:  float = config.SILENCE_THRESHOLD,
        silence_duration_s: float = config.SILENCE_DURATION_S,
        min_record_s:       float = config.MIN_RECORD_S,
        max_record_s:       float = config.MAX_RECORD_S,
        # Backend
        backend_url:        str   = config.BACKEND_URL,
        backend_timeout_s:  float = config.BACKEND_TIMEOUT_S,
        # Output
        output_dir:         str   = config.OUTPUT_DIR,
    ) -> None:
        self.output_dir        = Path(output_dir)
        self.backend_url       = backend_url
        self.backend_timeout_s = backend_timeout_s
        self.whisper_prompt    = whisper_prompt

        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.plc = PLCClient(
            host               = plc_host,
            port               = plc_port,
            part_bit_device    = part_bit_device,
            engine_word_device = engine_word_device,
            model_word_device  = model_word_device,
        )
        self.recorder = AudioRecorder(
            silence_threshold  = silence_threshold,
            silence_duration_s = silence_duration_s,
            min_record_s       = min_record_s,
            max_record_s       = max_record_s,
        )
        self.denoiser = AudioDenoiser(
            use_noisereduce = use_noisereduce,
            prop_decrease   = prop_decrease,
            stationary      = stationary_noise,
        )
        self.transcriber   = AudioTranscriber(model_name=whisper_model)
        self.postprocessor = TextPostProcessor()

    # ------------------------------------------------------------------
    # File processing (no PLC / microphone required)
    # ------------------------------------------------------------------

    def process_file(
        self,
        input_path:  str,
        output_dir:  Optional[str] = None,
        part_data:   Optional[Dict] = None,
    ) -> Dict:
        """Denoise and transcribe a pre-recorded audio/video file.

        Parameters
        ----------
        input_path : Path to the source file (MP3, M4A, WAV, MP4, AAC …).
        output_dir : Override output directory. Defaults to self.output_dir.
        part_data  : PLC data dict to attach to result (used by conveyor loop).

        Returns
        -------
        Dict with keys:
            success          : bool
            denoised_audio   : str path to denoised MP3
            transcript_file  : str path to transcript TXT
            processed_result : structured result dict from TextPostProcessor
            error            : str (only present on failure)
        """
        input_path = Path(input_path)
        out_dir    = Path(output_dir) if output_dir else self.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Processing: %s", input_path.name)

        # Step 1 — Denoise
        denoised_path = out_dir / f"{input_path.stem}_denoised.mp3"
        logger.info("  [1/3] Denoising ...")
        if not self.denoiser.denoise_file(str(input_path), str(denoised_path)):
            return {"success": False, "error": "Denoising failed"}

        # Step 2 — Transcribe
        logger.info("  [2/3] Transcribing ...")
        try:
            raw_text = self.transcriber.transcribe(
                str(denoised_path),
                prompt=self.whisper_prompt,
            )
        except Exception as exc:
            return {"success": False, "error": f"Transcription failed: {exc}"}

        # Step 3 — Post-process
        logger.info("  [3/3] Post-processing ...")
        result = self.postprocessor.process(
            raw_text       = raw_text,
            input_filename = input_path.name,
            part_data      = part_data,
        )

        # Save transcript TXT
        transcript_path = out_dir / f"{input_path.stem}_transcript.txt"
        transcript_path.write_text(result["output"], encoding="utf-8")
        logger.info("  Transcript saved → %s", transcript_path)

        return {
            "success":          True,
            "denoised_audio":   str(denoised_path),
            "transcript_file":  str(transcript_path),
            "processed_result": result,
        }

    def process_directory(
        self,
        input_dir:  str,
        output_dir: Optional[str] = None,
    ) -> List[Dict]:
        """Batch process all audio/video files in a directory.

        Parameters
        ----------
        input_dir  : Directory to scan for audio/video files.
        output_dir : Override output directory.

        Returns
        -------
        List of result dicts (one per file).
        """
        input_dir = Path(input_dir)
        extensions = {
            "*.mp4", "*.MP4", "*.m4a", "*.M4A",
            "*.wav", "*.WAV", "*.mp3", "*.MP3",
            "*.aac", "*.AAC", "*.ogg", "*.OGG",
        }

        files: List[Path] = []
        seen:  set         = set()
        for pat in extensions:
            for fp in input_dir.glob(pat):
                if fp not in seen:
                    seen.add(fp)
                    files.append(fp)
        files.sort()

        if not files:
            logger.warning("No audio/video files found in: %s", input_dir)
            return []

        logger.info("Found %d file(s) to process.", len(files))
        results: List[Dict] = []

        for i, fp in enumerate(files, 1):
            logger.info("[%d/%d] %s", i, len(files), fp.name)
            r = self.process_file(str(fp), output_dir)
            r["input_file"] = str(fp)
            results.append(r)

        ok = sum(1 for r in results if r.get("success"))
        logger.info("Batch complete: %d/%d succeeded.", ok, len(results))
        return results

    # ------------------------------------------------------------------
    # Single conveyor cycle (PLC-triggered)
    # ------------------------------------------------------------------

    def _run_single_cycle(self, part_data: Dict) -> Dict:
        """Record → denoise → transcribe one inspection triggered by PLC.

        Parameters
        ----------
        part_data : Output of PLCClient.wait_for_part().

        Returns
        -------
        Result dict from process_file().
        """
        model_slug = part_data.get("model_name", "unknown").replace(" ", "_")
        engine     = part_data.get("engine_number", "unknown")
        label      = f"eng{engine}_{model_slug}"

        logger.info(
            "CYCLE START — engine=%s model=%s",
            engine, part_data.get("model_name"),
        )

        # Record
        logger.info("Recording ... (auto-stops on silence)")
        self.recorder.start_recording()
        self.recorder.wait_until_done()
        wav_path = str(self.output_dir / f"{label}.wav")
        self.recorder.stop_recording(output_path=wav_path)

        return self.process_file(
            input_path = wav_path,
            output_dir = str(self.output_dir),
            part_data  = part_data,
        )

    # ------------------------------------------------------------------
    # Conveyor loop (production mode)
    # ------------------------------------------------------------------

    def run_conveyor_loop(self) -> None:
        """Connect to PLC and process parts indefinitely. Ctrl+C to stop.

        Flow per cycle:
          1. Wait for M0 bit to go HIGH (part-present trigger)
          2. Read engine number (D100) and model code (D101)
          3. Reset M0 to 0 (acknowledge / re-arm PLC)
          4. Record operator voice until silence
          5. Denoise → transcribe → save files
          6. POST result JSON to backend_url
          7. Repeat
        """
        logger.info("=== AutoVoice Conveyor Loop ===")
        logger.info("Connecting to PLC %s:%d ...", self.plc.host, self.plc.port)

        if not self.plc.connect():
            raise RuntimeError(
                f"Cannot connect to PLC at {self.plc.host}:{self.plc.port}. "
                "Check IP address, port, and SLMP settings in GX Works3."
            )

        logger.info("PLC connected. Waiting for parts ...")

        try:
            while True:
                part_data = self.plc.wait_for_part(timeout_s=0)

                if part_data is None:
                    logger.warning("wait_for_part returned None — retrying in 1 s.")
                    time.sleep(1.0)
                    continue

                result = self._run_single_cycle(part_data)

                if result.get("success"):
                    self._post_result(result)
                else:
                    logger.error("Cycle failed: %s", result.get("error"))

                # Brief pause between parts
                time.sleep(0.5)

        except KeyboardInterrupt:
            logger.info("Conveyor loop stopped by user (Ctrl+C).")
        finally:
            self.plc.disconnect()

    # ------------------------------------------------------------------
    # Backend POST
    # ------------------------------------------------------------------

    def _post_result(self, result: Dict) -> None:
        """POST the inspection result JSON to the configured backend URL.

        Does nothing (silently) when backend_url is empty.
        """
        if not self.backend_url:
            return

        processed = result.get("processed_result", {})
        payload   = {
            "transcript":      processed.get("output", ""),
            "plc_data":        processed.get("plc_data", {}),
            "denoised_audio":  result.get("denoised_audio"),
            "transcript_file": result.get("transcript_file"),
            "metadata":        processed.get("metadata", {}),
        }

        try:
            response = httpx.post(
                self.backend_url,
                json    = payload,
                timeout = self.backend_timeout_s,
            )
            response.raise_for_status()
            logger.info("Backend accepted result (HTTP %d).", response.status_code)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Backend returned HTTP %d: %s",
                exc.response.status_code, exc.response.text,
            )
        except Exception as exc:
            logger.error("Failed to POST to backend (%s): %s", self.backend_url, exc)
