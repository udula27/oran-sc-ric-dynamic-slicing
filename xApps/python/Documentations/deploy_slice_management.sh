#!/bin/bash

# Intelligent Slice Management xApp Deployment Script
# Deploys KMP monitoring and RAN control xApps for dynamic slice management

set -e

echo "=== ORAN-SC RIC Intelligent Slice Management Deployment ==="

# Configuration
E2_NODE_ID="gnbd_001_001_00019b_0"
RIC_BASE_DIR="/home/manamperige/Documents/srsRAN_Project/oran-sc-ric"
XAPP_DIR="$RIC_BASE_DIR/xApps/python"

# Check if RIC is running
echo "Checking RIC status..."
if ! docker ps | grep -q ric_e2term; then
    echo "Error: RIC E2 termination container not running. Please start RIC first."
    exit 1
fi

# Make xApps executable
chmod +x $XAPP_DIR/intelligent_slice_mgmt_xapp.py
chmod +x $XAPP_DIR/kpm_mon_xapp.py
chmod +x $XAPP_DIR/simple_rc_xapp.py

# Create log directory
mkdir -p $RIC_BASE_DIR/logs

echo "=== Deploying Intelligent Slice Management System ==="

# Function to start xApp in background
start_xapp() {
    local xapp_name=$1
    local xapp_file=$2
    local port=$3
    local rmr_port=$4
    local extra_args=$5
    
    echo "Starting $xapp_name..."
    cd $XAPP_DIR
    
    python3 $xapp_file \
        --e2_node_id $E2_NODE_ID \
        --http_server_port $port \
        --rmr_port $rmr_port \
        $extra_args \
        > $RIC_BASE_DIR/logs/${xapp_name}.log 2>&1 &
    
    local pid=$!
    echo "$xapp_name started with PID: $pid"
    echo $pid > $RIC_BASE_DIR/logs/${xapp_name}.pid
}

# Deployment options
case "${1:-all}" in
    "intelligent")
        echo "Deploying Intelligent Slice Management xApp..."
        start_xapp "intelligent_slice_mgmt" "intelligent_slice_mgmt_xapp.py" 8094 4564 \
            "--kmp_ran_func_id 2 --rc_ran_func_id 3 --kmp_report_style 1 --metrics DRB.UEThpDl,DRB.UEThpUl,RRU.PrbUsedDl,RRU.PrbUsedUl,DRB.PacketDelayDl,RRC.ConnEstabAtt,RRC.ConnEstabSucc,DRB.PacketLossRateDl"
        ;;
        
    "kmp_only")
        echo "Deploying KMP Monitoring xApp only..."
        start_xapp "kmp_monitor" "kpm_mon_xapp.py" 8092 4562 \
            "--ran_func_id 2 --kmp_report_style 1 --metrics DRB.UEThpDl,DRB.UEThpUl,RRU.PrbUsedDl,RRU.PrbUsedUl"
        ;;
        
    "rc_only")
        echo "Deploying RAN Control xApp only..."
        start_xapp "ran_control" "simple_rc_xapp.py" 8090 4560 \
            "--ran_func_id 3"
        ;;
        
    "separate")
        echo "Deploying separate KMP monitoring and RAN control xApps..."
        start_xapp "kmp_monitor" "kpm_mon_xapp.py" 8092 4562 \
            "--ran_func_id 2 --kmp_report_style 1 --metrics DRB.UEThpDl,DRB.UEThpUl,RRU.PrbUsedDl,RRU.PrbUsedUl,DRB.PacketDelayDl,RRC.ConnEstabAtt,RRC.ConnEstabSucc"
        
        sleep 2
        
        start_xapp "ran_control" "simple_rc_xapp.py" 8090 4560 \
            "--ran_func_id 3"
        ;;
        
    "all"|*)
        echo "Deploying complete intelligent slice management system..."
        start_xapp "intelligent_slice_mgmt" "intelligent_slice_mgmt_xapp.py" 8094 4564 \
            "--kmp_ran_func_id 2 --rc_ran_func_id 3 --kmp_report_style 1 --metrics DRB.UEThpDl,DRB.UEThpUl,RRU.PrbUsedDl,RRU.PrbUsedUl,DRB.PacketDelayDl,RRC.ConnEstabAtt,RRC.ConnEstabSucc,DRB.PacketLossRateDl,DRB.PacketLossRateUl"
        ;;
esac

echo ""
echo "=== Deployment Complete ==="
echo "Monitor logs with: tail -f $RIC_BASE_DIR/logs/*.log"
echo "Stop xApps with: $0 stop"
echo ""

# Show running processes
echo "Active xApp processes:"
ps aux | grep -E "(intelligent_slice|kmp_mon|simple_rc)" | grep -v grep || echo "No xApps currently running"

# Function to stop all xApps
if [ "$1" == "stop" ]; then
    echo "Stopping all slice management xApps..."
    
    for pidfile in $RIC_BASE_DIR/logs/*.pid; do
        if [ -f "$pidfile" ]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                echo "Stopping PID: $pid"
                kill "$pid"
                rm "$pidfile"
            fi
        fi
    done
    
    # Cleanup any remaining processes
    pkill -f "intelligent_slice_mgmt_xapp.py" || true
    pkill -f "kpm_mon_xapp.py" || true
    pkill -f "simple_rc_xapp.py" || true
    
    echo "All xApps stopped."
fi