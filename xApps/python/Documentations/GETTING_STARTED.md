# Getting Started with xApps

## Prerequisites

1. **O-RAN Near-RT RIC running** (in the `oran-sc-ric` directory)
2. **srsRAN gNodeB with E2 agent** (compiled with GCC 11+ and E2 support enabled)
3. **UE connected** (for testing)

## Step-by-Step Instructions

### 1. Start the O-RAN RIC Platform

```bash
cd /home/fyp5g/Documents/srsPATCH/srsRAN_Project/oran-sc-ric
docker compose up -d
```

Wait for all containers to be ready (about 10-15 seconds).

### 2. Start the gNodeB

```bash
sudo /home/fyp5g/Documents/srsPATCH/srsRAN_Project/build/apps/gnb/gnb \
  -c /home/fyp5g/Documents/srsPATCH/srsRAN_Project/GnodeB210_Slicing.yml
```

**Verify E2 connection:**
Look for this message in the gNodeB output:
```
E2AP: Connection to Near-RT-RIC on 10.0.2.10:36421 completed
```

### 3. Verify E2 Node Registration

Check the RIC logs to see the registered node ID:

```bash
docker compose logs rtmgr_sim | grep ranNamelist
```

You should see something like:
```
ranNamelist":["gnbd_001_001_00019b_0"]
```

**Note the E2 Node ID:** `gnbd_001_001_00019b_0` (this is what you'll use for xApps)

### 4. Run xApps

All xApps run **inside** the `python_xapp_runner` container.

#### KPM Monitoring xApp (for metrics)

```bash
docker exec python_xapp_runner ./kpm_mon_xapp.py \
  --metrics=DRB.UEThpDl,DRB.UEThpUl \
  --kpm_report_style=5
```

#### RC Style 4 Validator xApp (for UE Information)

```bash
docker exec python_xapp_runner python3 -u ./rc_style4_validator_xapp.py \
  --e2_node_id gnbd_001_001_00019b_0 \
  --http_port 8090
```


```bash
/home/fyp5g/Documents/srsPATCH/srsRAN_Project/oran-sc-ric
docker compose restart python_xapp_runner
```

**Connect a UE** to see the UE context information (UE ID and S-NSSAI).

#### Running Multiple xApps

Use different HTTP ports for each xApp:

```bash
# First xApp (default port 8090)
docker exec python_xapp_runner ./kpm_mon_xapp.py \
  --metrics=DRB.UEThpDl \
  --kpm_report_style=5 \
  --http_port 8090

# Second xApp (port 8091)
docker exec python_xapp_runner ./rc_style4_validator_xapp.py \
  --e2_node_id gnbd_001_001_00019b_0 \
  --http_port 8091
```

## Troubleshooting

### Issue: "No E2 connection for ranName"

**Symptom:** xApp fails with error:
```
{"msg":"No E2 connection for ranName gnbd_001_001_00019b_0"}
```

**Solution:**
1. Check the actual E2 node ID from RIC logs (step 3 above)
2. Make sure the gNodeB is connected (`E2AP: Connection to Near-RT-RIC completed`)
3. Use the exact node ID from the RIC logs

### Issue: "Address already in use"

**Symptom:**
```
OSError: [Errno 98] Address already in use
```

**Solution:**
- Use a different `--http_port` (e.g., 8091, 8092, 9000)
- Or restart the container: `docker compose restart python_xapp_runner`

### Issue: libuhd.so library error

**Symptom:**
```
error while loading shared libraries: libuhd.so.4.8.0: cannot open shared object file
```

**Solution:**
Rebuild srsRAN with GCC 11:

```bash
# Set GCC 11 as default
sudo update-alternatives --config gcc  # Select gcc-11
sudo update-alternatives --config g++  # Select g++-11

# Rebuild
cd /home/fyp5g/Documents/srsPATCH/srsRAN_Project
rm -rf build
mkdir build
cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON
make -j$(nproc)
```

### Issue: Compilation error with GCC 10

**Symptom:**
```
error: 'std::is_signed_v<unsigned char>' is not a constant expression
```

**Solution:**
Use GCC 11 or newer (see above).

## E2 Node ID Format

The E2 node ID format is: `gnbd_<MCC>_<MNC>_<NCI_hex>_0`

For the default configuration:
- **PLMN:** `00101` → MCC=`001`, MNC=`001`
- **NCI:** `0x19B` (from gNodeB logs)
- **Result:** `gnbd_001_001_00019b_0`

## Checking gNodeB Logs

To find the E2 node ID from gNodeB logs:

```bash
grep -i "globalGNB-ID\|gNB-ID" /tmp/gnb.log
```

Look for the binary gNB-ID and convert to hex.

## Useful Commands

**Check RIC container status:**
```bash
docker compose ps
```

**View RIC logs:**
```bash
docker compose logs -f submgr    # Subscription manager
docker compose logs -f e2mgr     # E2 manager
docker compose logs -f e2term    # E2 termination
```

**Stop RIC:**
```bash
docker compose down
```

**Restart specific container:**
```bash
docker compose restart python_xapp_runner
```
