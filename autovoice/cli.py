"""
autovoice/cli.py
=================
Command-line interface for AutoVoice.

Commands
--------
autovoice conveyor   Start PLC conveyor loop (production mode)
autovoice process    Process a file or directory (no PLC required)
autovoice server     Start the FastAPI server
autovoice devices    List available microphone devices

Examples
--------
# Start the PLC conveyor loop
autovoice conveyor --plc-host 192.168.3.39

# Process a single file
autovoice process recording.m4a -o ./output

# Process all files in a folder
autovoice process ./audio_folder -o ./output

# Start API server
autovoice server --host 0.0.0.0 --port 8000

# List microphone devices
autovoice devices
"""

import argparse
import io
import logging
import sys
from pathlib import Path

# Fix Windows console UTF-8 encoding (prevents UnicodeEncodeError with
# special characters in log messages / transcripts on cmd.exe)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level  = level,
        format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt= "%H:%M:%S",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog        = "autovoice",
        description = "AutoVoice — voice isolation, denoising, and transcription with PLC integration",
    )
    parser.add_argument(
        "--version", action="version", version="AutoVoice 2.1.0"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------ conveyor
    conv = sub.add_parser(
        "conveyor",
        help="Start PLC-triggered conveyor loop (production mode)",
    )
    conv.add_argument("--plc-host",          default=None,  help="PLC IP address")
    conv.add_argument("--plc-port",          default=None,  type=int, help="SLMP TCP port")
    conv.add_argument("--bit",               default=None,  help="Part-present bit device (e.g. M0)")
    conv.add_argument("--engine-reg",        default=None,  help="Engine number word device (e.g. D100)")
    conv.add_argument("--model-reg",         default=None,  help="Model code word device (e.g. D101)")
    conv.add_argument("--silence-threshold", default=None,  type=float, help="RMS silence threshold (0.0–1.0)")
    conv.add_argument("--silence-duration",  default=None,  type=float, help="Seconds of silence before stop")
    conv.add_argument("--prop-decrease",     default=None,  type=float, help="noisereduce aggressiveness (0.0–1.0)")
    conv.add_argument("--no-noisereduce",    action="store_true",        help="Use spectral subtraction only")
    conv.add_argument("--model",             default=None,
                      choices=["tiny","base","small","medium","large","large-v2","turbo"],
                      help="Whisper model (default: turbo)")
    conv.add_argument("--backend",           default=None,  help="Backend URL for POST results")
    conv.add_argument("-o", "--output",      default=None,  help="Output directory for audio/transcripts")

    # ------------------------------------------------------------------ process
    proc = sub.add_parser(
        "process",
        help="Process a file or directory (no PLC required)",
    )
    proc.add_argument("input",               help="Audio/video file or directory to process")
    proc.add_argument("-o", "--output",      default=None, help="Output directory")
    proc.add_argument("--model",             default=None,
                      choices=["tiny","base","small","medium","large","large-v2","turbo"])
    proc.add_argument("--no-noisereduce",    action="store_true")
    proc.add_argument("--prop-decrease",     default=None, type=float)

    # ------------------------------------------------------------------ server
    srv = sub.add_parser(
        "server",
        help="Start the FastAPI REST API server",
    )
    srv.add_argument("--host",   default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    srv.add_argument("--port",   default=8000, type=int, help="Bind port (default: 8000)")
    srv.add_argument("--reload", action="store_true",    help="Auto-reload on code changes (dev mode)")

    # ------------------------------------------------------------------ devices
    sub.add_parser(
        "devices",
        help="List available microphone / audio devices",
    )

    return parser


def cmd_conveyor(args) -> None:
    from . import config
    from .core import AutoVoice

    kwargs = {}
    if args.plc_host:         kwargs["plc_host"]           = args.plc_host
    if args.plc_port:         kwargs["plc_port"]           = args.plc_port
    if args.bit:              kwargs["part_bit_device"]    = args.bit
    if args.engine_reg:       kwargs["engine_word_device"] = args.engine_reg
    if args.model_reg:        kwargs["model_word_device"]  = args.model_reg
    if args.silence_threshold is not None: kwargs["silence_threshold"]  = args.silence_threshold
    if args.silence_duration  is not None: kwargs["silence_duration_s"] = args.silence_duration
    if args.prop_decrease     is not None: kwargs["prop_decrease"]       = args.prop_decrease
    if args.model:            kwargs["whisper_model"]      = args.model
    if args.backend:          kwargs["backend_url"]        = args.backend
    if args.output:           kwargs["output_dir"]         = args.output
    if args.no_noisereduce:   kwargs["use_noisereduce"]    = False

    av = AutoVoice(**kwargs)
    av.run_conveyor_loop()


def cmd_process(args) -> None:
    from .core import AutoVoice

    kwargs = {}
    if args.model:            kwargs["whisper_model"]   = args.model
    if args.prop_decrease is not None: kwargs["prop_decrease"] = args.prop_decrease
    if args.no_noisereduce:   kwargs["use_noisereduce"] = False

    av = AutoVoice(**kwargs)
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"[ERROR] Path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if input_path.is_file():
        r = av.process_file(str(input_path), args.output)
        if r["success"]:
            print(f"[OK] Transcript saved: {r['transcript_file']}")
            print(f"     Denoised audio:   {r['denoised_audio']}")
        else:
            print(f"[FAIL] {r.get('error')}", file=sys.stderr)
            sys.exit(1)
    else:
        results = av.process_directory(str(input_path), args.output)
        ok = sum(1 for r in results if r.get("success"))
        print(f"\nDone: {ok}/{len(results)} files succeeded.")


def cmd_server(args) -> None:
    try:
        import uvicorn
    except ImportError:
        print("[ERROR] uvicorn not installed. Run: pip install uvicorn[standard]", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "autovoice.main:app",
        host   = args.host,
        port   = args.port,
        reload = args.reload,
        log_level = "info",
    )


def cmd_devices(args) -> None:
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_input = sd.query_devices(kind="input")
        print("\nAvailable audio devices:")
        print("-" * 60)
        print(devices)
        print(f"\nDefault input: {default_input['name']}")
    except Exception as exc:
        print(f"[ERROR] Could not query audio devices: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()
    _setup_logging(getattr(args, "verbose", False))

    dispatch = {
        "conveyor": cmd_conveyor,
        "process":  cmd_process,
        "server":   cmd_server,
        "devices":  cmd_devices,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
