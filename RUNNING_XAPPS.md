# Running xApps with Docker

## Quick Start

The Static Slice PRB Allocation xApp runs inside the `python_xapp_runner` Docker container where RMR libraries are available.

### Method 1: Simple docker exec (Recommended)

```bash
cd /home/fyp5g/Documents/Patches/srsPATCH_3/srsRAN_Project/oran-sc-ric

# Copy files to container
docker cp xApps/python/static_slice_prb_xapp.py python_xapp_runner:/root/
docker cp xApps/python/lib python_xapp_runner:/root/

# Run the xApp
docker exec -it python_xapp_runner python3 -u /root/static_slice_prb_xapp.py \
    --e2_node_id gnbd_001_001_00019b_0 \
    --plmn 00101 \
    --http_port 8090
```

### Method 2: Using the wrapper script

```bash
cd /home/fyp5g/Documents/Patches/srsPATCH_3/srsRAN_Project/oran-sc-ric

# Basic usage (uses default PLMN 00101, port 8090)
./run_static_slice_xapp.sh gnbd_001_001_00019b_0

# With custom parameters
./run_static_slice_xapp.sh gnbd_001_001_00019b_0 00101 8090
```

## Prerequisites

1. **RIC Platform running:**
   ```bash
   cd /home/fyp5g/Documents/Patches/srsPATCH_3/srsRAN_Project/oran-sc-ric
   docker compose up -d
   ```

2. **gNodeB connected to RIC** with E2 enabled

3. **Get E2 Node ID:**
   ```bash
   docker compose logs rtmgr_sim | grep ranNamelist
   ```
   Look for: `gnbd_001_001_00019b_0`

## Running Multiple xApps

Use different HTTP ports for each xApp:

```bash
# First xApp (port 8090)
docker exec python_xapp_runner python3 -u /root/kpm_mon_xapp.py \
    --metrics=DRB.UEThpDl \
    --http_port 8090

# Static Slice xApp (port 8091)
docker exec python_xapp_runner python3 -u /root/static_slice_prb_xapp.py \
    --e2_node_id gnbd_001_001_00019b_0 \
    --http_port 8091
```

## What Changed

The xApp has been updated to:
1. **Use config dict approach** (like other working xApps)
2. **Import ricxappframe only inside Docker** where RMR libs exist
3. **No host dependencies** - doesn't require librmr_si.so on host system

## Troubleshooting

### Container Not Found
```bash
# Check if RIC platform is running
docker compose ps

# Start the platform
cd /home/fyp5g/Documents/Patches/srsPATCH_3/srsRAN_Project/oran-sc-ric
docker compose up -d
```

### xApp Import Errors
The `python_xapp_runner` container should already have all dependencies. If you see import errors:
```bash
# Restart the container
docker compose restart python_xapp_runner
```

### E2 Connection Issues
- Verify gNB is running with E2 enabled
- Check E2 Node ID matches exactly (case-sensitive): 
  ```bash
  docker compose logs rtmgr_sim | grep ranNamelist
  ```
- Ensure RIC platform is running and reachable

### Port Already in Use
If you get "Address already in use", use a different HTTP port:
```bash
docker exec python_xapp_runner python3 -u /root/static_slice_prb_xapp.py \
    --e2_node_id gnbd_001_001_00019b_0 \
    --http_port 8091  # Changed from 8090
```

## Troubleshooting

### Container Not Found
```bash
# List all containers (running and stopped)
docker ps -a

# Start a stopped container
docker start <container_name>
```

### xApp Import Errors
Make sure the container has:
- Python 3.8+
- ricxappframe installed
- RMR libraries (librmr_si.so)

### E2 Connection Issues
- Verify gNB is running with E2 enabled
- Check E2 Node ID matches exactly (case-sensitive)
- Ensure RIC platform is running and reachable
