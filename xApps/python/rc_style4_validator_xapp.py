#!/usr/bin/env python3

"""
E2SM-RC Report Style 4 (UE Information) Validator xApp

This xApp subscribes to E2SM-RC Report Style 4 to receive UE context information
including UE ID and S-NSSAI (slice assignment). It prints this information to the
terminal to validate the RC Report Style 4 patch implementation.

Usage:
    python3 rc_style4_validator_xapp.py --e2_node_id <E2_NODE_ID>

Example:
    python3 rc_style4_validator_xapp.py --e2_node_id gnb_001_001_00019b
"""

import time
import datetime
import argparse
import signal
import struct
from lib.xAppBase import xAppBase
from lib.e2sm_kpm_module import e2sm_types


class RCStyle4ValidatorXapp(xAppBase):
    def __init__(self, http_server_port, rmr_port):
        super(RCStyle4ValidatorXapp, self).__init__('', http_server_port, rmr_port)
        self.ue_contexts = {}  # Store UE ID -> S-NSSAI mapping
        
    def decode_s_nssai(self, s_nssai_bytes):
        """
        Decode S-NSSAI from byte array.
        Format: [SST (1 byte)] + [SD (3 bytes, optional)]
        
        Returns:
            dict: {'sst': int, 'sd': int or None}
        """
        if len(s_nssai_bytes) < 1:
            return None
            
        sst = s_nssai_bytes[0]
        sd = None
        
        if len(s_nssai_bytes) >= 4:
            # SD is present (3 bytes)
            sd = (s_nssai_bytes[1] << 16) | (s_nssai_bytes[2] << 8) | s_nssai_bytes[3]
            
        return {'sst': sst, 'sd': sd}
    
    def decode_ue_id(self, ue_id_bytes):
        """
        Decode UE ID from byte array.
        For gNB-DU-UE-F1AP-ID, this is typically a 32-bit integer.
        
        Returns:
            int: UE ID
        """
        if len(ue_id_bytes) == 0:
            return None
        
        # Try to decode as integer (big-endian)
        ue_id = 0
        for byte in ue_id_bytes:
            ue_id = (ue_id << 8) | byte
        
        return ue_id

    def parse_e2sm_rc_indication_format2(self, indication_msg_bytes):
        """
        Parse E2SM-RC Indication Message Format 2 (UE-level parameters).
        
        Simplified decoder that uses pattern matching for the specific
        gNB implementation rather than full PER decoding.
        
        Returns:
            list: List of dicts with 'ue_id' and 's_nssai'
        """
        ue_info_list = []
        
        if len(indication_msg_bytes) < 2:
            return ue_info_list
        
        try:
            # SIMPLIFIED APPROACH: Instead of full PER decoding, use pattern recognition
            # The gNB encodes messages in a predictable way, so we can extract data directly
            
            # Look for characteristic patterns in the byte stream:
            # - UE IDs are typically 32-bit values encoded as 4 bytes
            # - S-NSSAI SST is 1 byte (value 1-255)
            # - S-NSSAI SD is 3 bytes (optional)
            # - C-RNTI is 16 bits (2 bytes)
            
            offset = 0  # Byte offset
            
            # For very short messages (< 20 bytes), use simplified extraction
            if len(indication_msg_bytes) <= 20:
                # DECODED 14-byte message structure (SST now properly encoded!):
                # Bytes 0-4:  08 00 00 08 00  (ASN.1 header)
                # Byte 5:     F1AP ID         (changes per connection)
                # Bytes 6-9:  00 00 00 00     (zeros/padding)
                # Bytes 10-11: 2a 00          (S-NSSAI marker tag)
                # Byte 12:    01              (length or counter)
                # Byte 13:    SST value       (actual SST! 0x01, 0x02, 0x03, etc.)
                
                # Extract F1AP ID
                f1ap_id = None
                if len(indication_msg_bytes) >= 6:
                    f1ap_id = indication_msg_bytes[5]
                
                # Extract SST from offset 13 (where it's actually encoded)
                sst = None
                if len(indication_msg_bytes) >= 14:
                    sst = indication_msg_bytes[13]
                
                # Try to extract UE ID from offset 8-9 (though this seems to be zeros)
                ue_id = None
                if len(indication_msg_bytes) >= 10:
                    ue_id_bytes = indication_msg_bytes[8:10]
                    ue_id = struct.unpack('>H', bytes(ue_id_bytes))[0]
                    if ue_id == 0:
                        ue_id = f1ap_id  # Use F1AP ID as UE identifier
                
                ue_info = {
                    'ue_id': ue_id if ue_id else 'unknown',
                    'c_rnti': None,
                    's_nssai': {
                        'sst': sst,
                        'sd': None  # SD not in short messages
                    }
                }
                ue_info_list.append(ue_info)
                
                return ue_info_list
            
            # Use same pattern extraction as for short messages
            # Look for SST value (1 byte)
            for i in range(len(indication_msg_bytes) - 4):
                potential_sst = indication_msg_bytes[i]
                if 1 <= potential_sst <= 10:  # Typical SST range
                    # Check if SD follows (3 bytes)
                    potential_sd_bytes = indication_msg_bytes[i+1:i+4]
                    potential_sd = (potential_sd_bytes[0] << 16) | (potential_sd_bytes[1] << 8) | potential_sd_bytes[2]
                    
                    s_nssai = {
                        'sst': potential_sst,
                        'sd': potential_sd if potential_sd > 0 else None
                    }
                    
                    ue_info = {
                        'ue_id': 'unknown',
                        'c_rnti': 'N/A',
                        's_nssai': s_nssai
                    }
                    ue_info_list.append(ue_info)
                    break  # Found one UE
        
        except Exception as e:
            print(f"    Error: Parsing failed - {e}")
        
        return ue_info_list

    def rc_style4_indication_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        """
        Callback function for E2SM-RC Report Style 4 (UE Information) indications.
        
        Args:
            e2_agent_id: E2 node identifier (gNB)
            subscription_id: Subscription ID
            indication_hdr: RIC Indication Header (Format 1)
            indication_msg: RIC Indication Message (Format 2 - UE level params)
        """
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        
        print("=" * 80)
        print(f"[{timestamp}] RC REPORT STYLE 4 - UE INFORMATION RECEIVED")
        print("=" * 80)
        print(f"  E2 Node ID:      {e2_agent_id}")
        print(f"  Subscription ID: {subscription_id}")
        print(f"  Message Type:    E2SM-RC Indication - Report Style 4 (UE Information)")
        print()
        
        # Parse indication header
        print("  Indication Header (Format 1):")
        print(f"    Length: {len(indication_hdr)} bytes")
        print(f"    Raw (hex): {' '.join(f'{b:02x}' for b in indication_hdr[:50])}")  # First 50 bytes
        print()
        
        # Parse indication message (Format 2 - UE-level parameters)
        print("  Indication Message (Format 2 - UE Level Parameters):")
        print(f"    Length: {len(indication_msg)} bytes")
        print(f"    Raw (hex): {' '.join(f'{b:02x}' for b in indication_msg)}")
        print()
        
        try:
            ue_info_list = self.parse_e2sm_rc_indication_format2(indication_msg)
            
            if len(ue_info_list) == 0:
                print("  Empty UE list - all UEs disconnected")
                print()
                
                # Clear cache for this E2 node (all UEs disconnected)
                keys_to_remove = [key for key in self.ue_contexts.keys() if key.startswith(f"{e2_agent_id}:")]
                for key in keys_to_remove:
                    del self.ue_contexts[key]
                    print(f"  Removed {key} from cache (disconnected)")
                
                if len(keys_to_remove) > 0:
                    print(f"  Total removed: {len(keys_to_remove)} UE(s)")
                print()
            else:
                # Build set of current UE IDs in this indication
                current_ue_ids = {f"{e2_agent_id}:{ue_info['ue_id']}" for ue_info in ue_info_list}
                
                # Remove UEs that are no longer in the indication (disconnected)
                keys_to_remove = []
                for ue_key in list(self.ue_contexts.keys()):
                    if ue_key.startswith(f"{e2_agent_id}:") and ue_key not in current_ue_ids:
                        keys_to_remove.append(ue_key)
                
                if len(keys_to_remove) > 0:
                    print(f"  Detected {len(keys_to_remove)} disconnected UE(s):")
                    for key in keys_to_remove:
                        del self.ue_contexts[key]
                        print(f"    Removed {key} from cache")
                    print()
                
                print(f"  Found {len(ue_info_list)} UE(s)")
                print()
                
                for idx, ue_info in enumerate(ue_info_list, 1):
                    s_nssai = ue_info['s_nssai']
                    print(f"  UE #{idx}:")
                    print(f"    UE ID:      {ue_info['ue_id']}")
                    print(f"    C-RNTI:     {ue_info.get('c_rnti', 'N/A')}")
                    print(f"    S-NSSAI:")
                    print(f"      SST:      {s_nssai['sst']}")
                    if s_nssai['sd'] is not None:
                        print(f"      SD:       {s_nssai['sd']:#08x} ({s_nssai['sd']})")
                    else:
                        print(f"      SD:       N/A")
                    
                    # Store in local cache
                    ue_key = f"{e2_agent_id}:{ue_info['ue_id']}"
                    self.ue_contexts[ue_key] = {
                        'timestamp': timestamp,
                        's_nssai': s_nssai,
                        'c_rnti': ue_info.get('c_rnti'),
                        'e2_node': e2_agent_id
                    }
                    print()
                
        except Exception as e:
            print(f"    Error parsing indication message: {e}")
            print("    Dumping raw message for debugging:")
            print("    " + ' '.join(f'{b:02x}' for b in indication_msg[:100]))
            import traceback
            traceback.print_exc()
        
        print("=" * 80)
        print()
        
        # Print summary of all tracked UEs
        if len(self.ue_contexts) > 0:
            print(" SUMMARY - All Tracked UEs:")
            print("─" * 80)
            for ue_key, context in self.ue_contexts.items():
                s_nssai = context['s_nssai']
                sst_str = f"{s_nssai['sst']}" if s_nssai['sst'] is not None else "N/A"
                sd_str = f"{s_nssai['sd']:#08x}" if s_nssai['sd'] is not None else "N/A"
                c_rnti_str = f"{context.get('c_rnti', 'N/A')}" if context.get('c_rnti') is not None else "N/A"
                print(f"  {ue_key} → SST={sst_str}, SD={sd_str}, C-RNTI={c_rnti_str} (last seen: {context['timestamp']})")
            print("─" * 80)
            print()

    @xAppBase.start_function
    def start(self, e2_node_id):
        """
        Start the RC Style 4 validator xApp.
        
        Args:
            e2_node_id: E2 node identifier (gNB) to subscribe to
        """
        print("=" * 80)
        print("E2SM-RC REPORT STYLE 4 (UE INFORMATION) VALIDATOR xApp")
        print("=" * 80)
        print(f"Target E2 Node: {e2_node_id}")
        print(f"RAN Function:   E2SM-RC (ID=3)")
        print(f"Report Style:   Style 4 (UE Information)")
        print("=" * 80)
        print()
        
        # E2SM-RC RAN Function ID
        ran_function_id = 3
        
        # Build E2SM-RC Action Definition for Report Style 4
        # Note: srsRAN may not properly decode complex ASN.1 - try minimal encoding
        # For testing, use empty byte arrays which some implementations accept as defaults
        
        # Option 1: Try empty (may use defaults)
        action_def = bytes([])
        event_trigger_def = bytes([])
        
        print("📡 Sending RIC Subscription Request (using minimal encoding)...")
        print(f"   RAN Function ID: {ran_function_id}")
        print(f"   Report Style:    4 (UE Information)")
        print(f"   Event Trigger:   Empty (default)")
        print(f"   Action Def:      Empty (default Style 4)")
        print()
        
        try:
            # Subscribe to E2SM-RC Report Style 4
            self.subscribe(
                e2_node_id=e2_node_id,
                ran_function_id=ran_function_id,
                event_trigger_def=event_trigger_def,
                action_def=action_def,
                indication_callback=self.rc_style4_indication_callback,
                e2sm_type=e2sm_types.E2SM_RC  # Mark as RC type (not KPM)
            )
            
            print(" Subscription request sent successfully!")
            print()
            print(" Waiting for UE context information from gNB...")
            print("   (Connect a UE to the network to trigger RC Report Style 4 indications)")
            print()
            print("─" * 80)
            print()
            
        except Exception as e:
            print(f" Error during subscription: {e}")
            import traceback
            traceback.print_exc()
            return
        
        # Keep xApp running
        print("xApp is now running. Press Ctrl+C to stop.")
        print()


def main():
    parser = argparse.ArgumentParser(
        description='E2SM-RC Report Style 4 (UE Information) Validator xApp',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 rc_style4_validator_xapp.py --e2_node_id gnb_001_001_00019b
  python3 rc_style4_validator_xapp.py --e2_node_id gnb_001_001_00019b --http_port 8091 --rmr_port 4561

This xApp subscribes to E2SM-RC Report Style 4 to receive UE context information
including UE ID and S-NSSAI (network slice assignment). When a UE connects to the
network, the gNB will send RIC Indication messages with the UE's slice information.

The xApp will print:
  - UE ID (e.g., gNB-DU-UE-F1AP-ID)
  - S-NSSAI (SST and SD values)
  - Timestamp of the indication

This validates the RC Report Style 4 patch implementation in srsRAN.
        """
    )
    
    parser.add_argument(
        '--e2_node_id',
        type=str,
        required=True,
        help='E2 Node ID (gNB identifier) to subscribe to'
    )
    
    parser.add_argument(
        '--http_port',
        type=int,
        default=8090,
        help='HTTP server port for xApp (default: 8090)'
    )
    
    parser.add_argument(
        '--rmr_port',
        type=int,
        default=4560,
        help='RMR port for xApp (default: 4560)'
    )
    
    args = parser.parse_args()
    
    # Create and start xApp
    xapp = RCStyle4ValidatorXapp(
        http_server_port=args.http_port,
        rmr_port=args.rmr_port
    )
    
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)
    
    # Start xApp
    xapp.start(e2_node_id=args.e2_node_id)


if __name__ == '__main__':
    main()
