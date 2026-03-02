"""
AutoVoice — Central configuration
==================================
Edit this file to match your hardware before running.
All settings can also be overridden via environment variables
(useful for Docker / CI deployments).

Environment variable names match the constant names exactly,
e.g. PLC_HOST=192.168.1.5 overrides PLC_HOST below.
"""

import os

# ---------------------------------------------------------------------------
# Mitsubishi iQ-R PLC — SLMP / MC Protocol, 3E Binary Frame, TCP
# ---------------------------------------------------------------------------
# How to find these values in GX Works3:
#   Navigation → Parameter → Module Parameter → Ethernet Configuration

PLC_HOST            = os.getenv("PLC_HOST",            "192.168.3.39")
PLC_PORT            = int(os.getenv("PLC_PORT",         "3000"))

# Device addresses — change to match your GX Works3 variable assignments
PART_BIT_DEVICE     = os.getenv("PART_BIT_DEVICE",     "M0")    # bit set HIGH when part passes sensor
ENGINE_WORD_DEVICE  = os.getenv("ENGINE_WORD_DEVICE",  "D100")  # engine serial number
MODEL_WORD_DEVICE   = os.getenv("MODEL_WORD_DEVICE",   "D101")  # model code integer

PLC_POLL_INTERVAL_S = float(os.getenv("PLC_POLL_INTERVAL_S", "0.1"))   # seconds between polls

# ---------------------------------------------------------------------------
# Audio denoising — noisereduce
# ---------------------------------------------------------------------------
USE_NOISEREDUCE  = os.getenv("USE_NOISEREDUCE",  "true").lower() == "true"

# 0.0–1.0: how aggressively to reduce noise
#   0.5  = gentle, more natural voice
#   0.75 = recommended for factory floor (default)
#   0.9  = aggressive, good for heavy machinery nearby
PROP_DECREASE    = float(os.getenv("PROP_DECREASE",  "0.75"))

# True  = assume background noise is constant (conveyor hum, HVAC)
# False = variable noise (people talking nearby)
STATIONARY_NOISE = os.getenv("STATIONARY_NOISE", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Audio recording — microphone + silence detection
# ---------------------------------------------------------------------------

# RMS amplitude below which audio is treated as silent (0.0–1.0 scale)
#   0.01 = office / quiet room (default)
#   0.02 = light factory noise
#   0.05 = heavy machinery nearby
SILENCE_THRESHOLD   = float(os.getenv("SILENCE_THRESHOLD",   "0.01"))

# Consecutive seconds of silence before auto-stop
SILENCE_DURATION_S  = float(os.getenv("SILENCE_DURATION_S",  "3.0"))

# Minimum recording time before silence detection activates
# (prevents stopping on a leading pause before operator speaks)
MIN_RECORD_S        = float(os.getenv("MIN_RECORD_S",        "1.0"))

# Hard maximum recording time (safety fallback)
MAX_RECORD_S        = float(os.getenv("MAX_RECORD_S",        "120.0"))

# ---------------------------------------------------------------------------
# Whisper speech-to-text
# ---------------------------------------------------------------------------
# Model options (larger = more accurate, slower):
#   tiny  ~39M params  | ~1 GB RAM  | fastest
#   base  ~74M params  | ~1 GB RAM
#   small ~244M params | ~2 GB RAM
#   medium ~769M       | ~5 GB RAM
#   large ~1550M       | ~10 GB RAM
#   turbo ~809M        | ~3 GB RAM  | recommended (best speed/accuracy)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "turbo")

# Transcription prompt — tells Whisper the context and common vocabulary
# Edit to include terminology specific to your inspection process
WHISPER_PROMPT = os.getenv("WHISPER_PROMPT", (
    "You are a mechanic logging issues found in inspections of vehicles "
    "for an Indian automotive manufacturer. Transcribe the audio clearly "
    "so it reads like a vehicle issue report. Do not make anything up. "
    "If words are unclear, use the closest automotive term that fits the context. "
    "Common terms: bearing, bolt, axle, mounting, shock absorber, engine, "
    "transmission, electrical, cable, tightening, defect, torque, clearance, "
    "Pulsar 125, Pulsar N160, Dominar, dynamometer."
))

# ---------------------------------------------------------------------------
# Backend integration
# ---------------------------------------------------------------------------
# AutoVoice POSTs a JSON result to this URL after each conveyor cycle.
# Set this to your backend's inspection result endpoint.
# Set to empty string "" to disable POSTing (store-only mode).
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:3001/api/inspection-result")

# Timeout in seconds for the backend POST request
BACKEND_TIMEOUT_S = float(os.getenv("BACKEND_TIMEOUT_S", "30.0"))

# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------
# Directory where denoised MP3s and transcript TXT files are saved
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./data/output")
