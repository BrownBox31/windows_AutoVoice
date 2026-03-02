"""
autovoice/modules/plc_client.py
=================================
Mitsubishi iQ-R PLC client using SLMP / MC Protocol over Ethernet.

Protocol overview
-----------------
The Mitsubishi iQ-R exposes its device memory over TCP/IP using the
SLMP (Seamless Message Protocol) 3E frame format.  The pymcprotocol
library handles all frame construction and parsing.

Expected PLC memory layout
---------------------------
Device  Type  Description
------  ----  -----------
M0      Bit   PART_PRESENT — set HIGH by PLC ladder when part passes sensor.
              AutoVoice resets this to 0 immediately after reading, so the
              PLC knows the PC has acknowledged the trigger.
D100    Word  ENGINE_NUMBER — engine serial number written by PLC before M0.
D101    Word  MODEL_CODE — integer code for the vehicle model.

Device addresses are configurable in config.py or via environment variables.

GX Works3 setup required
--------------------------
1. Navigation → Parameter → Module Parameter → Ethernet Configuration:
   Set the PLC's IP address.
2. Add SLMP Connection Module:
   Protocol: TCP, Port: 3000, Frame: 3E Binary.
3. Ladder / ST program:
   - Write engine serial number to D100
   - Write model code to D101
   - Set M0 := TRUE
   when the photoelectric sensor detects a part on the conveyor.
"""

import logging
import time
from typing import Dict, Optional

import pymcprotocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model code → human-readable name
# Add entries to match the codes your PLC team uses in D101
# ---------------------------------------------------------------------------
MODEL_CODE_MAP: Dict[int, str] = {
    0: "Unknown",
    1: "Pulsar 125",
    2: "Pulsar N160",
    3: "Pulsar NS200",
    4: "Dominar 400",
    5: "Avenger 220",
}


class PLCClient:
    """SLMP client for Mitsubishi iQ-R PLC.

    Connects via TCP using the 3E Binary frame (QnA-compatible),
    which is the standard for iQ-R built-in Ethernet ports.

    Parameters
    ----------
    host               : PLC IP address (from GX Works3 Ethernet Configuration).
    port               : SLMP TCP port (default 3000, GX Works3 default).
    part_bit_device    : Bit device for the part-present trigger (e.g. "M0").
    engine_word_device : Word device for engine serial number (e.g. "D100").
    model_word_device  : Word device for model code integer (e.g. "D101").
    poll_interval_s    : Seconds between each trigger-bit poll (default 0.1).
    connect_timeout_s  : TCP connection timeout in seconds (default 5.0).
    """

    def __init__(
        self,
        host:               str   = "192.168.3.39",
        port:               int   = 3000,
        part_bit_device:    str   = "M0",
        engine_word_device: str   = "D100",
        model_word_device:  str   = "D101",
        poll_interval_s:    float = 0.1,
        connect_timeout_s:  float = 5.0,
    ) -> None:
        self.host               = host
        self.port               = port
        self.part_bit_device    = part_bit_device
        self.engine_word_device = engine_word_device
        self.model_word_device  = model_word_device
        self.poll_interval_s    = poll_interval_s
        self.connect_timeout_s  = connect_timeout_s

        self._plc:       Optional[pymcprotocol.Type3E] = None
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open TCP connection to the PLC.

        Returns True on success, False on failure.
        """
        try:
            self._plc = pymcprotocol.Type3E(plctype="iQ-R")
            # Default is binary mode (faster). To use ASCII mode:
            # self._plc.setaccessopt(commtype="ascii")
            self._plc.connect(self.host, self.port)
            self._connected = True
            logger.info("PLC connected: %s:%d", self.host, self.port)
            return True
        except Exception as exc:
            logger.error("PLC connection failed: %s", exc)
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Close the TCP connection."""
        if self._plc and self._connected:
            try:
                self._plc.close()
            except Exception:
                pass
        self._connected = False
        logger.info("PLC disconnected.")

    @property
    def is_connected(self) -> bool:
        """True if the TCP connection is currently open."""
        return self._connected

    # ------------------------------------------------------------------
    # Low-level device I/O
    # ------------------------------------------------------------------

    def _read_bit(self, device: str) -> int:
        """Read a single bit device.  Returns 0, 1, or -1 on error."""
        try:
            values = self._plc.batchread_bitunits(headdevice=device, readsize=1)
            return int(values[0])
        except Exception as exc:
            logger.warning("Bit read error [%s]: %s", device, exc)
            return -1

    def _write_bit(self, device: str, value: int) -> bool:
        """Write 0 or 1 to a bit device.  Returns True on success."""
        try:
            self._plc.batchwrite_bitunits(headdevice=device, values=[value])
            return True
        except Exception as exc:
            logger.warning("Bit write error [%s]: %s", device, exc)
            return False

    def _read_word(self, device: str) -> int:
        """Read a single word device.  Returns the integer value or -1 on error."""
        try:
            values = self._plc.batchread_wordunits(headdevice=device, readsize=1)
            return int(values[0])
        except Exception as exc:
            logger.warning("Word read error [%s]: %s", device, exc)
            return -1

    # ------------------------------------------------------------------
    # High-level part-detection API
    # ------------------------------------------------------------------

    def check_part_present(self) -> bool:
        """Return True if the part-present trigger bit is HIGH."""
        return self._read_bit(self.part_bit_device) == 1

    def read_part_data(self) -> Optional[Dict]:
        """Read engine number and model code from PLC word registers.

        Call this after the part-present bit goes HIGH.

        Returns
        -------
        Dict with keys engine_number (int), model_code (int), model_name (str).
        None if reads failed.
        """
        if not self._connected:
            logger.error("Cannot read part data — not connected.")
            return None

        engine_number = self._read_word(self.engine_word_device)
        model_code    = self._read_word(self.model_word_device)

        if engine_number < 0 or model_code < 0:
            logger.error("Part data read failed (engine=%d model=%d).", engine_number, model_code)
            return None

        return {
            "engine_number": engine_number,
            "model_code":    model_code,
            "model_name":    MODEL_CODE_MAP.get(model_code, f"ModelCode{model_code}"),
        }

    def reset_trigger(self) -> bool:
        """Write 0 to the part-present bit to acknowledge the trigger.

        The PLC ladder monitors this bit — when it returns to 0, the
        system re-arms for the next part on the conveyor.
        """
        ok = self._write_bit(self.part_bit_device, 0)
        if ok:
            logger.debug("Trigger bit %s reset to 0.", self.part_bit_device)
        return ok

    def wait_for_part(self, timeout_s: float = 0.0) -> Optional[Dict]:
        """Block until the part-present bit goes HIGH, then return part data.

        Parameters
        ----------
        timeout_s : Maximum wait time in seconds. 0 = wait forever.

        Returns
        -------
        Part data dict on trigger, or None on timeout / error.
        """
        if not self._connected:
            logger.error("Cannot wait for part — not connected.")
            return None

        start = time.monotonic()
        logger.info("Waiting for part-present signal on %s …", self.part_bit_device)

        while True:
            bit = self._read_bit(self.part_bit_device)

            if bit == 1:
                logger.info("Part-present HIGH — reading part data.")
                part_data = self.read_part_data()
                self.reset_trigger()   # acknowledge immediately
                return part_data

            if bit < 0:
                # Communication error — brief pause before retry
                time.sleep(1.0)

            if timeout_s > 0 and (time.monotonic() - start) >= timeout_s:
                logger.info("wait_for_part timed out after %.1f s.", timeout_s)
                return None

            time.sleep(self.poll_interval_s)
