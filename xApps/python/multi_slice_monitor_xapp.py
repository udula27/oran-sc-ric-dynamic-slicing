#!/usr/bin/env python3
"""
Multi-Slice Monitor xApp
========================
Subscribes to E2SM-KPM metrics for SST=1, SST=2, and SST=3 simultaneously.
Each slice gets a separate subscription and displays metrics independently.

Usage:
    python3 multi_slice_monitor_xapp.py --e2_node_id gnbd_001_001_00019b_0
"""

import os
import sys
import time
import argparse

# Import xApp framework
sys.path.append('/opt/ric-plt-xapp-frame-py')
from lib.xAppBase import xAppBase
from lib.e2sm_kpm_module import e2sm_types


class MultiSliceMonitorXApp(xAppBase):
    """xApp that monitors PRB usage for multiple slices independently"""
    
    def __init__(self, config):
        super().__init__(config)
        self.indication_counts = {1: 0, 2: 0, 3: 0}  # Per-slice counters
        
        print("\n" + "="*80)
        print("Multi-Slice Monitor xApp Initialized")
        print("="*80)
        print(f"[DEBUG] RMR Port: {config.get('rmr_port', 'unknown')}")
        print(f"[DEBUG] HTTP Port: {config.get('http_port', 'unknown')}")
        print(f"[DEBUG] RAN Function ID: {self.e2sm_kpm.ran_func_id}")
        print("="*80 + "\n")

    def subscription_callback_sst1(self, e2_agent_id, e2_subscription_id, indication_hdr, indication_msg, subscription_id):
        """Callback for SST=1 slice metrics"""
        self._handle_indication(e2_agent_id, e2_subscription_id, indication_hdr, indication_msg, 1)

    def subscription_callback_sst2(self, e2_agent_id, e2_subscription_id, indication_hdr, indication_msg, subscription_id):
        """Callback for SST=2 slice metrics"""
        self._handle_indication(e2_agent_id, e2_subscription_id, indication_hdr, indication_msg, 2)

    def subscription_callback_sst3(self, e2_agent_id, e2_subscription_id, indication_hdr, indication_msg, subscription_id):
        """Callback for SST=3 slice metrics"""
        self._handle_indication(e2_agent_id, e2_subscription_id, indication_hdr, indication_msg, 3)

    def _handle_indication(self, e2_agent_id, e2_subscription_id, indication_hdr, indication_msg, sst):
        """Common handler for all slice indications"""
        self.indication_counts[sst] += 1
        
        print(f"\n{'='*80}")
        print(f"[SST={sst}] RIC INDICATION #{self.indication_counts[sst]} RECEIVED!")
        print(f"{'='*80}")
        print(f"[DEBUG] e2_agent_id = {e2_agent_id}")
        print(f"[DEBUG] subscription_id = {e2_subscription_id}")
        
        try:
            # Use helper methods to decode (same as working phase2_slice_test_xapp.py)
            hdr_info = self.e2sm_kpm.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
            
            print(f"Timestamp: {hdr_info.get('colletStartTime', 'N/A')}")
            print(f"Granularity Period: {meas_data.get('granulPeriod', 'N/A')} ms\n")
            
            # Extract metric values
            if "measData" in meas_data:
                dl_prb = meas_data["measData"].get("RRU.PrbUsedDl", ["N/A"])[0]
                ul_prb = meas_data["measData"].get("RRU.PrbUsedUl", ["N/A"])[0]
                
                print(f"  SLICE SST={sst} METRICS:")
                print(f"    RRU.PrbUsedDl: {dl_prb} PRBs")
                print(f"    RRU.PrbUsedUl: {ul_prb} PRBs")
            else:
                print(f"  WARNING: No measData found for SST={sst}")
                print(f"  [DEBUG] meas_data keys: {meas_data.keys()}")
            
            print(f"{'='*80}\n")
            
        except Exception as e:
            print(f"[ERROR] Failed to decode SST={sst} indication: {e}")
            print(f"  Exception type: {type(e).__name__}")
            print("[DEBUG] Attempting fallback INTEGER extraction...")
            
            try:
                if isinstance(indication_msg, bytes):
                    integers = self._extract_integers_from_asn1(indication_msg)
                    print(f"[FALLBACK] Found {len(integers)} integers in payload: {integers}")
                    if len(integers) >= 2:
                        print(f"  SLICE SST={sst} METRICS (FALLBACK):")
                        print(f"    RRU.PrbUsedDl: {integers[0]} PRBs")
                        print(f"    RRU.PrbUsedUl: {integers[1]} PRBs")
                else:
                    print(f"[ERROR] indication_msg is not bytes: {type(indication_msg)}")
            except Exception as e2:
                print(f"[ERROR] Fallback extraction also failed: {e2}")
            
            print(f"{'='*80}\n")

    def _extract_integers_from_asn1(self, payload):
        """Fallback: Extract raw integers from ASN.1 payload"""
        integers_found = []
        i = 0
        while i < len(payload) - 1:
            if payload[i] == 0x02:  # ASN.1 INTEGER tag
                length = payload[i + 1]
                if length <= 8 and i + 2 + length <= len(payload):
                    val_bytes = payload[i + 2:i + 2 + length]
                    value = int.from_bytes(val_bytes, byteorder='big', signed=False)
                    integers_found.append(value)
                    i += 2 + length
                else:
                    i += 1
            else:
                i += 1
        return integers_found

    def _create_subscription(self, e2_node_id, sst, callback):
        """Create a subscription for a specific SST"""
        report_period = 1000  # 1 second
        granul_period = 1000
        
        metric_names = ['RRU.PrbUsedDl', 'RRU.PrbUsedUl']
        event_trigger_def = self.e2sm_kpm.e2sm_kpm_compiler.pack_event_trigger_def(report_period)
        
        # Pack action definition with SLICE_ID_LABEL for this SST
        measInfoList = []
        for metric_name in metric_names:
            metric_def = {
                'measType': ('measName', metric_name),
                'labelInfoList': [{
                    'measLabel': {
                        'sliceID': {
                            'sST': sst.to_bytes(1, byteorder='big'),  # SST as bytes
                            'sD': b'\x00\x00\x00'  # SD = 0
                        }
                    }
                }]
            }
            measInfoList.append(metric_def)
        
        action_def = {
            'ric-Style-Type': 1,
            'actionDefinition-formats': ('actionDefinition-Format1', {
                'measInfoList': measInfoList,
                'granulPeriod': granul_period
            })
        }
        action_def = self.e2sm_kpm.e2sm_kpm_compiler.asn1_compiler.encode('E2SM-KPM-ActionDefinition', action_def)
        
        print(f"[SST={sst}] Event Trigger: {len(event_trigger_def)} bytes, Action Def: {len(action_def)} bytes")
        
        self.subscribe(e2_node_id, self.e2sm_kpm.ran_func_id, event_trigger_def, action_def, callback, e2sm_types.E2SM_KPM)
        print(f"[SST={sst}] Subscription sent")

    @xAppBase.start_function
    def start(self, e2_node_id):
        print("\n" + "="*80)
        print("Starting Multi-Slice Monitor xApp")
        print("="*80)
        print(f"E2 Node ID: {e2_node_id}")
        print(f"Report Period: 1000 ms")
        print(f"Metrics: RRU.PrbUsedDl, RRU.PrbUsedUl")
        print(f"Slices: SST=1, SST=2, SST=3 (all with SD=0)")
        print("="*80 + "\n")
        
        # Create subscription for SST=1
        print("Subscribing to SST=1...")
        callback_sst1 = lambda agent, sub, hdr, msg: self.subscription_callback_sst1(agent, sub, hdr, msg, 'sst1')
        self._create_subscription(e2_node_id, 1, callback_sst1)
        time.sleep(0.5)
        
        # Create subscription for SST=2
        print("\nSubscribing to SST=2...")
        callback_sst2 = lambda agent, sub, hdr, msg: self.subscription_callback_sst2(agent, sub, hdr, msg, 'sst2')
        self._create_subscription(e2_node_id, 2, callback_sst2)
        time.sleep(0.5)
        
        # Create subscription for SST=3
        print("\nSubscribing to SST=3...")
        callback_sst3 = lambda agent, sub, hdr, msg: self.subscription_callback_sst3(agent, sub, hdr, msg, 'sst3')
        self._create_subscription(e2_node_id, 3, callback_sst3)
        time.sleep(0.5)
        
        print("\n" + "="*80)
        print("All 3 subscriptions active - monitoring all slices...")
        print("="*80)
        print("\nWatching for indications:")
        print("  SST=1 (SD=0) - DL/UL PRB usage")
        print("  SST=2 (SD=0) - DL/UL PRB usage") 
        print("  SST=3 (SD=0) - DL/UL PRB usage")
        print("\nMove your UE between slices to see metrics change")
        print("Press Ctrl+C to stop\n")
        print("="*80 + "\n")

    def print_summary(self):
        """Print test summary on exit"""
        print("\n\n" + "="*80)
        print("Multi-Slice Monitor Summary")
        print("="*80)
        print(f"  SST=1: {self.indication_counts[1]} indications received")
        print(f"  SST=2: {self.indication_counts[2]} indications received")
        print(f"  SST=3: {self.indication_counts[3]} indications received")
        print(f"  Total: {sum(self.indication_counts.values())} indications")
        print("="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Multi-Slice Monitor xApp - Monitor PRB usage for SST 1, 2, 3 simultaneously'
    )
    parser.add_argument("--e2_node_id", required=True, help="E2 Node ID (e.g., gnbd_001_001_00019b_0)")
    parser.add_argument("--rmr_port", type=int, default=4563, help="RMR port")
    parser.add_argument("--http_port", type=int, default=8080, help="HTTP port")
    
    args = parser.parse_args()
    
    # Configuration
    config = {
        'xapp_name': 'multi_slice_monitor_xapp',
        'rmr_port': args.rmr_port,
        'http_port': args.http_port
    }
    
    # Create and start xApp
    xapp = MultiSliceMonitorXApp(config)
    
    try:
        xapp.start(args.e2_node_id)
        
        # Keep running until interrupted
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\nCtrl+C detected - shutting down...")
        xapp.print_summary()
        sys.exit(0)


if __name__ == '__main__':
    main()
