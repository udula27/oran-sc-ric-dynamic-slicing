#!/usr/bin/env python3

import argparse
import signal
import json
from lib.xAppBase import xAppBase


class MyXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

    def my_subscription_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg, kmp_report_style, ue_id):
        print(f"\nRIC Indication Received from {e2_agent_id} for Subscription ID: {subscription_id}, KMP Report Style: {kmp_report_style}")
        indication_hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
        meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
        print("E2SM_KPM RIC Indication Content:")
        print("-ColletStartTime:", indication_hdr['colletStartTime'])
        print("-granulPeriod:", meas_data.get("granulPeriod", None))
        print("DEBUG - Full meas_data structure:", json.dumps(meas_data, indent=2, default=str))

        # Define slice metrics
        slice_metrics = {
            1: {  # eMBB
                "name": "eMBB",
                "minimal": ["Throughput_DL", "Throughput_UL", "PRB_use_DL", "PRB_use_UL", "UE_count", "CQI_avg", "MCS_avg", "BLER_DL", "BLER_UL"],
                "extended": ["UE_Throughput_P95", "SpectralEfficiency_avg"]
            },
            2: {  # URLLC
                "name": "URLLC",
                "minimal": ["Throughput_DL", "Throughput_UL", "PRB_use_DL", "PRB_use_UL", "UE_count", "CQI_avg", "MCS_avg", "BLER_DL", "BLER_UL"],
                "extended": ["Latency_P99", "PacketLoss", "GrantLatency"]
            },
            3: {  # mMTC
                "name": "mMTC",
                "minimal": ["Throughput_DL", "Throughput_UL", "PRB_use_DL", "PRB_use_UL", "UE_count", "CQI_avg", "MCS_avg", "BLER_DL", "BLER_UL"],
                "extended": ["PRACH_SuccessRate", "RRC_Setup_SuccessRate", "Paging_SuccessRate"]
            }
        }

        # Prepare KMPs for control xApp
        kmp_payload = {}
        for sst, info in slice_metrics.items():
            kmp_payload[f'sst{sst}'] = {}
            for metric in info["minimal"] + info["extended"]:
                value = None
                if "measData" in meas_data and metric in meas_data["measData"]:
                    value = meas_data["measData"][metric]
                elif "ueMeasData" in meas_data:
                    for ue_id, ue_meas in meas_data["ueMeasData"].items():
                        if metric in ue_meas["measData"]:
                            value = ue_meas["measData"][metric]
                if value is not None:
                    kmp_payload[f'sst{sst}'][metric] = value[0] if isinstance(value, list) and value else value

        # Send KMPs to control xApp via RMR
        try:
            payload = json.dumps(kmp_payload).encode('utf-8')
            # Use message type 60000 for inter-xApp KMP data
            self.rmr_send("control_xapp", payload, 60000, retries=1)
            print("[KMP_MON] Sent KMPs to control xApp via RMR.")
        except Exception as e:
            print(f"[KMP_MON] Failed to send KMPs to control xApp: {e}")

        # Print metrics grouped by slice
        for sst, info in slice_metrics.items():
            print(f"\nSlice SST={sst} ({info['name']}):")
            print("  Minimal Metrics:")
            for metric in info["minimal"]:
                value = None
                if "measData" in meas_data and metric in meas_data["measData"]:
                    value = meas_data["measData"][metric]
                elif "ueMeasData" in meas_data:
                    for ue_id, ue_meas in meas_data["ueMeasData"].items():
                        if metric in ue_meas["measData"]:
                            value = ue_meas["measData"][metric]
                            print(f"    UE {ue_id}: {metric} = {value}")
                    continue
                if value is not None:
                    print(f"    {metric}: {value}")
            print("  Extended Metrics:")
            for metric in info["extended"]:
                value = None
                if "measData" in meas_data and metric in meas_data["measData"]:
                    value = meas_data["measData"][metric]
                elif "ueMeasData" in meas_data:
                    for ue_id, ue_meas in meas_data["ueMeasData"].items():
                        if metric in ue_meas["measData"]:
                            value = ue_meas["measData"][metric]
                            print(f"    UE {ue_id}: {metric} = {value}")
                    continue
                if value is not None:
                    print(f"    {metric}: {value}")


    # Mark the function as xApp start function using xAppBase.start_function decorator.
    # It is required to start the internal msg receive loop.
    @xAppBase.start_function
    def start(self, e2_node_id, kpm_report_style, ue_ids, metric_names):
        report_period = 1000
        granul_period = 1000

        # use always the same subscription callback, but bind kpm_report_style parameter
        subscription_callback = lambda agent, sub, hdr, msg: self.my_subscription_callback(agent, sub, hdr, msg, kpm_report_style, None)

        if (kpm_report_style == 1):
            print("Subscribe to E2 node ID: {}, RAN func: e2sm_kpm, Report Style: {}, metrics: {}".format(e2_node_id, kpm_report_style, metric_names))
            self.e2sm_kpm.subscribe_report_service_style_1(e2_node_id, report_period, metric_names, granul_period, subscription_callback)

        elif (kpm_report_style == 2):
            # need to bind also UE_ID to callback as it is not present in the RIC indication in the case of E2SM KPM Report Style 2
            subscription_callback = lambda agent, sub, hdr, msg: self.my_subscription_callback(agent, sub, hdr, msg, kpm_report_style, ue_ids[0])
            
            print("Subscribe to E2 node ID: {}, RAN func: e2sm_kpm, Report Style: {}, UE_id: {}, metrics: {}".format(e2_node_id, kpm_report_style, ue_ids[0], metric_names))
            self.e2sm_kpm.subscribe_report_service_style_2(e2_node_id, report_period, ue_ids[0], metric_names, granul_period, subscription_callback)

        elif (kpm_report_style == 3):
            if (len(metric_names) > 1):
                metric_names = metric_names[0]
                print("INFO: Currently only 1 metric can be requested in E2SM-KPM Report Style 3, selected metric: {}".format(metric_names))
            # TODO: currently only dummy condition that is always satisfied, useful to get IDs of all connected UEs
            # example matching UE condition: ul-rSRP < 1000
            matchingConds = [{'matchingCondChoice': ('testCondInfo', {'testType': ('ul-rSRP', 'true'), 'testExpr': 'lessthan', 'testValue': ('valueInt', 1000)})}]

            print("Subscribe to E2 node ID: {}, RAN func: e2sm_kpm, Report Style: {}, metrics: {}".format(e2_node_id, kpm_report_style, metric_names))
            self.e2sm_kpm.subscribe_report_service_style_3(e2_node_id, report_period, matchingConds, metric_names, granul_period, subscription_callback)

        elif (kpm_report_style == 4):
            # TODO: currently only dummy condition that is always satisfied, useful to get IDs of all connected UEs
            # example matching UE condition: ul-rSRP < 1000
            matchingUeConds = [{'testCondInfo': {'testType': ('ul-rSRP', 'true'), 'testExpr': 'lessthan', 'testValue': ('valueInt', 1000)}}]
            
            print("Subscribe to E2 node ID: {}, RAN func: e2sm_kpm, Report Style: {}, metrics: {}".format(e2_node_id, kpm_report_style, metric_names))
            self.e2sm_kpm.subscribe_report_service_style_4(e2_node_id, report_period, matchingUeConds, metric_names, granul_period, subscription_callback)

        elif (kpm_report_style == 5):
            if (len(ue_ids) < 2):
                dummyUeId = ue_ids[0] + 1
                ue_ids.append(dummyUeId)
                print("INFO: Subscription for E2SM_KPM Report Service Style 5 requires at least two UE IDs -> add dummy UeID: {}".format(dummyUeId))

            print("Subscribe to E2 node ID: {}, RAN func: e2sm_kpm, Report Style: {}, UE_ids: {}, metrics: {}".format(e2_node_id, kpm_report_style, ue_ids, metric_names))
            self.e2sm_kpm.subscribe_report_service_style_5(e2_node_id, report_period, ue_ids, metric_names, granul_period, subscription_callback)

        else:
            print("INFO: Subscription for E2SM_KPM Report Service Style {} is not supported".format(kpm_report_style))
            exit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='My example xApp')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8092, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4562, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=2, help="RAN function ID")
    parser.add_argument("--kpm_report_style", type=int, default=1, help="xApp config file path")
    parser.add_argument("--ue_ids", type=str, default='0', help="UE ID")
    parser.add_argument("--metrics", type=str, default='DRB.UEThpUl,DRB.UEThpDl', help="Metrics name as comma-separated string")

    args = parser.parse_args()
    config = args.config
    e2_node_id = args.e2_node_id # TODO: get available E2 nodes from SubMgr, now the id has to be given.
    ran_func_id = args.ran_func_id # TODO: get available E2 nodes from SubMgr, now the id has to be given.
    ue_ids = list(map(int, args.ue_ids.split(","))) # Note: the UE id has to exist at E2 node!
    kpm_report_style = args.kpm_report_style
    metrics = args.metrics.split(",")

    # Create MyXapp.
    myXapp = MyXapp(config, args.http_server_port, args.rmr_port)
    myXapp.e2sm_kpm.set_ran_func_id(ran_func_id)

    # Connect exit signals.
    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    # Start xApp.
    myXapp.start(e2_node_id, kpm_report_style, ue_ids, metrics)
    # Note: xApp will unsubscribe all active subscriptions at exit.
