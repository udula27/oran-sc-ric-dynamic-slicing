#!/usr/bin/env python3

import time
import datetime
import argparse
import signal
import json
from lib.xAppBase import xAppBase
from ricxappframe.xapp_frame import rmr

class MyXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)
        self.current_alloc = {1: (10, 30), 2: (20, 40), 3: (5, 15)}
        self.e2_node_id = None

    @xAppBase.start_function
    def start(self, e2_node_id):
        self.e2_node_id = e2_node_id
        print("Control xApp listening for KMPs via RMR...")
        # The main RMR loop is handled by xAppBase._run method which we override
        while self.running:
            time.sleep(1)

    def _run(self):
        # Override the base _run method to handle KMP messages
        while self.running:
            try:
                sbuf = rmr.rmr_torcv_msg(self.rmr_client, None, 100)
                summary = rmr.message_summary(sbuf)
            except Exception as e:
                continue

            if summary[rmr.RMR_MS_MSG_STATE] == 0:  # RMR_OK
                # Check for KMP data message type 60000
                if summary['message type'] == 60000:
                    data = rmr.get_payload(sbuf)
                    try:
                        kmp_data = json.loads(data.decode('utf-8'))
                        self.process_kmp_data(kmp_data)
                    except Exception as e:
                        print(f"[CONTROL] Error processing KMP data: {e}")
                
                # Handle other message types (RIC Control ACK/FAILURE)
                elif summary['message type'] == 12041:
                    print("Received RIC_CONTROL_ACK")
                elif summary['message type'] == 12042:
                    print("Received RIC_CONTROL_FAILURE")

            rmr.rmr_free_msg(sbuf)

    def process_kmp_data(self, kmp_data):
        # Example logic: only increase/decrease, no change if already optimal
        new_alloc = {}
        # eMBB
        sst1 = kmp_data.get('sst1', {})
        if sst1.get('Throughput_DL', 0) > 1000 and sst1.get('UE_count', 0) > 10:
            new_alloc[1] = (20, 40)
        else:
            new_alloc[1] = (10, 20)
        # mMTC
        sst2 = kmp_data.get('sst2', {})
        if sst2.get('PRACH_SuccessRate', 1) < 0.9 or sst2.get('Paging_SuccessRate', 1) < 0.9:
            new_alloc[2] = (10, 20)
        else:
            new_alloc[2] = (5, 10)
        # URLLC
        sst3 = kmp_data.get('sst3', {})
        if sst3.get('Latency_P99', 0) > 10 or sst3.get('BLER_UL', 0) > 0.05:
            new_alloc[3] = (15, 30)
        else:
            new_alloc[3] = (5, 15)
        # Only send control if allocation changes
        for sst, alloc in new_alloc.items():
            if self.current_alloc.get(sst) != alloc:
                ue_id = 0
                print(f"[CONTROL] Changing PRB allocation for SST={sst}: {self.current_alloc.get(sst)} -> {alloc}")
                self.e2sm_rc.control_slice_level_prb_quota(self.e2_node_id, ue_id, alloc[0], alloc[1], dedicated_prb_ratio=100, ack_request=1)
                self.current_alloc[sst] = alloc
            else:
                print(f"[CONTROL] No change for SST={sst}, allocation remains {alloc}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='My example xApp')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8090, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4560, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--gnb_cu_ue_f1ap_id", type=int, default=0, help="UE ID")

    args = parser.parse_args()
    config = args.config
    e2_node_id = args.e2_node_id
    ran_func_id = args.ran_func_id

    # Create MyXapp.
    myXapp = MyXapp(config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_rc.set_ran_func_id(ran_func_id)

    # Connect exit signals.
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp listening for KMPs via RMR
    myXapp.start(e2_node_id)
