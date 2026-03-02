"""
autovoice/main.py
==================
FastAPI REST API server for AutoVoice.

This server acts as the bridge between your frontend/backend and the
AutoVoice Python pipeline.  It exposes HTTP endpoints for:
  - File upload → denoise → transcribe
  - PLC conveyor loop start/stop/status
  - Inspection result storage and retrieval

Start the server
-----------------
    python -m uvicorn autovoice.main:app --host 0.0.0.0 --port 8000
    # or
    autovoice server --host 0.0.0.0 --port 8000

Interactive API docs
---------------------
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)

All settings (PLC host, Whisper model, backend URL …) are read from
environment variables at startup.  See config.py for the full list.
"""

import logging
import os
import shutil
import threading
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config
from .core import AutoVoice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title       = "AutoVoice API",
    description = (
        "Voice isolation, denoising (noisereduce), and transcription (Whisper) "
        "with Mitsubishi iQ-R PLC integration for automotive inspection lines."
    ),
    version     = "2.1.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# CORS — allow any frontend origin in development.
# Restrict origins in production by setting CORS_ORIGINS env var:
#   CORS_ORIGINS=http://localhost:3000,https://yourdomain.com
_cors_origins_env = os.getenv("CORS_ORIGINS", "*")
_cors_origins = (
    ["*"] if _cors_origins_env == "*"
    else [o.strip() for o in _cors_origins_env.split(",")]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = _cors_origins,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ---------------------------------------------------------------------------
# Temp directory for uploaded files
# ---------------------------------------------------------------------------
_UPLOAD_DIR = os.path.join(os.getcwd(), "uploads")
os.makedirs(_UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_autovoice:      Optional[AutoVoice]         = None
_plc_thread:     Optional[threading.Thread]  = None
_plc_running:    bool                        = False
_results_store:  List[Dict[str, Any]]        = []   # in-memory (replace with DB for persistence)
_MAX_RESULTS     = 500


def _get_autovoice() -> AutoVoice:
    """Lazy-initialise AutoVoice (loads Whisper model on first call)."""
    global _autovoice
    if _autovoice is None:
        logger.info("Initialising AutoVoice (loading Whisper model) …")
        _autovoice = AutoVoice(
            use_noisereduce    = config.USE_NOISEREDUCE,
            prop_decrease      = config.PROP_DECREASE,
            stationary_noise   = config.STATIONARY_NOISE,
            whisper_model      = config.WHISPER_MODEL,
            plc_host           = config.PLC_HOST,
            plc_port           = config.PLC_PORT,
            part_bit_device    = config.PART_BIT_DEVICE,
            engine_word_device = config.ENGINE_WORD_DEVICE,
            model_word_device  = config.MODEL_WORD_DEVICE,
            silence_threshold  = config.SILENCE_THRESHOLD,
            silence_duration_s = config.SILENCE_DURATION_S,
            min_record_s       = config.MIN_RECORD_S,
            max_record_s       = config.MAX_RECORD_S,
            backend_url        = config.BACKEND_URL,
            backend_timeout_s  = config.BACKEND_TIMEOUT_S,
            output_dir         = config.OUTPUT_DIR,
        )
        logger.info("AutoVoice ready.")
    return _autovoice


# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event() -> None:
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("AutoVoice API starting …")
    _get_autovoice()   # pre-load Whisper model so first request is fast
    logger.info("AutoVoice API ready on http://0.0.0.0:8000")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get(
    "/health",
    tags    = ["System"],
    summary = "Liveness check",
)
async def health_check() -> Dict:
    """Returns 200 OK when the server is up.

    Also reports PLC loop status and loaded Whisper model.
    """
    av = _get_autovoice()
    return {
        "status":           "ok",
        "version":          "2.1.0",
        "plc_loop_running": _plc_running,
        "whisper_model":    av.transcriber.model_name,
        "plc_host":         av.plc.host,
        "plc_port":         av.plc.port,
    }


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------
@app.post(
    "/process-audio",
    tags    = ["Processing"],
    summary = "Upload an audio file and receive a transcript",
)
async def process_audio(
    input_file: UploadFile = File(...,  description="Audio/video file (MP3, M4A, WAV, MP4, AAC …)"),
    output_dir: str        = Form(config.OUTPUT_DIR, description="Server-side output directory"),
) -> Dict:
    """Upload a recording and run the full AutoVoice pipeline.

    Steps performed server-side:
    1. Save uploaded file to disk
    2. Extract audio via FFmpeg
    3. Denoise with noisereduce + spectral subtraction
    4. Transcribe with Whisper (CPU)
    5. Return structured result JSON

    The denoised MP3 and transcript TXT are saved to `output_dir`.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Save upload to temp location
    safe_name  = os.path.basename(input_file.filename or "upload.wav")
    tmp_path   = os.path.join(_UPLOAD_DIR, safe_name)

    try:
        with open(tmp_path, "wb") as buf:
            shutil.copyfileobj(input_file.file, buf)

        result = _get_autovoice().process_file(
            input_path = tmp_path,
            output_dir = output_dir,
        )
    except Exception as exc:
        logger.error("process_audio failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        # Clean up the upload temp file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    if not result.get("success"):
        raise HTTPException(
            status_code=422,
            detail=result.get("error", "Processing failed"),
        )

    return {"status": "success", "result": result}


# ---------------------------------------------------------------------------
# Inspection result storage
# (AutoVoice core.py POSTs here; your own backend can also POST here)
# ---------------------------------------------------------------------------
@app.post(
    "/inspection-result",
    tags    = ["Results"],
    summary = "Store an inspection result (called by AutoVoice core after each cycle)",
)
async def receive_inspection_result(payload: Dict[str, Any]) -> Dict:
    """Accept a JSON inspection result and store it in memory.

    AutoVoice core.py POSTs to this endpoint automatically after each
    conveyor cycle when BACKEND_URL points here.  You can also POST
    results from any external source.

    Expected payload
    ----------------
    {
        "transcript":      "...",
        "plc_data":        {"engine_number": ..., "model_name": "..."},
        "denoised_audio":  "/path/to/file.mp3",
        "transcript_file": "/path/to/file.txt",
        "metadata":        {"word_count": ..., ...}
    }
    """
    _results_store.insert(0, payload)
    if len(_results_store) > _MAX_RESULTS:
        _results_store.pop()

    engine = payload.get("plc_data", {}).get("engine_number", "?")
    model  = payload.get("plc_data", {}).get("model_name", "?")
    logger.info("Inspection stored: engine=%s model=%s", engine, model)

    return {"status": "received", "total": len(_results_store)}


@app.get(
    "/inspection-results",
    tags    = ["Results"],
    summary = "Retrieve stored inspection results",
)
async def get_inspection_results(
    limit:  int = 50,
    offset: int = 0,
) -> Dict:
    """Return stored inspection results (newest first).

    Parameters
    ----------
    limit  : Maximum number of results to return (default 50, max 200).
    offset : Pagination offset (default 0).
    """
    limit   = min(limit, 200)
    page    = _results_store[offset : offset + limit]
    return {
        "total":   len(_results_store),
        "limit":   limit,
        "offset":  offset,
        "results": page,
    }


@app.delete(
    "/inspection-results",
    tags    = ["Results"],
    summary = "Clear all stored inspection results",
)
async def clear_inspection_results() -> Dict:
    """Delete all in-memory inspection results."""
    _results_store.clear()
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# PLC conveyor loop control
# ---------------------------------------------------------------------------
def _plc_loop_runner() -> None:
    global _plc_running
    _plc_running = True
    try:
        _get_autovoice().run_conveyor_loop()
    except Exception as exc:
        logger.error("PLC loop error: %s", exc)
    finally:
        _plc_running = False


@app.post(
    "/plc/start-loop",
    tags    = ["PLC"],
    summary = "Start the PLC conveyor loop",
)
async def start_plc_loop() -> Dict:
    """Start polling the Mitsubishi iQ-R PLC for conveyor triggers.

    The loop runs in a background thread.  Each time a part passes the
    sensor (M0 goes HIGH), AutoVoice records, transcribes, and stores
    the result.  Use GET /plc/status to monitor.
    """
    global _plc_thread, _plc_running

    if _plc_running:
        return {"status": "already_running"}

    _plc_thread = threading.Thread(
        target=_plc_loop_runner, daemon=True, name="PLCLoop"
    )
    _plc_thread.start()
    return {"status": "started"}


@app.post(
    "/plc/stop-loop",
    tags    = ["PLC"],
    summary = "Stop the PLC conveyor loop",
)
async def stop_plc_loop() -> Dict:
    """Disconnect from the PLC and stop the conveyor loop.

    The current cycle (if any) will complete before stopping.
    """
    global _plc_running
    av = _get_autovoice()
    av.plc.disconnect()
    _plc_running = False
    return {"status": "stopping"}


@app.get(
    "/plc/status",
    tags    = ["PLC"],
    summary = "Get PLC connection and loop status",
)
async def plc_status() -> Dict:
    """Return the current PLC loop state and connection details."""
    av = _get_autovoice()
    return {
        "plc_loop_running": _plc_running,
        "plc_connected":    av.plc.is_connected,
        "plc_host":         av.plc.host,
        "plc_port":         av.plc.port,
        "part_bit_device":  av.plc.part_bit_device,
    }
