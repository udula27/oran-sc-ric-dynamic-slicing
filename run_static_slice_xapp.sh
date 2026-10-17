#!/bin/bash
################################################################################
# Static Slice PRB Allocation xApp - Simple Docker Exec Wrapper
################################################################################
# 
# Quick launcher that runs the xApp via docker exec
# Run this from the /oran-sc-ric directory
#
# Usage:
#   ./run_static_slice_xapp.sh [e2_node_id] [plmn] [http_port]
#   ./run_static_slice_xapp.sh gnbd_001_001_00019b_0
#   ./run_static_slice_xapp.sh gnbd_001_001_00019b_0 00101 8090
#
################################################################################

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Default values
CONTAINER="python_xapp_runner"
E2_NODE_ID="${1:-gnbd_001_001_00019b_0}"
PLMN="${2:-00101}"
HTTP_PORT="${3:-8090}"
RMR_PORT="${4:-4560}"
RAN_FUNC_ID="${5:-3}"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo -e "${RED}[ERROR]${NC} Container '$CONTAINER' is not running"
    echo ""
    echo "Start the RIC platform first:"
    echo "  cd /home/fyp5g/Documents/Patches/srsPATCH_3/srsRAN_Project/oran-sc-ric"
    echo "  docker compose up -d"
    echo ""
    echo "Available containers:"
    docker ps --format "  - {{.Names}}"
    exit 1
fi

echo -e "${GREEN}[INFO]${NC} Using container: $CONTAINER"
echo -e "${GREEN}[INFO]${NC} E2 Node ID: $E2_NODE_ID"
echo -e "${GREEN}[INFO]${NC} PLMN: $PLMN"
echo -e "${GREEN}[INFO]${NC} HTTP Port: $HTTP_PORT"
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy xApps directory to container
echo -e "${GREEN}[INFO]${NC} Copying xApp files to container..."
docker cp "$SCRIPT_DIR/xApps/python/static_slice_prb_xapp.py" "$CONTAINER:/root/"
docker cp "$SCRIPT_DIR/xApps/python/lib" "$CONTAINER:/root/"

# Run the xApp
echo -e "${GREEN}[INFO]${NC} Starting Static Slice PRB Allocation xApp..."
echo ""

docker exec -it "$CONTAINER" python3 -u /root/static_slice_prb_xapp.py \
    --e2_node_id "$E2_NODE_ID" \
    --plmn "$PLMN" \
    --rmr_port "$RMR_PORT" \
    --http_port "$HTTP_PORT" \
    --ran_func_id "$RAN_FUNC_ID" \
    --ue_id 0

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}[SUCCESS]${NC} xApp completed successfully"
else
    echo ""
    echo -e "${RED}[ERROR]${NC} xApp exited with code $EXIT_CODE"
fi

exit $EXIT_CODE
