#!/bin/bash
#
# RIC E2 Connectivity Diagnostic Script
# 
# This script checks if the RIC infrastructure is properly configured
# for E2AP message delivery to xApps
#

echo "================================================================================"
echo "  O-RAN Near-RT RIC E2 Diagnostics"
echo "================================================================================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_step() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $1"
    else
        echo -e "${RED}✗${NC} $1"
    fi
}

# Step 1: Check if RIC containers are running
echo "Step 1: Checking RIC containers..."
echo "-----------------------------------"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "e2mgr|e2term|rtmgr|submgr|python_xapp"
check_step "RIC core containers running"
echo ""

# Step 2: Check E2 Node registration
echo "Step 2: Checking E2 Node registration..."
echo "-----------------------------------------"
E2_NODE=$(docker logs rtmgr_sim 2>&1 | grep "ranNamelist" | tail -1)
if [ -n "$E2_NODE" ]; then
    echo "$E2_NODE"
    check_step "E2 Node registered in RIC"
else
    echo -e "${RED}No E2 Node found!${NC}"
    echo "Hint: Check if gNB E2 agent connected successfully"
fi
echo ""

# Step 3: Check E2Term connectivity
echo "Step 3: Checking E2Term (E2AP termination)..."
echo "----------------------------------------------"
docker logs e2term 2>&1 | tail -20 | grep -E "Listening|Connection|SCTP"
check_step "E2Term listening for SCTP connections"
echo ""

# Step 4: Check Subscription Manager
echo "Step 4: Checking Subscription Manager..."
echo "-----------------------------------------"
docker logs submgr 2>&1 | tail -20 | grep -E "Subscription|REST|Listening"
check_step "Subscription Manager active"
echo ""

# Step 5: Check RMR routing
echo "Step 5: Checking RMR message routing..."
echo "----------------------------------------"
docker exec rtmgr_sim cat /etc/route/rt.json 2>/dev/null | jq '.routing_table' 2>/dev/null | head -30
if [ $? -eq 0 ]; then
    check_step "RMR routing table configured"
else
    echo -e "${YELLOW}⚠${NC} RMR routing might not be configured"
fi
echo ""

# Step 6: Check xApp container
echo "Step 6: Checking python_xapp_runner container..."
echo "-------------------------------------------------"
docker exec python_xapp_runner ls -la /opt/xapp 2>/dev/null | head -10
check_step "xApp container accessible"
echo ""

# Step 7: Network connectivity gNB -> RIC
echo "Step 7: Checking gNB -> RIC connectivity..."
echo "--------------------------------------------"
echo "RIC E2Term SCTP port should be reachable from gNB"
docker port e2term 2>/dev/null | grep 36421 || echo "E2Term port 36421/SCTP"
check_step "E2Term SCTP port exposed"
echo ""

# Step 8: Recent E2 logs
echo "Step 8: Recent E2 message activity..."
echo "--------------------------------------"
echo "E2Term recent logs:"
docker logs e2term 2>&1 | tail -10
echo ""
echo "Subscription Manager recent logs:"
docker logs submgr 2>&1 | tail -10
echo ""

# Common problems
echo "================================================================================"
echo "  Common Issues & Solutions"
echo "================================================================================"
echo ""
echo "1. xApp receives nothing (503 or timeout):"
echo "   - Check if Subscription Manager forwards to xApp"
echo "   - Verify RMR routing table has xApp routes"
echo "   - Check xApp listens on correct RMR port (4560)"
echo ""
echo "2. gNB shows 'E2 TX' but xApp silent:"
echo "   - E2Term might not be forwarding to Subscription Manager"
echo "   - Check submgr logs for incoming RIC Indications"
echo "   - Verify RAN Function ID matches subscription request"
echo ""
echo "3. Subscription fails:"
echo "   - Wrong E2 Node ID in subscription request"
echo "   - RAN Function ID mismatch (check gNB RAN Function list)"
echo "   - Invalid Event Trigger or Action Definition format"
echo ""
echo "4. Decode errors in xApp:"
echo "   - E2SM-KPM version mismatch"
echo "   - Missing ASN.1 libraries (asn1tools, asn1c)"
echo "   - Payload is raw APER but xApp expects JSON"
echo ""
echo "================================================================================"
echo "  Next Steps"
echo "================================================================================"
echo ""
echo "Run the minimal xApp:"
echo "  docker exec -it python_xapp_runner python3 /opt/xapp/minimal_kpm_xapp.py \\"
echo "    --e2_node_id <YOUR_E2_NODE_ID>"
echo ""
echo "Monitor E2 traffic in real-time:"
echo "  docker logs -f e2term"
echo "  docker logs -f submgr"
echo ""
echo "================================================================================"
