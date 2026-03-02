@echo off
:: ============================================================
:: AutoVoice v2.1 — Windows Setup Script
::
:: Installs everything needed in the correct order.
:: No Rust, no C++ Build Tools, no compilation required.
::
:: Requirements:
::   - Python 3.10 or 3.11  (https://www.python.org/downloads/)
::   - Internet connection   (for package downloads)
::
:: Usage:
::   1. Open Command Prompt in the autovoice project folder
::   2. Run:  scripts\setup.bat
:: ============================================================

setlocal enabledelayedexpansion

echo.
echo ============================================================
echo  AutoVoice v2.1 - Windows Setup
echo  Denoiser: noisereduce (pure Python - no Rust required)
echo ============================================================
echo.

:: ---- Check Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo.
    echo         Install Python 3.10 or 3.11 from:
    echo         https://www.python.org/downloads/
    echo.
    echo         IMPORTANT: Check "Add Python to PATH" during install.
    echo.
    pause & exit /b 1
)

:: Check version is 3.10 or 3.11
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found.

:: ---- Create virtual environment ----
echo.
if not exist "venv" (
    echo [STEP] Creating virtual environment ...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause & exit /b 1
    )
    echo [OK]   Virtual environment created.
) else (
    echo [OK]   Virtual environment already exists.
)

:: ---- Activate ----
call venv\Scripts\activate.bat
echo [OK] Virtual environment activated.

:: ---- Upgrade pip and base tools ----
echo.
echo [STEP] Upgrading pip, setuptools, wheel, packaging ...
python -m pip install --upgrade pip setuptools wheel packaging
if errorlevel 1 (
    echo [ERROR] Failed to upgrade base tools.
    pause & exit /b 1
)
echo [OK]   Base tools upgraded.

:: ---- PyTorch CPU (MUST come before requirements.txt) ----
echo.
echo [STEP] Installing PyTorch CPU build (approx 200 MB) ...
echo        This uses a specific index URL to avoid downloading the 2.5 GB CUDA version.
pip install torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo [ERROR] PyTorch CPU install failed.
    echo         Check your internet connection and try again.
    pause & exit /b 1
)
echo [OK]   PyTorch installed.

:: ---- Remaining requirements ----
echo.
echo [STEP] Installing AutoVoice dependencies ...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed.
    echo         Run "pip check" to see what went wrong.
    pause & exit /b 1
)
echo [OK]   Dependencies installed.

:: ---- Install AutoVoice package ----
echo.
echo [STEP] Installing AutoVoice package ...
pip install -e .
if errorlevel 1 (
    echo [ERROR] AutoVoice package install failed.
    pause & exit /b 1
)
echo [OK]   AutoVoice 2.1.0 installed.

:: ---- Pre-download Whisper turbo model ----
echo.
echo [STEP] Pre-downloading Whisper turbo model (~809 MB, first time only) ...
echo        This may take several minutes depending on your internet speed.
python -c "import whisper; whisper.load_model('turbo'); print('[OK]   Whisper turbo model ready.')"
if errorlevel 1 (
    echo [WARN] Whisper model download failed or was interrupted.
    echo        It will download automatically on first use.
)

:: ---- Check microphone ----
echo.
echo [STEP] Checking microphone ...
python -c "import sounddevice as sd; devs = sd.query_devices(); inp = sd.query_devices(kind='input'); print('[OK]   Microphone found: ' + inp['name'])"
if errorlevel 1 (
    echo [WARN] No microphone detected.
    echo        Go to Windows Settings ^> System ^> Sound ^> Input to configure one.
)

:: ---- Check pip conflicts ----
echo.
echo [STEP] Checking for dependency conflicts ...
pip check
if errorlevel 1 (
    echo [WARN] Some conflicts found above. Try:
    echo        python -m pip install --upgrade pip setuptools wheel packaging
    echo        pip install -r requirements.txt --upgrade
)

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  NEXT STEP: Edit autovoice\config.py and set your PLC IP:
echo    PLC_HOST = "192.168.3.39"
echo.
echo  Then choose how to run AutoVoice:
echo.
echo  Option A - CLI conveyor loop (PLC-triggered, production):
echo    venv\Scripts\activate
echo    autovoice conveyor --plc-host 192.168.3.39
echo.
echo  Option B - API server (for frontend/backend integration):
echo    venv\Scripts\activate
echo    autovoice server --host 0.0.0.0 --port 8000
echo    (Swagger UI at http://localhost:8000/docs)
echo.
echo  Option C - Process a file (no PLC, for testing):
echo    venv\Scripts\activate
echo    autovoice process "C:\path\to\recording.m4a" -o ".\data\output"
echo.
echo  List audio devices:
echo    autovoice devices
echo.
echo  Run tests:
echo    pip install pytest
echo    pytest tests\ -v
echo ============================================================
echo.
pause
