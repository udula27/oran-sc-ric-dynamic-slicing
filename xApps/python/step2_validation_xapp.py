#!/usr/bin/env python3
"""
Step 2 Validation xApp - Simple E2SM-RC Control Action 2.6 Test

Purpose: Send slice-level PRB quota updates to trigger STEP2-VALIDATION logs in gNB
This is a minimal test xApp to validate E2 interface integration with Phase 1 infrastructure.

Expected gNB logs after running this xApp:
  [STEP2-VALIDATION] Updating slice-level PRB policy: SST=1 min=20% max=60% ded=0%
  [STEP2-VALIDATION] Updating slice-level PRB policy: SST=2 min=15% max=50% ded=0%
  [STEP2-VALIDATION] Updating slice-level PRB policy: SST=3 min=10% max=40% ded=0%
"""

import argparse
import signal
import time
from lib.xAppBase import xAppBase


class Step2ValidationXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(Step2ValidationXapp, self).__init__(config, http_server_port, rmr_port)
        self.control_ack_count = 0
        self.control_failure_count = 0

    @xAppBase.start_function
    def start(self, e2_node_id, test_slices):
        """
        Send E2SM-RC Control Action 2.6 messages for slice-level PRB quotas
        
        Args:
            e2_node_id: E2 Node identifier (e.g., 'gnbd_001_001_00019b_0')
            test_slices: List of (SST, min%, max%, ded%) tuples to test
        """
        print("\n" + "="*80)
        print("  Step 2 Validation xApp - E2SM-RC Control Action 2.6")
        print("="*80)
        print(f"E2 Node ID: {e2_node_id}")
        print(f"RAN Function ID: {self.e2sm_rc.ran_func_id}")
        print(f"Test Slices: {len(test_slices)}")
        print("="*80 + "\n")

        # Wait for RMR to be ready
        print("[INFO] Waiting 2 seconds for RMR initialization...")
        time.sleep(2)

        # Send control messages for each slice
        for idx, (sst, min_prb, max_prb, ded_prb) in enumerate(test_slices, 1):
            print(f"\n[CONTROL {idx}/{len(test_slices)}] Sending PRB quota update:")
            print(f"  Target Slice: SST={sst}")
            print(f"  Min PRB Ratio: {min_prb}%")
            print(f"  Max PRB Ratio: {max_prb}%")
            print(f"  Dedicated PRB Ratio: {ded_prb}%")
            
            try:
                # Send E2SM-RC Control Request (Style 2, Action 6)
                # Note: ue_id=0 is used as a wildcard/broadcast for slice-level control
                self.e2sm_rc.control_slice_level_prb_quota(
                    e2_node_id=e2_node_id,
                    ue_id=0,  # Broadcast to all UEs in slice
                    min_prb_ratio=min_prb,
                    max_prb_ratio=max_prb,
                    dedicated_prb_ratio=ded_prb,
                    ack_request=1,  # Request acknowledgment
                    sst=sst,  # Target slice SST
                    sd=0,     # SD=0 (default slice differentiation)
                    plmn_string="00101"  # PLMN identifier
                )
                print(f"  [✓] Control message sent successfully")
                
                # Wait between messages to avoid overwhelming RIC
                time.sleep(1)
                
            except Exception as e:
                print(f"  [✗] ERROR sending control message: {e}")
                continue

        print("\n" + "="*80)
        print("All control messages sent!")
        print("="*80)
        print("\n[VALIDATION] Check gNB log file for STEP2-VALIDATION messages:")
        print("  grep 'STEP2-VALIDATION' /path/to/gnb.log")
        print("\nExpected output:")
        for sst, min_prb, max_prb, ded_prb in test_slices:
            print(f"  [STEP2-VALIDATION] Updating slice-level PRB policy: SST={sst} min={min_prb}% max={max_prb}% ded={ded_prb}%")
        
        print("\n[INFO] xApp will now listen for RIC Control ACK/FAILURE responses...")
        print("[INFO] Press Ctrl+C to exit")
        print("="*80 + "\n")

        # Keep xApp running to receive responses
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[INFO] Shutting down xApp...")

    def handle_control_ack(self, payload):
        """Handle RIC Control Acknowledgment"""
        self.control_ack_count += 1
        print(f"\n[✓] RIC CONTROL ACK RECEIVED (total: {self.control_ack_count})")
        print("    gNB successfully processed the control request")

    def handle_control_failure(self, payload):
        """Handle RIC Control Failure"""
        self.control_failure_count += 1
        print(f"\n[✗] RIC CONTROL FAILURE RECEIVED (total: {self.control_failure_count})")
        print("    gNB rejected the control request")

    def signal_handler(self, sig, frame):
        """Handle graceful shutdown"""
        print("\n\n" + "="*80)
        print("  Step 2 Validation Summary")
        print("="*80)
        print(f"Control ACK received: {self.control_ack_count}")
        print(f"Control FAILURE received: {self.control_failure_count}")
        print("="*80 + "\n")
        super().signal_handler(sig, frame)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Step 2 Validation xApp - Test E2SM-RC Control Action 2.6',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test default slices (SST 1, 2, 3)
  python3 step2_validation_xapp.py --e2_node_id gnbd_001_001_00019b_0
  
  # Custom single slice test
  python3 step2_validation_xapp.py --e2_node_id gnbd_001_001_00019b_0 --slices "1:20:60:0"
  
  # Multiple custom slices
  python3 step2_validation_xapp.py --slices "1:20:60:0,2:15:50:0,3:10:40:0"
        """
    )
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8095, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4565, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', 
                        help="E2 Node ID (check gNB logs for actual ID)")
    parser.add_argument("--ran_func_id", type=int, default=3, 
                        help="E2SM-RC RAN function ID")
    parser.add_argument("--slices", type=str, default="1:20:60:0,2:15:50:0,3:10:40:0",
                        help="Slice configurations as 'SST:MIN:MAX:DED' separated by commas")

    args = parser.parse_args()

    # Parse slice configurations
    test_slices = []
    try:
        for slice_config in args.slices.split(','):
            parts = slice_config.strip().split(':')
            if len(parts) != 4:
                raise ValueError(f"Invalid slice config: {slice_config} (expected SST:MIN:MAX:DED)")
            sst = int(parts[0])
            min_prb = int(parts[1])
            max_prb = int(parts[2])
            ded_prb = int(parts[3])
            
            # Validation
            if not (1 <= sst <= 255):
                raise ValueError(f"SST must be 1-255, got {sst}")
            if not (0 <= min_prb <= 100):
                raise ValueError(f"Min PRB ratio must be 0-100, got {min_prb}")
            if not (0 <= max_prb <= 100):
                raise ValueError(f"Max PRB ratio must be 0-100, got {max_prb}")
            if not (0 <= ded_prb <= 100):
                raise ValueError(f"Dedicated PRB ratio must be 0-100, got {ded_prb}")
            if min_prb > max_prb:
                raise ValueError(f"Min PRB ({min_prb}) cannot exceed Max PRB ({max_prb})")
            
            test_slices.append((sst, min_prb, max_prb, ded_prb))
    except ValueError as e:
        print(f"\n[ERROR] Invalid slice configuration: {e}\n")
        parser.print_help()
        exit(1)

    print("\n" + "="*80)
    print("  Initializing Step 2 Validation xApp")
    print("="*80)
    print(f"Configuration:")
    print(f"  HTTP Server Port: {args.http_server_port}")
    print(f"  RMR Port: {args.rmr_port}")
    print(f"  E2 Node ID: {args.e2_node_id}")
    print(f"  RAN Function ID: {args.ran_func_id}")
    print(f"  Test Slices:")
    for sst, min_prb, max_prb, ded_prb in test_slices:
        print(f"    - SST={sst}: min={min_prb}%, max={max_prb}%, ded={ded_prb}%")
    print("="*80 + "\n")

    # Create xApp instance
    myXapp = Step2ValidationXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(args.ran_func_id)

    # Register signal handlers
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp
    myXapp.start(args.e2_node_id, test_slices)
