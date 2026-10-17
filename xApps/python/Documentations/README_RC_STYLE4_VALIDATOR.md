# E2SM-RC Report Style 4 Validator xApp

## Overview

This xApp validates the E2SM-RC Report Style 4 (UE Information) implementation in srsRAN by subscribing to UE context events and displaying UE ID and S-NSSAI (network slice) information in real-time.

**Purpose:** Validate that the RC Report Style 4 patch is working correctly by monitoring UE connections and their slice assignments.

## Features

- ✅ Subscribes to E2SM-RC Report Style 4 (UE Information)
- ✅ Receives RIC Indication messages when UEs connect/disconnect
- ✅ Decodes and displays:
  - UE ID (gNB-DU-UE-F1AP-ID)
  - S-NSSAI (SST and SD values)
  - Timestamp of each event
- ✅ Maintains a summary of all tracked UEs
- ✅ Color-coded output for easy visualization
- ✅ Hex dump for debugging ASN.1 encoding issues

## Prerequisites

### 1. Python Dependencies

```bash
# Install RIC xApp framework
pip3 install ricxappframe

# Or if using virtual environment
python3 -m venv venv
source venv/bin/activate
pip3 install ricxappframe
```

### 2. O-RAN Near-RT RIC

The xApp requires a running O-RAN Near-RT RIC with:
- Subscription Manager (default: `http://10.0.2.13:8088`)
- E2 Termination endpoint (default: RMR port 4560)

### 3. srsRAN gNB with E2 Agent

Ensure your srsRAN gNB is built with E2 support and the RC Report Style 4 patch applied.

Configuration (`gnb.yml`):
```yaml
e2:
  enable_du_e2: true
  addr: 10.0.2.10          # RIC IP address
  port: 36421
  bind_addr: 127.0.0.1
  sctp_rto_initial: 120
  sctp_rto_min: 120
  sctp_rto_max: 220
  sctp_init_max_attempts: 3
  sctp_max_init_timeo: 500
```

## Usage

### Basic Usage

```bash
cd /path/to/srsRAN_Project/oran-sc-ric/xApps/python
python3 rc_style4_validator_xapp.py --e2_node_id <E2_NODE_ID>
```

### With Custom Ports

```bash
python3 rc_style4_validator_xapp.py \
    --e2_node_id gnb_001_001_00019b \
    --http_port 8091 \
    --rmr_port 4561
```

### Command-Line Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--e2_node_id` | Yes | - | E2 Node ID (gNB identifier) |
| `--http_port` | No | 8090 | HTTP server port for subscription callbacks |
| `--rmr_port` | No | 4560 | RMR messaging port |

### Finding E2 Node ID

The E2 Node ID is typically in the format: `gnb_<MCC>_<MNC>_<gNB_ID>`

Example: `gnb_001_001_00019b`

To find it:
1. Start your gNB with E2 enabled
2. Check gNB logs for E2 Setup:
   ```bash
   grep "E2 Node ID" /var/log/srsran/gnb.log
   ```
3. Or check RIC logs for connected E2 nodes

## Expected Output

### Startup

```
================================================================================
E2SM-RC REPORT STYLE 4 (UE INFORMATION) VALIDATOR xApp
================================================================================
Target E2 Node: gnb_001_001_00019b
RAN Function:   E2SM-RC (ID=3)
Report Style:   Style 4 (UE Information)
================================================================================

📡 Sending RIC Subscription Request...
   RAN Function ID: 3
   Report Style:    4 (UE Information)
   Trigger Type:    Event-triggered

✅ Subscription request sent successfully!

🔍 Waiting for UE context information from gNB...
   (Connect a UE to the network to trigger RC Report Style 4 indications)

────────────────────────────────────────────────────────────────────────────────

ℹ️  xApp is now running. Press Ctrl+C to stop.
```

### When UE Connects

```
================================================================================
[2026-01-28 14:32:15.234] RC REPORT STYLE 4 - UE INFORMATION RECEIVED
================================================================================
  E2 Node ID:      gnb_001_001_00019b
  Subscription ID: 1234
  Message Type:    E2SM-RC Indication - Report Style 4 (UE Information)

  Indication Header (Format 1):
    Length: 12 bytes
    Raw (hex): 00 04 00 0c 00 00 05 00 1d ...

  Indication Message (Format 2 - UE Level Parameters):
    Length: 45 bytes
    ✅ Found 1 UE(s)

  UE #1:
    UE ID:    0
    S-NSSAI:
      SST:    1
      SD:     0x000001 (1)

================================================================================

📊 SUMMARY - All Tracked UEs:
────────────────────────────────────────────────────────────────────────────────
  gnb_001_001_00019b:0 → SST=1, SD=0x000001 (last seen: 2026-01-28 14:32:15.234)
────────────────────────────────────────────────────────────────────────────────
```

## Validation Test Procedure

### Step 1: Start the RIC
```bash
# Assuming Near-RT RIC is installed
cd /path/to/ric
./start_ric.sh
```

### Step 2: Start the gNB
```bash
cd /path/to/srsRAN_Project/build/apps/gnb
sudo ./gnb -c gnb_rf_b210_fdd.yml
```

Verify E2 connection:
```bash
grep "E2 Setup" /var/log/srsran/gnb.log
# Expected: E2 Setup Request sent, E2 Setup Response received
```

### Step 3: Start the Validator xApp
```bash
cd /path/to/srsRAN_Project/oran-sc-ric/xApps/python
python3 rc_style4_validator_xapp.py --e2_node_id gnb_001_001_00019b
```

### Step 4: Connect a UE
```bash
# Using srsUE
cd /path/to/srsUE/build
sudo ./srsue --rf.device_name=zmq \
             --rf.device_args="tx_port=tcp://*:2001,rx_port=tcp://localhost:2000"

# Or power on a physical UE
```

### Step 5: Verify Output

**Expected Behavior:**
1. ✅ xApp receives RIC Indication message
2. ✅ UE ID is displayed
3. ✅ S-NSSAI values (SST and SD) match the slice configuration in your 5G core
4. ✅ No errors or crashes

**Pass Criteria:**
- [ ] Subscription accepted (no errors)
- [ ] RIC Indication received when UE connects
- [ ] S-NSSAI values correctly decoded
- [ ] UE ID matches the expected UE

### Step 6: Verify Slice Assignment

Cross-check the displayed S-NSSAI with your 5G core network configuration:

**In Open5GS (`open5gs.yaml`):**
```yaml
amf:
  slice:
    - sst: 1
      sd: 0x000001
      default_indicator: true
```

The xApp output should match these values.

## Troubleshooting

### Issue 1: Subscription Fails
**Symptom:** `Error during subscription`

**Solutions:**
1. Check RIC is running: `netstat -tuln | grep 8088`
2. Verify Subscription Manager endpoint in `xAppBase.py`:
   ```python
   self.SUB_MGR_URI = "http://10.0.2.13:8088/ric/v1"
   ```
3. Check network connectivity: `ping 10.0.2.13`

### Issue 2: No RIC Indications Received
**Symptom:** xApp starts but no messages when UE connects

**Solutions:**
1. Verify E2 connection at gNB:
   ```bash
   grep "E2 Setup" /var/log/srsran/gnb.log
   ```
2. Check if Report Style 4 is advertised:
   ```bash
   grep "Report Style 4" /var/log/srsran/gnb.log
   ```
3. Verify UE actually connected:
   ```bash
   grep "UE Context Setup" /var/log/srsran/gnb.log
   ```
4. Check E2 notifications in gNB logs:
   ```bash
   grep "Notified E2 about UE context update" /var/log/srsran/gnb.log
   ```

### Issue 3: Cannot Parse S-NSSAI
**Symptom:** `No UE information extracted (parsing may need adjustment)`

**Solutions:**
1. Check the raw message hex dump in the output
2. The ASN.1 encoding may differ - adjust `parse_e2sm_rc_indication_format2()` method
3. Enable detailed logging at gNB:
   ```yaml
   log:
     all_level: debug
     e2ap_level: debug
   ```
4. Use Wireshark to capture E2AP messages: `tcpdump -i lo port 36421 -w e2ap.pcap`

### Issue 4: Wrong S-NSSAI Values
**Symptom:** S-NSSAI doesn't match core network config

**Solutions:**
1. Verify core network slice configuration
2. Check F1AP messages contain correct S-NSSAI:
   ```bash
   grep "s_nssai" /var/log/srsran/gnb.log
   ```
3. Verify DRB setup logs show correct slice:
   ```bash
   grep "DRB.*assigned to slice" /var/log/srsran/gnb.log
   ```

## Advanced Usage

### Debug Mode (Print All Raw Messages)

Modify the xApp to print detailed ASN.1 dumps:

```python
# In rc_style4_validator_xapp.py, around line 130
print("  RAW INDICATION MESSAGE (full):")
for i in range(0, len(indication_msg), 16):
    hex_str = ' '.join(f'{b:02x}' for b in indication_msg[i:i+16])
    print(f"  {i:04x}:  {hex_str}")
```

### Multiple E2 Nodes

Subscribe to multiple gNBs by running multiple xApp instances:

```bash
# Terminal 1: Monitor gNB 1
python3 rc_style4_validator_xapp.py --e2_node_id gnb_001_001_00019b --http_port 8090 --rmr_port 4560

# Terminal 2: Monitor gNB 2
python3 rc_style4_validator_xapp.py --e2_node_id gnb_001_001_00019c --http_port 8091 --rmr_port 4561
```

## Files Modified/Created

- **New:** `rc_style4_validator_xapp.py` - Main xApp script
- **New:** `README_RC_STYLE4_VALIDATOR.md` - This file

## Integration with Test Framework

To automate validation:

```bash
#!/bin/bash
# validate_rc_style4.sh

# Start xApp in background
python3 rc_style4_validator_xapp.py --e2_node_id gnb_001_001_00019b > xapp.log 2>&1 &
XAPP_PID=$!

# Wait for subscription
sleep 5

# Connect UE
# (Your UE connection script here)

# Wait for indication
sleep 10

# Check if S-NSSAI was received
if grep -q "S-NSSAI:" xapp.log; then
    echo "✅ RC Report Style 4 validation PASSED"
    exit 0
else
    echo "❌ RC Report Style 4 validation FAILED"
    exit 1
fi

# Cleanup
kill $XAPP_PID
```

## Related Documentation

- **RC Report Style 4 Patch:** `/RC_REPORT_STYLE4_PATCH.md`
- **Validation Guide:** `/RC_REPORT_STYLE4_VALIDATION.md`
- **xApp Development:** O-RAN SC documentation

## License

This xApp follows the same license as srsRAN (AGPL v3).

## Author

Created: January 28, 2026
Purpose: Validate E2SM-RC Report Style 4 implementation in srsRAN

---

**Note:** This is a validation tool, not a production xApp. For production use, implement proper ASN.1 decoding using `asn1tools` or the E2SM-RC SDK.
