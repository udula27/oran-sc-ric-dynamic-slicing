#!/usr/bin/env python3
"""
Phase 2 Test xApp - Slice Filtering Validator
Tests E2SM-KPM Report Style 1 with SLICE_ID_LABEL

This xApp subscribes to RRU.PrbUsedDl and RRU.PrbUsedUl with:
1. NO_LABEL (aggregate metrics)
2. SLICE_ID_LABEL (SST=1, SD=0) - slice-filtered metrics

Purpose: Validate Phase 2B and 2C implementation of slice filtering
"""

import argparse
import signal
import json
import time
from lib.xAppBase import xAppBase


class Phase2TestXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(Phase2TestXapp, self).__init__(config, http_server_port, rmr_port)
        self.metrics_received = {
            'no_label': {'dl': [], 'ul': []},
            'slice_filtered': {'dl': [], 'ul': []}
        }
        self.indication_count = 0
        
        # Debug: Print initialization info
        print(f"\n[XAPP DEBUG] Phase2TestXapp initialized")
        print(f"[XAPP DEBUG] HTTP Server Port: {http_server_port}")
        print(f"[XAPP DEBUG] RMR Port: {rmr_port}")
        print(f"[XAPP DEBUG] Parent class: {super().__class__.__name__}")

    def subscription_callback_no_label(self, e2_agent_id, subscription_id, indication_hdr, indication_msg, label_type):
        """Callback for NO_LABEL subscription (aggregate metrics)"""
        self.indication_count += 1
        
        print(f"\n{'='*40}")
        print(f"[NO_LABEL] RIC INDICATION #{self.indication_count} RECEIVED!")
        print(f"{'='*40}")
        print(f"[DEBUG] Callback triggered successfully")
        print(f"[DEBUG] e2_agent_id = {e2_agent_id}")
        print(f"[DEBUG] subscription_id = {subscription_id}")
        print(f"[DEBUG] indication_hdr type = {type(indication_hdr).__name__}")
        print(f"[DEBUG] indication_msg type = {type(indication_msg).__name__}")
        
        # Debug: Raw payload inspection
        if isinstance(indication_msg, bytes):
            print(f"[DEBUG] indication_msg is bytes, length = {len(indication_msg)}")
            print(f"[HEX DUMP] First 64 bytes of indication_msg:")
            hex_str = indication_msg[:64].hex()
            for i in range(0, len(hex_str), 32):
                print(f"  {hex_str[i:i+32]}")
        elif isinstance(indication_msg, str):
            print(f"[DEBUG] indication_msg is string, length = {len(indication_msg)}")
        else:
            print(f"[DEBUG] indication_msg is {type(indication_msg)}")
        
        print(f"\n{'='*80}")
        print(f"[NO_LABEL] RIC Indication from {e2_agent_id}, Subscription ID: {subscription_id}")
        print(f"{'='*80}")
        
        try:
            indication_hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
            
            print(f"Timestamp: {indication_hdr['colletStartTime']}")
            print(f"Granularity Period: {meas_data.get('granulPeriod', 'N/A')} ms")
            
            if "measData" in meas_data:
                dl_prb = meas_data["measData"].get("RRU.PrbUsedDl", ["N/A"])[0]
                ul_prb = meas_data["measData"].get("RRU.PrbUsedUl", ["N/A"])[0]
                
                self.metrics_received['no_label']['dl'].append(dl_prb)
                self.metrics_received['no_label']['ul'].append(ul_prb)
                
                print(f"\n  AGGREGATE METRICS (All UEs):")
                print(f"    RRU.PrbUsedDl: {dl_prb} PRBs")
                print(f"    RRU.PrbUsedUl: {ul_prb} PRBs")
            else:
                print("  WARNING: No measData found in indication")
                print(f"  [DEBUG] meas_data keys: {meas_data.keys()}" if isinstance(meas_data, dict) else f"  [DEBUG] meas_data type: {type(meas_data)}")
        
        except Exception as e:
            print(f"\n[ERROR] Failed to decode E2SM-KPM message:")
            print(f"  Exception: {type(e).__name__}: {e}")
            print(f"  Attempting simple INTEGER extraction as fallback...")
            
            # Fallback: Simple INTEGER decoder
            if isinstance(indication_msg, bytes):
                integers = self._extract_integers_from_asn1(indication_msg)
                if integers:
                    print(f"\n  Extracted {len(integers)} INTEGER values:")
                    for idx, val in enumerate(integers):
                        print(f"    [{idx}]: {val}")
                    print(f"\n  Likely interpretation:")
                    if len(integers) >= 2:
                        print(f"    - RRU.PrbUsedDl (NO_LABEL): {integers[0]} PRBs")
                        print(f"    - RRU.PrbUsedDl (SLICE): {integers[1]} PRBs")
                    if len(integers) >= 4:
                        print(f"    - RRU.PrbUsedUl (NO_LABEL): {integers[2]} PRBs")
                        print(f"    - RRU.PrbUsedUl (SLICE): {integers[3]} PRBs")
        
        print(f"{'='*80}\n")

    def subscription_callback_slice_filtered(self, e2_agent_id, subscription_id, indication_hdr, indication_msg, label_type):
        """Callback for SLICE_ID_LABEL subscription (filtered metrics)"""
        self.indication_count += 1
        
        print(f"\n{'='*40}")
        print(f"[SLICE_FILTERED] RIC INDICATION #{self.indication_count} RECEIVED!")
        print(f"{'='*40}")
        print(f"[DEBUG] Callback triggered successfully")
        print(f"[DEBUG] e2_agent_id = {e2_agent_id}")
        print(f"[DEBUG] subscription_id = {subscription_id}")
        
        # Debug: Raw payload inspection
        if isinstance(indication_msg, bytes):
            print(f"[DEBUG] indication_msg is bytes, length = {len(indication_msg)}")
            print(f"[HEX DUMP] First 64 bytes of indication_msg:")
            hex_str = indication_msg[:64].hex()
            for i in range(0, len(hex_str), 32):
                print(f"  {hex_str[i:i+32]}")
        
        print(f"\n{'='*80}")
        print(f"[SLICE_FILTERED SST=1] RIC Indication from {e2_agent_id}, Subscription ID: {subscription_id}")
        print(f"{'='*80}")
        
        try:
            indication_hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
            
            print(f"Timestamp: {indication_hdr['colletStartTime']}")
            print(f"Granularity Period: {meas_data.get('granulPeriod', 'N/A')} ms")
            
            if "measData" in meas_data:
                dl_prb = meas_data["measData"].get("RRU.PrbUsedDl", ["N/A"])[0]
                ul_prb = meas_data["measData"].get("RRU.PrbUsedUl", ["N/A"])[0]
                
                self.metrics_received['slice_filtered']['dl'].append(dl_prb)
                self.metrics_received['slice_filtered']['ul'].append(ul_prb)
                
                print(f"\n  SLICE-FILTERED METRICS (SST=1, SD=0 only):")
                print(f"    RRU.PrbUsedDl: {dl_prb} PRBs")
                print(f"    RRU.PrbUsedUl: {ul_prb} PRBs")
            else:
                print("  WARNING: No measData found in indication")
                print(f"  [DEBUG] meas_data keys: {meas_data.keys()}" if isinstance(meas_data, dict) else f"  [DEBUG] meas_data type: {type(meas_data)}")
        
        except Exception as e:
            print(f"\n[ERROR] Failed to decode E2SM-KPM message:")
            print(f"  Exception: {type(e).__name__}: {e}")
            print(f"  Attempting simple INTEGER extraction as fallback...")
            
            # Fallback: Simple INTEGER decoder
            if isinstance(indication_msg, bytes):
                integers = self._extract_integers_from_asn1(indication_msg)
                if integers:
                    print(f"\n  Extracted {len(integers)} INTEGER values:")
                    for idx, val in enumerate(integers):
                        print(f"    [{idx}]: {val}")
                    print(f"\n  Likely interpretation (SLICE-FILTERED):")
                    if len(integers) >= 2:
                        print(f"    - RRU.PrbUsedDl (NO_LABEL): {integers[0]} PRBs")
                        print(f"    - RRU.PrbUsedDl (SLICE SST=1): {integers[1]} PRBs")
                    if len(integers) >= 4:
                        print(f"    - RRU.PrbUsedUl (NO_LABEL): {integers[2]} PRBs")
                        print(f"    - RRU.PrbUsedUl (SLICE SST=1): {integers[3]} PRBs")
        
        print(f"{'='*80}\n")
    
    def _extract_integers_from_asn1(self, payload):
        """
        Simple heuristic decoder for ASN.1 INTEGER values
        
        Looks for ASN.1 INTEGER tags (0x02) and extracts their values.
        This is a fallback when proper E2SM-KPM decoding fails.
        
        Args:
            payload: bytes - Raw ASN.1 encoded payload
            
        Returns:
            list of int - Extracted INTEGER values
        """
        integers_found = []
        i = 0
        while i < len(payload) - 3:
            if payload[i] == 0x02:  # INTEGER tag
                length = payload[i+1]
                if length <= 4 and i + 2 + length <= len(payload):
                    value_bytes = payload[i+2:i+2+length]
                    try:
                        value = int.from_bytes(value_bytes, 'big', signed=False)
                        integers_found.append(value)
                        i += 2 + length
                    except:
                        i += 1
                else:
                    i += 1
            else:
                i += 1
        return integers_found

    @xAppBase.start_function
    def start(self, e2_node_id, test_mode):
        report_period = 1000  # 1 second
        granul_period = 1000
        
        print(f"\n{'#'*80}")
        print("#" + " "*78 + "#")
        print("#  Phase 2 Slice Filtering Test - E2SM-KPM Report Style 1".center(80) + "#")
        print("#" + " "*78 + "#")
        print(f"{'#'*80}\n")
        
        print(f"E2 Node ID: {e2_node_id}")
        print(f"Report Period: {report_period} ms")
        print(f"Metrics: RRU.PrbUsedDl, RRU.PrbUsedUl")
        print(f"Test Mode: {test_mode}")
        print(f"RAN Function ID: {self.e2sm_kpm.ran_func_id}")
        print(f"xApp is now ACTIVE and listening for callbacks\n")
        
        if test_mode in ['no_label', 'both']:
            print("=" * 80)
            print("Subscribing: NO_LABEL (Aggregate)")
            print("=" * 80)
            print("Expected gNB logs:")
            print("  - Phase 2B: RRU.PrbUsedDl aggregate X PRBs")
            print("  - Phase 2C: RRU.PrbUsedUl aggregate X PRBs\n")
            
            # Create custom action definition with NO_LABEL
            metric_names = ['RRU.PrbUsedDl', 'RRU.PrbUsedUl']
            event_trigger_def = self.e2sm_kpm.e2sm_kpm_compiler.pack_event_trigger_def(report_period)
            
            # Pack action def with NO_LABEL
            measInfoList = []
            for metric_name in metric_names:
                metric_def = {
                    'measType': ('measName', metric_name),
                    'labelInfoList': [{'measLabel': {'noLabel': 'true'}}]
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
            
            print(f"[DEBUG] Event Trigger Definition encoded: {len(event_trigger_def)} bytes")
            print(f"[DEBUG] Action Definition encoded: {len(action_def)} bytes")
            print(f"[DEBUG] Calling self.subscribe() for NO_LABEL...")
            
            from lib.e2sm_kpm_module import e2sm_types
            callback = lambda agent, sub, hdr, msg: self.subscription_callback_no_label(agent, sub, hdr, msg, 'no_label')
            self.subscribe(e2_node_id, self.e2sm_kpm.ran_func_id, event_trigger_def, action_def, callback, e2sm_types.E2SM_KPM)
            print("NO_LABEL subscription sent")
            print("[DEBUG] Waiting for subscription response...")
            print("[INFO] If callback is not invoked, check:")
            print("  1. E2 Node ID is correct")
            print("  2. RIC routing table includes this xApp")
            print("  3. E2Term/SubMgr are forwarding messages")
            print()
            time.sleep(2)
        
        if test_mode in ['slice_filtered', 'both']:
            print("=" * 80)
            print("Subscribing: SLICE_ID_LABEL (SST=1, SD=0)")
            print("=" * 80)
            print("Expected gNB logs:")
            print("  - Phase 2A: Extracted slice_id from label: SST=1, SD=0")
            print("  - Phase 2B: RRU.PrbUsedDl filtering by slice SST=1, SD=0")
            print("  - Phase 2B: RRU.PrbUsedDl slice-filtered X UEs, Y PRBs")
            print("  - Phase 2C: RRU.PrbUsedUl filtering by slice SST=1, SD=0")
            print("  - Phase 2C: RRU.PrbUsedUl slice-filtered X UEs, Y PRBs\n")
            
            # Create custom action definition with SLICE_ID_LABEL
            metric_names = ['RRU.PrbUsedDl', 'RRU.PrbUsedUl']
            event_trigger_def = self.e2sm_kpm.e2sm_kpm_compiler.pack_event_trigger_def(report_period)
            
            # Pack action def with SLICE_ID_LABEL (SST=1, SD=0)
            measInfoList = []
            for metric_name in metric_names:
                metric_def = {
                    'measType': ('measName', metric_name),
                    'labelInfoList': [{
                        'measLabel': {
                            'sliceID': {
                                'sST': b'\x01',  # SST = 1 (note: capital S and T)
                                'sD': b'\x00\x00\x00'  # SD = 0 (note: lowercase s, capital D)
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
            
            print(f"[DEBUG] Event Trigger Definition encoded: {len(event_trigger_def)} bytes")
            print(f"[DEBUG] Action Definition encoded: {len(action_def)} bytes")
            print(f"[DEBUG] Calling self.subscribe() for SLICE_ID_LABEL...")
            
            from lib.e2sm_kpm_module import e2sm_types
            callback = lambda agent, sub, hdr, msg: self.subscription_callback_slice_filtered(agent, sub, hdr, msg, 'slice_filtered')
            self.subscribe(e2_node_id, self.e2sm_kpm.ran_func_id, event_trigger_def, action_def, callback, e2sm_types.E2SM_KPM)
            print("SLICE_ID_LABEL subscription sent")
            print("[DEBUG] Waiting for subscription response...")
            print("[INFO] If callback is not invoked, check:")
            print("  1. gNB supports SLICE_ID_LABEL filtering")
            print("  2. UE is assigned to slice SST=1")
            print("  3. RIC is forwarding indications to xApp")
            print()
        
        print("\n" + "="*80)
        print("Subscriptions active - monitoring metrics...")
        print("Check gNB logs for Phase 2A/2B/2C debug messages")
        print("Press Ctrl+C to stop and see summary")
        print("="*80)
        print("\n[INFO] xApp is now in WAIT mode - callbacks will be invoked when indications arrive")
        print("[INFO] If you see gNB '[E2 TRACE] SCTP send completed' but no callback here:")
        print("  → Problem is in RIC infrastructure (E2Term/SubMgr/RMR routing)")
        print("  → Run: docker logs -f e2term")
        print("  → Run: docker logs -f submgr")
        print("="*80 + "\n")

    def print_summary(self):
        """Print test summary on exit"""
        print("\n\n" + "="*80)
        print("=" + " "*78 + "=")
        print("  Phase 2 Test Summary".center(80))
        print("=" + " "*78 + "=")
        print("="*80 + "\n")
        
        if self.metrics_received['no_label']['dl']:
            avg_dl_no_label = sum(self.metrics_received['no_label']['dl']) / len(self.metrics_received['no_label']['dl'])
            avg_ul_no_label = sum(self.metrics_received['no_label']['ul']) / len(self.metrics_received['no_label']['ul'])
            print(f"NO_LABEL (Aggregate) - Samples: {len(self.metrics_received['no_label']['dl'])}")
            print(f"  Average DL PRB: {avg_dl_no_label:.2f}")
            print(f"  Average UL PRB: {avg_ul_no_label:.2f}\n")
        
        if self.metrics_received['slice_filtered']['dl']:
            avg_dl_slice = sum(self.metrics_received['slice_filtered']['dl']) / len(self.metrics_received['slice_filtered']['dl'])
            avg_ul_slice = sum(self.metrics_received['slice_filtered']['ul']) / len(self.metrics_received['slice_filtered']['ul'])
            print(f"SLICE_FILTERED (SST=1) - Samples: {len(self.metrics_received['slice_filtered']['dl'])}")
            print(f"  Average DL PRB: {avg_dl_slice:.2f}")
            print(f"  Average UL PRB: {avg_ul_slice:.2f}\n")
        
        if self.metrics_received['no_label']['dl'] and self.metrics_received['slice_filtered']['dl']:
            print("Phase 2 Validation:")
            print(f"  - Slice-filtered ≤ Aggregate: {avg_dl_slice <= avg_dl_no_label} (DL)")
            print(f"  - Slice-filtered ≤ Aggregate: {avg_ul_slice <= avg_ul_no_label} (UL)")
            if avg_dl_slice <= avg_dl_no_label and avg_ul_slice <= avg_ul_no_label:
                print("\nPhase 2 PASSED: Slice filtering working correctly!")
            else:
                print("\nPhase 2 WARNING: Slice-filtered values exceed aggregate")
        
        print("\n" + "="*80 + "\n")

    def signal_handler(self, sig, frame):
        """Override signal handler to print summary before exit"""
        self.print_summary()
        super().signal_handler(sig, frame)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Phase 2 Slice Filtering Test xApp')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8093, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4563, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=2, help="RAN function ID")
    parser.add_argument("--test_mode", type=str, default='both', 
                        choices=['no_label', 'slice_filtered', 'both'],
                        help="Test mode: no_label, slice_filtered, or both")

    args = parser.parse_args()
    
    # Create xApp
    myXapp = Phase2TestXapp(args.config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_kpm.set_ran_func_id(args.ran_func_id)

    # Connect exit signals
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp
    myXapp.start(args.e2_node_id, args.test_mode)
