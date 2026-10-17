#!/bin/bash
################################################################################
# Step 2 Validation xApp - Docker Exec Launcher
################################################################################
# 
# Simple launcher to run the Step 2 validation xApp via docker exec
# This xApp sends E2SM-RC Control Action 2.6 messages to test gNB integration
#
# Usage:
#   ./run_step2_validation_xapp.sh
#   ./run_step2_validation_xapp.sh gnbd_001_001_00019b_0
#   ./run_step2_validation_xapp.sh gnbd_001_001_00019b_0 "1:20:60:0,2:15:50:0"
#
# Arguments:
#   $1 - E2 Node ID (default: gnbd_001_001_00019b_0)
#   $2 - Slice configs as "SST:MIN:MAX:DED,..." (default: 1:20:60:0,2:15:50:0,3:10:40:0)
#
################################################################################

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'  # No Color

# Configuration
CONTAINER="python_xapp_runner"
XAPP_NAME="step2_validation_xapp.py"
E2_NODE_ID="${1:-gnbd_001_001_00019b_0}"
SLICES="${2:-1:20:60:0,2:15:50:0,3:10:40:0}"
HTTP_PORT="${HTTP_PORT:-8095}"
RMR_PORT="${RMR_PORT:-4565}"
RAN_FUNC_ID="${RAN_FUNC_ID:-3}"

# Get script directory (where xApp files are located)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

################################################################################
# Functions
################################################################################

print_header() {
    echo -e "${BLUE}"
    echo "================================================================================"
    echo "  Step 2 Validation xApp - E2SM-RC Control Action 2.6 Test"
    echo "================================================================================"
    echo -e "${NC}"
}

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_container() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
        print_error "Container '${CONTAINER}' is not running"
        echo ""
        echo "Available containers:"
        docker ps --format "  - {{.Names}}" || echo "  (none)"
        echo ""
        echo "To start the RIC platform:"
        echo "  cd /path/to/oran-sc-ric"
        echo "  docker compose up -d"
        return 1
    fi
    return 0
}

copy_xapp_files() {
    print_info "Copying xApp files to container..."
    
    # Copy xApp script
    docker cp "${SCRIPT_DIR}/${XAPP_NAME}" "${CONTAINER}:/root/${XAPP_NAME}" 2>&1 | grep -v "tar:" || true
    
    # Copy lib directory
    docker cp "${SCRIPT_DIR}/lib" "${CONTAINER}:/root/" 2>&1 | grep -v "tar:" || true
    
    print_info "Files copied successfully"
}

run_xapp() {
    print_info "Starting Step 2 Validation xApp in container..."
    echo ""
    
    # Run xApp with docker exec
    docker exec -it "${CONTAINER}" python3 "/root/${XAPP_NAME}" \
        --e2_node_id "${E2_NODE_ID}" \
        --slices "${SLICES}" \
        --http_server_port "${HTTP_PORT}" \
        --rmr_port "${RMR_PORT}" \
        --ran_func_id "${RAN_FUNC_ID}"
}

################################################################################
# Main
################################################################################

print_header

echo "Configuration:"
echo "  Container:       ${CONTAINER}"
echo "  E2 Node ID:      ${E2_NODE_ID}"
echo "  Slice Configs:   ${SLICES}"
echo "  HTTP Port:       ${HTTP_PORT}"
echo "  RMR Port:        ${RMR_PORT}"
echo "  RAN Function ID: ${RAN_FUNC_ID}"
echo ""

# Check if container is running
if ! check_container; then
    exit 1
fi

print_info "Container '${CONTAINER}' is running"

# Copy xApp files to container
copy_xapp_files

# Run xApp
echo ""
run_xapp

# Note: The xApp will run interactively and can be stopped with Ctrl+C

echo ""
print_info "xApp execution completed"
echo ""
echo "To verify Step 2 validation, check gNB logs:"
echo -e "${YELLOW}  grep 'STEP2-VALIDATION' /path/to/gnb.log${NC}"
echo ""
echo "You should see logs like:"
echo "  [STEP2-VALIDATION] Updating slice-level PRB policy: SST=1 min=20% max=60% ded=0%"
echo "  [STEP2-VALIDATION] Updating slice-level PRB policy: SST=2 min=15% max=50% ded=0%"
echo "  [STEP2-VALIDATION] Updating slice-level PRB policy: SST=3 min=10% max=40% ded=0%"
echo ""
