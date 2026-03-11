# =============================================================
# AutoVoice — Python 3.11 Docker image
# =============================================================
#
# Build:
#   docker build -t autovoice .
#
# Run (API server):
#   docker run -p 8000:8000 \
#     -e PLC_HOST=192.168.3.39 \
#     -e BACKEND_URL=http://your-backend:3001/api/inspection-result \
#     -v $(pwd)/data:/app/data \
#     autovoice
#
# Run (CLI conveyor loop):
#   docker run --network host \
#     -e PLC_HOST=192.168.3.39 \
#     -v $(pwd)/data:/app/data \
#     autovoice conveyor --plc-host 192.168.3.39
#
# Notes:
#   - Uses Python 3.11-slim (Debian Bookworm) — avoids Alpine
#     because soundfile/librosa need glibc not musl
#   - PyTorch CPU-only build (~800 MB) — no CUDA drivers needed
#   - Whisper model is downloaded at build time into the image
#     so containers start instantly with no download delay
#   - ffmpeg installed via apt (more reliable than imageio-ffmpeg
#     on Linux containers)
# =============================================================

FROM python:3.11-slim-bookworm

# ── System labels ─────────────────────────────────────────────
LABEL maintainer="AutoVoice"
LABEL version="2.1.0"
LABEL description="AutoVoice: voice denoising + Whisper transcription + Mitsubishi PLC integration"

# ── Build arguments ───────────────────────────────────────────
# Change WHISPER_MODEL to pre-download a different model size
ARG WHISPER_MODEL=turbo

# ── Environment variables ─────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Whisper model cache inside the image
    XDG_CACHE_HOME=/root/.cache \
    # AutoVoice settings (all overridable at runtime via -e)
    PLC_HOST=192.168.3.39 \
    PLC_PORT=3000 \
    PART_BIT_DEVICE=M0 \
    ENGINE_WORD_DEVICE=D100 \
    MODEL_WORD_DEVICE=D101 \
    WHISPER_MODEL=${WHISPER_MODEL} \
    USE_NOISEREDUCE=true \
    PROP_DECREASE=0.75 \
    STATIONARY_NOISE=true \
    SILENCE_THRESHOLD=0.01 \
    SILENCE_DURATION_S=3.0 \
    MIN_RECORD_S=1.0 \
    MAX_RECORD_S=120.0 \
    BACKEND_URL=http://localhost:3001/api/inspection-result \
    BACKEND_TIMEOUT_S=30.0 \
    OUTPUT_DIR=/app/data/output \
    CORS_ORIGINS=* \
    PORT=8000

# ── System dependencies ───────────────────────────────────────
# ffmpeg:        audio extraction (used by Whisper + denoiser)
# libsndfile1:   soundfile backend for librosa/soundfile
# libportaudio2: PortAudio for sounddevice (microphone recording)
# build-essential: needed for some numpy/scipy wheels on slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        libportaudio2 \
        build-essential \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────
# Copy requirements first so Docker layer cache avoids reinstalling
# dependencies when only source code changes.

COPY requirements.txt .

# Step 1: upgrade pip toolchain
RUN pip install --upgrade pip setuptools wheel packaging

# Step 2: PyTorch CPU-only (MUST come before requirements.txt install)
# Using the official PyTorch CPU index avoids downloading the 2.5 GB CUDA build
RUN pip install \
    torch==2.3.1 \
    torchaudio==2.3.1 \
    --index-url https://download.pytorch.org/whl/cpu \
    --no-cache-dir

# Step 3: all other dependencies
RUN pip install -r requirements.txt --no-cache-dir

# ── Copy source code ──────────────────────────────────────────
COPY . .

# ── Install the AutoVoice package ────────────────────────────
RUN pip install -e . --no-cache-dir

# ── Pre-download Whisper model ────────────────────────────────
# Baking the model into the image means no download on container start.
# The model is cached at /root/.cache/whisper/ inside the image.
# To skip this (smaller image, download on first run), remove these lines.
RUN python -c "\
import whisper, os; \
os.makedirs('/root/.cache/whisper', exist_ok=True); \
print(f'Downloading Whisper model: ${WHISPER_MODEL}'); \
whisper.load_model('${WHISPER_MODEL}', download_root='/root/.cache/whisper'); \
print('Whisper model ready.')"

# ── Output directory ──────────────────────────────────────────
RUN mkdir -p /app/data/output /app/data/samples /app/uploads

# ── Expose API port ───────────────────────────────────────────
EXPOSE 8000

# ── Health check ─────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── Default command — start API server ───────────────────────
# Override at runtime:
#   docker run autovoice conveyor --plc-host 192.168.3.39
#   docker run autovoice process /app/data/samples/file.m4a -o /app/data/output
CMD ["autovoice", "server", "--host", "0.0.0.0", "--port", "8000"]
