# PLC Setup Guide — Mitsubishi iQ-R + GX Works3

## Overview

AutoVoice connects to the Mitsubishi iQ-R PLC using the SLMP (Seamless Message Protocol) 3E Binary frame over TCP/IP. The PC polls the PLC every 100 ms for a part-present trigger bit, then reads the engine and model data from word registers.

---

## Step 1 — Set the PLC IP address in GX Works3

1. Open your GX Works3 project
2. Go to **Navigation → Parameter → Module Parameter**
3. Select your **Ethernet module** (built-in port on iQ-R)
4. Under **Ethernet Configuration**, set:
   - **IP Address**: `192.168.3.39` (or your preferred static IP)
   - **Subnet mask**: `255.255.255.0`
   - **Default gateway**: your router IP (or leave blank for isolated network)
5. Write parameters to PLC

---

## Step 2 — Enable SLMP (MC Protocol) connection

1. In **Module Parameter → Ethernet Configuration**, click **Detailed Settings**
2. Add a new connection with these settings:

| Setting | Value |
|---------|-------|
| Protocol | TCP |
| Fixed Buffer | Not used (set any) |
| Port No. | `3000` |
| Frame | 3E |
| Communication Data Code | Binary |
| Enable/Disable | Enable |

3. Write parameters to PLC and reset

---

## Step 3 — Allocate device memory

In your GX Works3 global label or device comment file, assign:

| Device | Type | Description |
|--------|------|-------------|
| M0 | Bit | PART_PRESENT — set HIGH when part reaches conveyor sensor |
| D100 | Word (INT) | ENGINE_NUMBER — engine serial number (e.g. 12345) |
| D101 | Word (INT) | MODEL_CODE — integer code for vehicle model |

You can use different device addresses — just update `autovoice/config.py` to match.

---

## Step 4 — Write the ladder logic

The ladder program should execute when the photoelectric sensor detects a part. A simple implementation in Structured Text (ST):

```
(* AutoVoice trigger sequence *)
(* Run this when the photoelectric sensor input goes HIGH *)

IF PhotoSensor = TRUE AND M0 = FALSE THEN
    (* Write engine data BEFORE setting trigger bit *)
    D100 := EngineSerialNumber;   (* e.g. 12345 *)
    D101 := ModelCode;             (* e.g. 1 = Pulsar 125 *)
    M0   := TRUE;                  (* signal AutoVoice *)
END_IF;
```

In Ladder Diagram (LD):
```
|--[PhotoSensor]--[/M0]--|--( MOV EngineSerial → D100 )--|
                          |--( MOV ModelCode   → D101 )--|
                          |--( SET M0                  )--|
```

**Important**: Write D100 and D101 BEFORE setting M0. AutoVoice reads D100/D101 immediately when it detects M0 HIGH.

AutoVoice resets M0 to 0 within ~100 ms of detection. Your ladder can monitor M0 returning to 0 as confirmation that the PC acknowledged the trigger.

---

## Step 5 — Model code map

Update `autovoice/modules/plc_client.py` → `MODEL_CODE_MAP` to match whatever integer codes your team has defined for D101:

```python
MODEL_CODE_MAP = {
    0: "Unknown",
    1: "Pulsar 125",
    2: "Pulsar N160",
    3: "Pulsar NS200",
    4: "Dominar 400",
    5: "Avenger 220",
    # Add more as needed
}
```

---

## Step 6 — Verify connectivity

Test the connection before running AutoVoice:

```bash
# Ping the PLC
ping 192.168.3.39

# Check port is open
# Windows:
Test-NetConnection -ComputerName 192.168.3.39 -Port 3000
# Linux:
nc -zv 192.168.3.39 3000
```

Then test AutoVoice can read the PLC:

```python
from autovoice.modules.plc_client import PLCClient

plc = PLCClient(host="192.168.3.39", port=3000)
if plc.connect():
    print("Connected!")
    print("M0 state:", plc.check_part_present())
    plc.disconnect()
else:
    print("Connection failed — check IP, port, and SLMP settings.")
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Cannot connect | Wrong IP / port | Verify IP in GX Works3 Module Parameter |
| Cannot connect | SLMP not enabled | Check Ethernet Configuration, add 3E connection |
| Cannot connect | Firewall | Disable Windows Firewall or add port 3000 exception |
| M0 never goes HIGH | Sensor not triggering ladder | Check sensor wiring and ladder condition |
| M0 goes HIGH but D100/D101 are 0 | Writing sequence wrong | Write D100/D101 before setting M0 |
| Double triggers | PLC sets M0 again before reset | Add interlock: only set M0 when it is currently 0 |
| Random disconnects | Network instability | Use a dedicated wired switch for PLC ↔ PC |

---

## Network topology (recommended for factory floor)

```
[Mitsubishi iQ-R PLC]
         |
    [Managed switch] ← dedicated to automation
         |
    [AutoVoice PC]   ← static IP, wired Ethernet
```

Use a dedicated switch (not shared with office WiFi) to eliminate packet loss and latency that causes SLMP timeouts.

Set the PC to a static IP on the same subnet as the PLC:
- PC IP: `192.168.3.100`
- Subnet: `255.255.255.0`
- PLC IP: `192.168.3.39`
