#!/bin/bash
################################################################################
# Static Slice PRB Allocation xApp - Run Script
################################################################################
# 
# Quick launcher for the Static Slice PRB Allocation xApp
#
# Usage:
#   ./run_static_slice_xapp.sh
#   ./run_static_slice_xapp.sh --e2-node-id gnbd_001_001_00019b_0
#   ./run_static_slice_xapp.sh --e2-node-id gnbd_001_001_00019b_0 --plmn 00101
#
################################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default parameters
E2_NODE_ID="${E2_NODE_ID:-gnbd_001_001_00019b_0}"
PLMN="${PLMN:-00101}"
RMR_PORT="${RMR_PORT:-4560}"
HTTP_PORT="${HTTP_PORT:-8090}"
RAN_FUNC_ID="${RAN_FUNC_ID:-3}"
UE_ID="${UE_ID:-0}"

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XAPP_FILE="$SCRIPT_DIR/static_slice_prb_xapp.py"

################################################################################
# Functions
################################################################################

print_header() {
    echo -e "${BLUE}"
    echo "================================================================================"
    echo "  Static Slice PRB Allocation xApp - Launcher"
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

check_dependencies() {
    print_info "Checking dependencies..."
    
    # Check if docker is available
    if ! command -v docker &> /dev/null; then
        print_error "docker not found. Please install Docker."
        exit 1
    fi
    print_info "Docker is available"
    
    # Check if xApp container is running
    XAPP_CONTAINER=$(docker ps --format '{{.Names}}' | grep -E 'xapp|ric' | head -n1)
    if [ -z "$XAPP_CONTAINER" ]; then
        print_warn "No xApp or RIC container found running"
        print_warn "Looking for any container with Python and RMR support..."
        
        # Try to find a suitable container
        XAPP_CONTAINER=$(docker ps -a --format '{{.Names}}' | grep -E 'xapp|ric|e2term' | head -n1)
        if [ -z "$XAPP_CONTAINER" ]; then
            print_error "No suitable container found"
            print_error "Please ensure RIC platform containers are running"
            exit 1
        fi
    fi
    print_info "Using container: $XAPP_CONTAINER"
    
    # Check xApp file exists
    if [ ! -f "$XAPP_FILE" ]; then
        print_error "xApp file not found: $XAPP_FILE"
        exit 1
    fi
    print_info "xApp file found: $(basename $XAPP_FILE)"
    
    # Check lib directory
    if [ ! -d "$SCRIPT_DIR/lib" ]; then
        print_error "lib directory not found: $SCRIPT_DIR/lib"
        exit 1
    fi
    print_info "xApp library found"
    
    echo ""
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --e2-node-id|--e2_node_id)
                E2_NODE_ID="$2"
                shift 2
                ;;
            --plmn)
                PLMN="$2"
                shift 2
                ;;
            --rmr-port|--rmr_port)
                RMR_PORT="$2"
                shift 2
                ;;
            --http-port|--http_port)
                HTTP_PORT="$2"
                shift 2
                ;;
            --ran-func-id|--ran_func_id)
                RAN_FUNC_ID="$2"
                shift 2
                ;;
            --ue-id|--ue_id)
                UE_ID="$2"
                shift 2
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                print_usage
                exit 1
                ;;
        esac
    done
}

print_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --e2-node-id <id>     E2 Node ID (default: $E2_NODE_ID)"
    echo "  --plmn <plmn>         PLMN identity (default: $PLMN)"
    echo "  --rmr-port <port>     RMR port (default: $RMR_PORT)"
    echo "  --http-port <port>    HTTP port (default: $HTTP_PORT)"
    echo "  --ran-func-id <id>    RAN Function ID (default: $RAN_FUNC_ID)"
    echo "  --ue-id <id>          UE ID dummy value (default: $UE_ID)"
    echo "  -h, --help            Show this help message"
    echo ""
    echo "Environment variables:"
    echo "  E2_NODE_ID            Same as --e2-node-id"
    echo "  PLMN                  Same as --plmn"
    echo "  RMR_PORT              Same as --rmr-port"
    echo "  HTTP_PORT             Same as --http-port"
    echo ""
    echo "Note: This xApp runs inside a Docker container where RMR libraries are available."
    echo "      The script will automatically detect and use a running RIC/xApp container."
    echo ""
    echo "Examples:"
    echo "  $0 --e2-node-id gnbd_001_001_00019b_0"
    echo "  $0 --e2-node-id gnbd_001_001_00019b_0 --plmn 00101"
    echo "  E2_NODE_ID=gnbd_001_001_00019b_0 $0"
}

print_config() {
    echo -e "${BLUE}Configuration:${NC}"
    echo "  E2 Node ID       : $E2_NODE_ID"
    echo "  PLMN             : $PLMN"
    echo "  RMR Port         : $RMR_PORT"
    echo "  HTTP Port        : $HTTP_PORT"
    echo "  RAN Function ID  : $RAN_FUNC_ID"
    echo "  UE ID (dummy)    : $UE_ID"
    echo ""
}

print_slice_config() {
    echo -e "${BLUE}Slice Configurations (to be applied):${NC}"
    echo "  ┌─────┬─────────┬──────────┬──────────┬───────────┬───────────┐"
    echo "  │ SST │ Type    │ Min PRB% │ Max PRB% │ Min PRBs  │ Max PRBs  │"
    echo "  ├─────┼─────────┼──────────┼──────────┼───────────┼───────────┤"
    echo "  │  1  │ eMBB    │   40%    │   80%    │    65     │   130     │"
    echo "  │  2  │ URLLC   │   10%    │   40%    │    16     │    65     │"
    echo "  │  3  │ mMTC    │   10%    │   30%    │    16     │    49     │"
    echo "  └─────┴─────────┴──────────┴──────────┴───────────┴───────────┘"
    echo "  (Based on 162 total PRBs: 60 MHz, 30 kHz SCS)"
    echo ""
}

check_ric_connectivity() {
    print_info "Checking RIC connectivity..."
    
    # Check if container is running
    if ! docker ps | grep -q "$XAPP_CONTAINER"; then
        print_error "Container $XAPP_CONTAINER is not running"
        exit 1
    fi
    
    print_info "Container $XAPP_CONTAINER is running"
    print_warn "RIC E2Term connectivity will be verified during xApp startup"
    echo ""
}

run_xapp() {
    print_info "Starting Static Slice PRB Allocation xApp in container..."
    echo ""
    
    # Get absolute path to oran-sc-ric directory
    ORAN_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
    
    print_info "Copying xApp files to container..."
    
    # Copy xApp directory to container
    docker cp "$SCRIPT_DIR" "$XAPP_CONTAINER:/tmp/xApps_temp"
    
    # Run the xApp inside the container
    print_info "Executing xApp in container: $XAPP_CONTAINER"
    docker exec -it "$XAPP_CONTAINER" bash -c "
        cd /tmp/xApps_temp/python && \
        python3 static_slice_prb_xapp.py \
            --e2_node_id '$E2_NODE_ID' \
            --plmn '$PLMN' \
            --rmr_port $RMR_PORT \
            --http_port $HTTP_PORT \
            --ran_func_id $RAN_FUNC_ID \
            --ue_id $UE_ID
    "
    
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 0 ]; then
        print_info "xApp exited successfully"
    else
        print_error "xApp exited with code $EXIT_CODE"
    fi
    
    return $EXIT_CODE
}

################################################################################
# Main
################################################################################

main() {
    print_header
    
    # Parse command line arguments
    parse_args "$@"
    
    # Check dependencies
    check_dependencies
    
    # Print configuration
    print_config
    print_slice_config
    
    # Check RIC connectivity (basic)
    check_ric_connectivity
    
    # Run xApp
    run_xapp
    
    EXIT_CODE=$?
    exit $EXIT_CODE
}

# Run main function
main "$@"
