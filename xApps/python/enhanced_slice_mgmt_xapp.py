#!/usr/bin/env python3

"""
Enhanced Intelligent Slice Management xApp for ORAN-SC RIC
Implements E2SM-KPM monitoring and E2SM-RC control for dynamic PRB allocation

Key Features:
- Real-time URLLC RTT monitoring via E2SM-KPM
- Dynamic PRB allocation control via E2SM-RC
- Logic: if URLLC RTT > threshold or PRB load high, raise URLLC MinRatio / lower eMBB MaxRatio
- Automatic reversion when URLLC is idle
"""

import argparse
import signal
import json
import time
import threading
from collections import deque, defaultdict
from lib.xAppBase import xAppBase
from ricxappframe.xapp_frame import rmr
import datetime


class EnhancedSliceMgmtXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(EnhancedSliceMgmtXapp, self).__init__(config, http_server_port, rmr_port)
        
        # Enhanced slice configuration for E2SM-KPM/RC control
        self.slice_config = {
            1: {  # eMBB - Enhanced Mobile Broadband
                "name": "eMBB",
                "sst": 1,
                "priority": 6,
                "scheduler_weight": 40,
                "prb_allocation": {
                    "min_ratio": 25,     # Minimum PRB allocation (%)
                    "max_ratio": 70,     # Maximum PRB allocation (%)
                    "dedicated_ratio": 40, # Current dedicated allocation
                    "adjustment_step": 5   # PRB adjustment step
                },
                "kpi_thresholds": {
                    "min_throughput_dl": 50,    # Mbps
                    "max_prb_load": 80,         # % PRB utilization threshold
                    "target_latency": 15        # ms target latency
                },
                "metrics": ["DRB.UEThpDl", "DRB.UEThpUl", "RRU.PrbUsedDl", "RRU.PrbUsedUl", 
                           "DRB.PacketDelayDl", "QosFlow.PrbUsedDl"]
            },
            2: {  # URLLC - Ultra-Reliable Low-Latency Communications
                "name": "URLLC",
                "sst": 2,
                "priority": 2,  # Highest priority
                "scheduler_weight": 30,
                "prb_allocation": {
                    "min_ratio": 15,     # Minimum guaranteed PRBs
                    "max_ratio": 50,     # Maximum PRBs when needed
                    "dedicated_ratio": 25, # Current dedicated allocation
                    "adjustment_step": 10  # Larger steps for urgent adjustments
                },
                "kpi_thresholds": {
                    "max_rtt": 1.0,           # ms - Critical RTT threshold
                    "target_rtt": 0.5,        # ms - Target RTT
                    "max_prb_load": 90,       # % PRB utilization threshold
                    "min_reliability": 99.999  # % reliability requirement
                },
                "metrics": ["DRB.PacketDelayDl", "DRB.PacketDelayUl", "RRU.PrbUsedDl", "RRU.PrbUsedUl",
                           "DRB.PacketLossRateDl", "QosFlow.RTT", "L1M.BLER"]
            },
            3: {  # mMTC - Massive Machine Type Communications
                "name": "mMTC",
                "sst": 3,
                "priority": 9,
                "scheduler_weight": 30,
                "prb_allocation": {
                    "min_ratio": 10,     # Minimum PRB allocation (%)
                    "max_ratio": 25,     # Maximum PRB allocation (%)
                    "dedicated_ratio": 15, # Current allocation
                    "adjustment_step": 3   # Small adjustment steps
                },
                "kpi_thresholds": {
                    "max_connection_density": 1000000,  # devices/km²
                    "min_success_rate": 0.99,
                    "max_setup_time": 10               # seconds
                },
                "metrics": ["RRC.ConnEstabAtt", "RRC.ConnEstabSucc", "PRACH.DetAttempts", 
                           "RRU.PrbUsedDl", "RRU.PrbUsedUl"]
            }
        }
        
        # E2SM-RC Control Parameters
        self.control_config = {
            "urllc_rtt_threshold": 1.0,      # ms - RTT threshold for URLLC
            "prb_load_threshold": 85,        # % - PRB load threshold
            "control_interval": 1.0,         # seconds - Control loop interval
            "revert_timeout": 10.0,          # seconds - Time to revert when idle
            "adjustment_cooldown": 2.0       # seconds - Cooldown between adjustments
        }
        
        # Control state tracking
        self.control_state = {
            "last_adjustment_time": 0,
            "urllc_high_priority_mode": False,
            "emergency_mode_start": 0,
            "pending_reverts": []
        }
        
        # KPI monitoring state
        self.current_kpis = {
            1: {"rtt": 0, "prb_load": 0, "throughput": 0},  # eMBB
            2: {"rtt": 0, "prb_load": 0, "throughput": 0},  # URLLC
            3: {"rtt": 0, "prb_load": 0, "throughput": 0}   # mMTC
        }
        
        # Historical data storage for trend analysis
        self.history_window = 30  # Keep last 30 measurements
        self.slice_metrics_history = {
            1: defaultdict(lambda: deque(maxlen=self.history_window)),
            2: defaultdict(lambda: deque(maxlen=self.history_window)),
            3: defaultdict(lambda: deque(maxlen=self.history_window))
        }
        
        # Slice status tracking
        self.slice_status = {
            1: {"active": True, "last_update": None},
            2: {"active": True, "last_update": None},
            3: {"active": True, "last_update": None}
        }
        
        print(f"[SLICE_MGMT] Enhanced Slice Management xApp initialized")
        print(f"[SLICE_MGMT] URLLC RTT Threshold: {self.control_config['urllc_rtt_threshold']}ms")
        print(f"[SLICE_MGMT] PRB Load Threshold: {self.control_config['prb_load_threshold']}%")

    def send_e2sm_rc_control(self, e2_node_id, slice_id, control_action, parameters):
        """
        Send E2SM-RC control message to adjust slice PRB allocation
        """
        try:
            print(f"[RC_CONTROL] Sending control to E2 Node {e2_node_id} for slice {slice_id}")
            print(f"[RC_CONTROL] Action: {control_action}, Parameters: {parameters}")
            
            control_msg = {
                "ran_function_id": 3,  # E2SM-RC function ID
                "ric_control_header": {
                    "control_message_type": "slice_prb_allocation",
                    "target_slice_id": slice_id,
                    "timestamp": datetime.datetime.now().isoformat()
                },
                "ric_control_message": {
                    "action": control_action,
                    "slice_config": {
                        "sst": self.slice_config[slice_id]["sst"],
                        "min_prb_ratio": parameters.get("min_ratio", self.slice_config[slice_id]["prb_allocation"]["min_ratio"]),
                        "max_prb_ratio": parameters.get("max_ratio", self.slice_config[slice_id]["prb_allocation"]["max_ratio"]),
                        "dedicated_prb_ratio": parameters.get("dedicated_ratio", self.slice_config[slice_id]["prb_allocation"]["dedicated_ratio"]),
                        "scheduler_weight": parameters.get("scheduler_weight", self.slice_config[slice_id]["scheduler_weight"])
                    }
                }
            }
            
            # Send control via E2SM-RC
            control_result = self.e2sm_rc.send_control_message(
                e2_node_id=e2_node_id,
                control_message=json.dumps(control_msg)
            )
            
            if control_result:
                print(f"[RC_CONTROL] Successfully sent control for slice {slice_id}")
                # Update local slice configuration
                if "min_ratio" in parameters:
                    self.slice_config[slice_id]["prb_allocation"]["min_ratio"] = parameters["min_ratio"]
                if "max_ratio" in parameters:
                    self.slice_config[slice_id]["prb_allocation"]["max_ratio"] = parameters["max_ratio"]
                if "dedicated_ratio" in parameters:
                    self.slice_config[slice_id]["prb_allocation"]["dedicated_ratio"] = parameters["dedicated_ratio"]
                    
                return True
            else:
                print(f"[RC_CONTROL] Failed to send control for slice {slice_id}")
                return False
                
        except Exception as e:
            print(f"[RC_CONTROL] Error sending control: {e}")
            return False

    def update_current_kpis(self, slice_id, slice_data):
        """
        Update current KPI values for slice
        """
        try:
            # Extract key KPIs based on slice type
            if slice_id == 2:  # URLLC
                # Focus on RTT and latency metrics
                if 'DRB.PacketDelayDl' in slice_data:
                    self.current_kpis[slice_id]['rtt'] = slice_data['DRB.PacketDelayDl']
                elif 'QosFlow.RTT' in slice_data:
                    self.current_kpis[slice_id]['rtt'] = slice_data['QosFlow.RTT']
                    
                if 'RRU.PrbUsedDl' in slice_data:
                    self.current_kpis[slice_id]['prb_load'] = slice_data['RRU.PrbUsedDl']
                    
            elif slice_id == 1:  # eMBB
                # Focus on throughput and PRB usage
                if 'DRB.UEThpDl' in slice_data:
                    self.current_kpis[slice_id]['throughput'] = slice_data['DRB.UEThpDl']
                    
                if 'RRU.PrbUsedDl' in slice_data:
                    self.current_kpis[slice_id]['prb_load'] = slice_data['RRU.PrbUsedDl']
                    
            elif slice_id == 3:  # mMTC
                # Focus on connection count and basic metrics
                if 'RRU.PrbUsedDl' in slice_data:
                    self.current_kpis[slice_id]['prb_load'] = slice_data['RRU.PrbUsedDl']
                    
        except Exception as e:
            print(f"[KPI_UPDATE] Error updating KPIs for slice {slice_id}: {e}")

    def execute_slice_control_logic(self, e2_node_id, timestamp):
        """
        Main control logic: Monitor URLLC RTT and adjust PRB allocations
        Logic: if URLLC RTT > threshold or PRB load high, raise URLLC MinRatio / lower eMBB MaxRatio
        """
        try:
            current_time = time.time()
            
            # Check cooldown period
            if (current_time - self.control_state["last_adjustment_time"]) < self.control_config["adjustment_cooldown"]:
                return
            
            # Get current URLLC metrics
            urllc_rtt = self.current_kpis[2]['rtt']  # URLLC RTT
            urllc_prb_load = self.current_kpis[2]['prb_load']  # URLLC PRB load
            embb_prb_load = self.current_kpis[1]['prb_load']   # eMBB PRB load
            
            print(f"[CONTROL_LOGIC] URLLC RTT: {urllc_rtt:.3f}ms, PRB Load: {urllc_prb_load:.1f}%")
            print(f"[CONTROL_LOGIC] eMBB PRB Load: {embb_prb_load:.1f}%")
            
            # Condition 1: URLLC RTT exceeds threshold
            rtt_violation = urllc_rtt > self.control_config["urllc_rtt_threshold"]
            
            # Condition 2: High PRB load detected
            high_prb_load = (urllc_prb_load > self.control_config["prb_load_threshold"] or 
                           embb_prb_load > self.control_config["prb_load_threshold"])
            
            if rtt_violation or high_prb_load:
                print(f"[CONTROL_LOGIC] Violation detected - RTT: {rtt_violation}, PRB Load: {high_prb_load}")
                self.handle_resource_contention(e2_node_id)
                self.control_state["last_adjustment_time"] = current_time
                
            elif self.control_state["urllc_high_priority_mode"]:
                # Check if we can revert to normal operation
                idle_time = current_time - self.control_state["emergency_mode_start"]
                if idle_time > self.control_config["revert_timeout"] and urllc_rtt < self.control_config["urllc_rtt_threshold"] * 0.8:
                    print(f"[CONTROL_LOGIC] URLLC performance stable, reverting to normal operation")
                    self.revert_to_normal_operation(e2_node_id)
                    self.control_state["last_adjustment_time"] = current_time
            
        except Exception as e:
            print(f"[CONTROL_LOGIC] Error in control logic: {e}")

    def handle_resource_contention(self, e2_node_id):
        """
        Handle resource contention by prioritizing URLLC
        Raise URLLC MinRatio and lower eMBB MaxRatio
        """
        try:
            print(f"[RESOURCE_CTRL] Handling resource contention - Prioritizing URLLC")
            
            # Get current allocations
            urllc_config = self.slice_config[2]["prb_allocation"]
            embb_config = self.slice_config[1]["prb_allocation"]
            
            # Calculate new URLLC allocation (increase minimum)
            new_urllc_min = min(urllc_config["min_ratio"] + urllc_config["adjustment_step"], 
                               urllc_config["max_ratio"])
            
            # Calculate new eMBB allocation (decrease maximum)
            new_embb_max = max(embb_config["max_ratio"] - embb_config["adjustment_step"], 
                              embb_config["min_ratio"])
            
            # Ensure total doesn't exceed 100%
            if new_urllc_min + self.slice_config[3]["prb_allocation"]["min_ratio"] + embb_config["min_ratio"] > 100:
                print(f"[RESOURCE_CTRL] Allocation constraint violation, adjusting...")
                new_urllc_min = urllc_config["min_ratio"]  # Keep current if constraint violated
            
            # Send control messages
            urllc_success = self.send_e2sm_rc_control(
                e2_node_id, 2, "increase_priority", 
                {"min_ratio": new_urllc_min, "scheduler_weight": 50}
            )
            
            embb_success = self.send_e2sm_rc_control(
                e2_node_id, 1, "decrease_priority", 
                {"max_ratio": new_embb_max, "scheduler_weight": 20}
            )
            
            if urllc_success and embb_success:
                self.control_state["urllc_high_priority_mode"] = True
                self.control_state["emergency_mode_start"] = time.time()
                print(f"[RESOURCE_CTRL] Successfully adjusted allocations - URLLC min: {new_urllc_min}%, eMBB max: {new_embb_max}%")
            
        except Exception as e:
            print(f"[RESOURCE_CTRL] Error handling resource contention: {e}")

    def revert_to_normal_operation(self, e2_node_id):
        """
        Revert PRB allocations to normal operation when URLLC is idle
        """
        try:
            print(f"[RESOURCE_CTRL] Reverting to normal PRB allocation")
            
            # Restore default allocations
            urllc_success = self.send_e2sm_rc_control(
                e2_node_id, 2, "normal_operation", 
                {"min_ratio": 15, "max_ratio": 50, "dedicated_ratio": 25, "scheduler_weight": 30}
            )
            
            embb_success = self.send_e2sm_rc_control(
                e2_node_id, 1, "normal_operation", 
                {"min_ratio": 25, "max_ratio": 70, "dedicated_ratio": 40, "scheduler_weight": 40}
            )
            
            if urllc_success and embb_success:
                self.control_state["urllc_high_priority_mode"] = False
                self.control_state["emergency_mode_start"] = 0
                print(f"[RESOURCE_CTRL] Successfully reverted to normal operation")
            
        except Exception as e:
            print(f"[RESOURCE_CTRL] Error reverting to normal operation: {e}")

    def process_kmp_data(self, e2_node_id, subscription_id, indication_hdr, indication_msg, kmp_report_style, ue_ids):
        """
        Enhanced KMP data processing with real-time slice control logic
        """
        try:
            print(f"[SLICE_MGMT] Processing KMP data for E2 Node: {e2_node_id}")
            
            # Parse KMP indication data
            timestamp = datetime.datetime.now()
            measurements = self.parse_kmp_measurements(indication_msg)
            
            # Process measurements for each slice
            for slice_id in [1, 2, 3]:  # eMBB, URLLC, mMTC
                slice_data = self.extract_slice_metrics(measurements, slice_id)
                
                # Update current KPIs
                self.update_current_kpis(slice_id, slice_data)
                
                # Update slice metrics history
                for metric_name, value in slice_data.items():
                    self.slice_metrics_history[slice_id][metric_name].append({
                        'timestamp': timestamp,
                        'value': value
                    })
                
                # Update slice status
                self.slice_status[slice_id].update({
                    'last_update': timestamp,
                    'current_metrics': slice_data
                })
            
            # Execute intelligent control logic
            self.execute_slice_control_logic(e2_node_id, timestamp)
                
        except Exception as e:
            print(f"[SLICE_MGMT] Error processing KMP data: {e}")

    def parse_kmp_measurements(self, indication_msg):
        """
        Parse E2SM-KPM indication message
        """
        try:
            # Placeholder for actual KMP parsing logic
            # This would parse the ASN.1 encoded KMP indication message
            measurements = {
                'DRB.UEThpDl': 45.5,      # Mbps
                'DRB.UEThpUl': 12.3,      # Mbps
                'RRU.PrbUsedDl': 67.8,    # %
                'RRU.PrbUsedUl': 34.2,    # %
                'DRB.PacketDelayDl': 0.8, # ms (URLLC RTT)
                'QosFlow.RTT': 0.9,       # ms
                'DRB.PacketLossRateDl': 0.001,
                'RRC.ConnEstabSucc': 99.5  # %
            }
            return measurements
        except Exception as e:
            print(f"[KMP_PARSE] Error parsing KMP measurements: {e}")
            return {}

    def extract_slice_metrics(self, measurements, slice_id):
        """
        Extract relevant metrics for specific slice
        """
        try:
            slice_metrics = {}
            target_metrics = self.slice_config[slice_id]["metrics"]
            
            for metric in target_metrics:
                if metric in measurements:
                    slice_metrics[metric] = measurements[metric]
                    
            return slice_metrics
        except Exception as e:
            print(f"[SLICE_EXTRACT] Error extracting slice {slice_id} metrics: {e}")
            return {}

    def start(self, e2_node_id, kmp_report_style, ue_ids, metrics):
        """
        Start the enhanced slice management xApp
        """
        try:
            print(f"[SLICE_MGMT] Starting Enhanced Slice Management with E2SM-KMP/RC")
            
            # Subscribe to E2SM-KPM reports
            self.subscribe_kmp_reports(e2_node_id, kmp_report_style, ue_ids, metrics)
            
            # Start control loop
            control_thread = threading.Thread(
                target=self.control_loop,
                args=(e2_node_id,),
                daemon=True
            )
            control_thread.start()
            
            # Keep xApp running - use threading to maintain execution
            while True:
                time.sleep(1)
            
        except Exception as e:
            print(f"[SLICE_MGMT] Error starting xApp: {e}")

    def control_loop(self, e2_node_id):
        """
        Continuous control loop for monitoring and adjustment
        """
        while True:
            try:
                time.sleep(self.control_config["control_interval"])
                
                # Periodic status reporting
                if int(time.time()) % 30 == 0:  # Every 30 seconds
                    self.report_slice_status()
                    
            except Exception as e:
                print(f"[CONTROL_LOOP] Error in control loop: {e}")

    def report_slice_status(self):
        """
        Report current slice status and control state
        """
        try:
            print(f"\n[STATUS_REPORT] === Slice Management Status ===")
            for slice_id, config in self.slice_config.items():
                kpis = self.current_kpis[slice_id]
                prb_config = config["prb_allocation"]
                
                print(f"[STATUS] {config['name']} (SST={config['sst']}):")
                print(f"  RTT: {kpis['rtt']:.3f}ms, PRB Load: {kpis['prb_load']:.1f}%")
                print(f"  PRB Allocation - Min: {prb_config['min_ratio']}%, Max: {prb_config['max_ratio']}%")
            
            print(f"[STATUS] Control State:")
            print(f"  URLLC High Priority Mode: {self.control_state['urllc_high_priority_mode']}")
            print(f"  Last Adjustment: {time.time() - self.control_state['last_adjustment_time']:.1f}s ago")
            print(f"[STATUS_REPORT] =====================================\n")
            
        except Exception as e:
            print(f"[STATUS_REPORT] Error in status report: {e}")

    def subscribe_kmp_reports(self, e2_node_id, kmp_report_style, ue_ids, metrics):
        """
        Subscribe to E2SM-KPM reports
        """
        try:
            subscription_callback = lambda node_id, sub_id, hdr, msg, style, ues: self.process_kmp_data(
                node_id, sub_id, hdr, msg, style, ues
            )
            
            if kmp_report_style == 1:
                self.e2sm_kmp.subscribe_report_service_style_1(
                    e2_node_id, 1000, ue_ids, metrics, subscription_callback
                )
            elif kmp_report_style == 2:
                self.e2sm_kmp.subscribe_report_service_style_2(
                    e2_node_id, 1000, ue_ids, metrics, subscription_callback
                )
            else:
                print(f"[SLICE_MGMT] KMP Report Style {kmp_report_style} not supported")
                return
                
            print(f"[SLICE_MGMT] Successfully subscribed to KMP reports for RTT and PRB monitoring")
            
        except Exception as e:
            print(f"[SLICE_MGMT] Error subscribing to KMP reports: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Enhanced Slice Management xApp with E2SM-KMP/RC')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8094, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4564, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--kmp_ran_func_id", type=int, default=2, help="E2SM KMP RAN function ID")
    parser.add_argument("--rc_ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--kmp_report_style", type=int, default=1, help="KMP report style")
    parser.add_argument("--ue_ids", type=str, default='0', help="UE ID")
    parser.add_argument("--metrics", type=str, 
                       default='DRB.UEThpDl,DRB.UEThpUl,RRU.PrbUsedDl,RRU.PrbUsedUl,DRB.PacketDelayDl,DRB.PacketDelayUl,QosFlow.RTT,RRC.ConnEstabAtt,RRC.ConnEstabSucc,DRB.PacketLossRateDl,DRB.PacketLossRateUl,L1M.BLER',
                       help="Metrics name as comma-separated string")

    args = parser.parse_args()
    
    e2_node_id = args.e2_node_id
    ue_ids = list(map(int, args.ue_ids.split(",")))
    kmp_report_style = args.kmp_report_style
    metrics = args.metrics.split(",")

    # Create Enhanced Slice Management xApp
    xapp = EnhancedSliceMgmtXapp(args.config, args.http_server_port, args.rmr_port)
    
    # Initialize E2SM service models
    if hasattr(xapp, 'e2sm_kmp') and xapp.e2sm_kmp:
        xapp.e2sm_kmp.set_ran_func_id(args.kmp_ran_func_id)
    if hasattr(xapp, 'e2sm_rc') and xapp.e2sm_rc:
        xapp.e2sm_rc.set_ran_func_id(args.rc_ran_func_id)

    # Connect exit signals
    signal.signal(signal.SIGQUIT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)
    signal.signal(signal.SIGINT, xapp.signal_handler)

    print(f"[SLICE_MGMT] Starting Enhanced Slice Management xApp with E2SM-KMP/RC control")
    print(f"  E2 Node ID: {e2_node_id}")
    print(f"  KMP RAN Function ID: {args.kmp_ran_func_id}")
    print(f"  RC RAN Function ID: {args.rc_ran_func_id}")
    print(f"  Metrics: {metrics}")
    print(f"  Report Style: {kmp_report_style}")
    print(f"  URLLC RTT Threshold: {xapp.control_config['urllc_rtt_threshold']}ms")
    print(f"  PRB Load Threshold: {xapp.control_config['prb_load_threshold']}%")
    print(f"  Control Interval: {xapp.control_config['control_interval']}s")

    # Start xApp
    xapp.start(e2_node_id, kmp_report_style, ue_ids, metrics)