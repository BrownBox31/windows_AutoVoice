"""
AutoVoice — Voice isolation, denoising, and transcription toolkit
with Mitsubishi iQ-R PLC integration for automotive inspection lines.

Quickstart
----------
>>> from autovoice import AutoVoice
>>> av = AutoVoice(plc_host="192.168.3.39", backend_url="http://localhost:3001/api/inspection-result")
>>> av.process_file("recording.m4a", output_dir="./output")
>>> av.run_conveyor_loop()   # blocks until Ctrl+C
"""

from .core import AutoVoice
from .modules.denoiser import AudioDenoiser
from .modules.transcriber import AudioTranscriber
from .modules.postprocessor import TextPostProcessor
from .modules.plc_client import PLCClient
from .modules.recorder import AudioRecorder

__version__ = "2.1.0"
__author__  = "AutoVoice"
__all__ = [
    "AutoVoice",
    "AudioDenoiser",
    "AudioTranscriber",
    "TextPostProcessor",
    "PLCClient",
    "AudioRecorder",
]
