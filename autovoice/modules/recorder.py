"""
autovoice/modules/recorder.py
===============================
Live microphone recorder with silence-based auto-stop.

How it works
-------------
1. start_recording() opens the default system microphone via sounddevice.
2. Audio chunks arrive via a sounddevice callback into a thread-safe queue.
3. A background monitor thread drains the queue, accumulates frames, and
   computes RMS energy per chunk.
4. When RMS stays below `silence_threshold` for `silence_duration_s`
   consecutive seconds — and at least `min_record_s` of audio has been
   captured — the monitor signals stop.
5. stop_recording(output_path) flushes remaining chunks and writes a WAV.

Windows note
-------------
sounddevice bundles a static PortAudio library on Windows, so no separate
PortAudio install is required.  The Windows default audio input device
(configured in Sound Settings) is used automatically.

Tuning for factory floors
--------------------------
Typical conveyor + machinery background RMS is 0.015–0.04.
Set silence_threshold slightly above your background RMS so silence
detection triggers only when the operator stops talking, not when they
pause between sentences.  Start at 0.02 and adjust up if needed.
"""

import logging
import queue
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

# Whisper's native sample rate — avoids resampling step in transcription
_SAMPLE_RATE  = 16_000   # Hz
_CHANNELS     = 1        # mono
_CHUNK_FRAMES = 1_024    # frames per sounddevice callback (~64 ms at 16 kHz)


class AudioRecorder:
    """Microphone recorder that auto-stops on sustained silence.

    Parameters
    ----------
    silence_threshold  : RMS amplitude (0.0–1.0) below which a chunk
                         is silent. Tune this for your environment:
                         0.01 = quiet office, 0.02–0.05 = factory floor.
    silence_duration_s : Consecutive seconds of silence before stop.
    min_record_s       : Minimum capture time before silence detection
                         activates (guards against stopping on a leading
                         pause before the operator begins speaking).
    max_record_s       : Hard upper limit on recording time (safety fallback).
    sample_rate        : Microphone sample rate in Hz.
    """

    def __init__(
        self,
        silence_threshold:  float = 0.01,
        silence_duration_s: float = 3.0,
        min_record_s:       float = 1.0,
        max_record_s:       float = 120.0,
        sample_rate:        int   = _SAMPLE_RATE,
    ) -> None:
        self.silence_threshold  = silence_threshold
        self.silence_duration_s = silence_duration_s
        self.min_record_s       = min_record_s
        self.max_record_s       = max_record_s
        self.sample_rate        = sample_rate

        self._queue:          queue.Queue     = queue.Queue()
        self._stop_event:     threading.Event = threading.Event()
        self._stream:         Optional[sd.InputStream]  = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._frames:         list[np.ndarray] = []
        self._recording:      bool = False

    # ------------------------------------------------------------------
    # sounddevice callback (runs in C audio thread — no blocking!)
    # ------------------------------------------------------------------

    def _sd_callback(
        self,
        indata:    np.ndarray,
        frames:    int,
        time_info,
        status,
    ) -> None:
        if status:
            logger.debug("sounddevice status: %s", status)
        self._queue.put(indata.copy())

    # ------------------------------------------------------------------
    # Background monitor thread
    # ------------------------------------------------------------------

    def _monitor(self) -> None:
        """Drain queue, detect silence, set stop event when done."""
        silence_start: Optional[float] = None
        record_start = time.monotonic()

        while not self._stop_event.is_set():
            try:
                chunk = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            self._frames.append(chunk)
            elapsed = time.monotonic() - record_start

            # Hard maximum safety guard
            if elapsed >= self.max_record_s:
                logger.info("Max recording time reached (%.0f s).", self.max_record_s)
                self._stop_event.set()
                break

            # Don't start silence detection until min_record_s has elapsed
            if elapsed < self.min_record_s:
                silence_start = None
                continue

            # RMS of this chunk (int16 → float32 normalised to ±1)
            rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) / 32768.0

            if rms < self.silence_threshold:
                if silence_start is None:
                    silence_start = time.monotonic()
                elif (time.monotonic() - silence_start) >= self.silence_duration_s:
                    logger.info(
                        "%.1f s of silence detected — stopping.", self.silence_duration_s
                    )
                    self._stop_event.set()
                    break
            else:
                silence_start = None   # reset on any speech chunk

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_recording(self) -> None:
        """Open microphone and start capturing audio (non-blocking).

        Recording runs in background threads.  Call wait_until_done()
        to block, then stop_recording() to save and retrieve the WAV.
        """
        if self._recording:
            logger.warning("Already recording — call stop_recording() first.")
            return

        self._frames.clear()
        self._stop_event.clear()
        self._queue = queue.Queue()

        self._stream = sd.InputStream(
            samplerate = self.sample_rate,
            channels   = _CHANNELS,
            dtype      = "int16",
            blocksize  = _CHUNK_FRAMES,
            callback   = self._sd_callback,
        )
        self._stream.start()
        self._recording = True

        self._monitor_thread = threading.Thread(
            target=self._monitor, daemon=True, name="AudioMonitor"
        )
        self._monitor_thread.start()

        try:
            mic_name = sd.query_devices(kind="input")["name"]
        except Exception:
            mic_name = "default"
        logger.info("Recording started on mic: %s", mic_name)

    def wait_until_done(self) -> None:
        """Block until the recording stops (silence or max time reached)."""
        if self._monitor_thread:
            self._monitor_thread.join()

    def stop_recording(self, output_path: Optional[str] = None) -> str:
        """Stop recording and save the captured audio as a WAV file.

        Parameters
        ----------
        output_path : Destination WAV file path.  If None, a temp file
                      in the system temp directory is created.

        Returns
        -------
        Absolute path of the saved WAV file.
        """
        # Signal the monitor thread to stop and wait for it
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)

        # Close the sounddevice stream
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning("Error closing audio stream: %s", exc)
            self._stream = None

        self._recording = False

        # Drain any remaining queued chunks
        while not self._queue.empty():
            try:
                self._frames.append(self._queue.get_nowait())
            except queue.Empty:
                break

        # Determine output path
        if output_path is None:
            import os
            tmp_fd, output_path = tempfile.mkstemp(
                suffix=".wav", prefix="autovoice_rec_"
            )
            os.close(tmp_fd)

        output_path = str(output_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Write WAV file
        if self._frames:
            audio_data = np.concatenate(self._frames, axis=0)
            with wave.open(output_path, "wb") as wf:
                wf.setnchannels(_CHANNELS)
                wf.setsampwidth(2)                  # int16 = 2 bytes per sample
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())
            duration = len(audio_data) / self.sample_rate
            logger.info("Saved %.1f s → %s", duration, output_path)
        else:
            logger.warning("No audio captured — writing empty WAV.")
            with wave.open(output_path, "wb") as wf:
                wf.setnchannels(_CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)

        return output_path

    @property
    def is_recording(self) -> bool:
        """True if recording is currently active."""
        return self._recording
