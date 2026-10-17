#!/usr/bin/env python3

"""
Intelligent Slice Management xApp for ORAN-SC RIC
Combines KPM monitoring with dynamic PRB allocation for multi-slice networks

Features:
- Real-time monitoring of eMBB, mMTC, and URLLC slices
- Dynamic PRB allocation based on slice-specific KPIs
- Intelligent resource optimization algorithms
- SLA enforcement and violation detection
- Predictive resource allocation
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


class IntelligentSliceMgmtXapp(xAppBase):
    def __init__(self, http_server_port, rmr_port):
        super(IntelligentSliceMgmtXapp, self).__init__(None, http_server_port, rmr_port)
        
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
                "priority": 3,
                "sla": {
                    "max_connection_density": 1000000,  # devices/km²
                    "min_success_rate": 0.99,
                    "max_setup_time": 10,               # seconds
                    "min_battery_efficiency": 0.95
                },
                "prb": {
                    "min": 5,      # Minimum PRB allocation (%)
                    "max": 30,     # Maximum PRB allocation (%)
                    "current": 15, # Current allocation
                    "step": 3      # Adjustment step size
                },
                "metrics": ["RRC.ConnEstabAtt", "RRC.ConnEstabSucc", "PRACH.DetAttempts", 
                           "PRACH.DetSucc", "QosFlow.UEThpDl", "QosFlow.UEThpUl"]
            },
            3: {  # URLLC - Ultra-Reliable Low-Latency Communications
                "name": "URLLC",
                "priority": 1,  # Highest priority
                "sla": {
                    "max_latency": 1,           # ms (ultra-low)
                    "min_reliability": 0.99999, # 99.999% reliability
                    "max_packet_loss": 0.00001,
                    "max_jitter": 0.5           # ms
                },
                "prb": {
                    "min": 10,     # Minimum PRB allocation (%)
                    "max": 50,     # Maximum PRB allocation (%)
                    "current": 25, # Current allocation
                    "step": 5      # Adjustment step size
                },
                "metrics": ["DRB.PacketDelayDl", "DRB.PacketDelayUl", "DRB.PacketLossRateDl", 
                           "DRB.PacketLossRateUl", "QosFlow.UEThpDl", "QosFlow.UEThpUl"]
            }
        }
        
        # Historical data for trend analysis (last 60 seconds)
        self.metrics_history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=60)))
        
        # Resource allocation state
        self.total_prb_pool = 100  # Total PRB pool (%)
        self.last_allocation_time = time.time()
        self.allocation_interval = 5  # seconds
        
        # Performance tracking
        self.sla_violations = defaultdict(int)
        self.allocation_decisions = []
        
        print(f"[SLICE_MGMT] Intelligent Slice Management xApp initialized")
        print(f"[SLICE_MGMT] Configured slices: {list(self.slice_config.keys())}")

    def calculate_slice_score(self, sst, current_metrics):
        """Calculate performance score for a slice based on current metrics and SLA"""
        config = self.slice_config[sst]
        sla = config["sla"]
        score = 1.0
        violations = []
        
        if sst == 1:  # eMBB
            # Throughput performance
            dl_throughput = current_metrics.get("DRB.UEThpDl", 0)
            ul_throughput = current_metrics.get("DRB.UEThpUl", 0)
            latency = current_metrics.get("DRB.PacketDelayDl", 0)
            
            if dl_throughput < sla["min_throughput_dl"]:
                score *= 0.7
                violations.append(f"DL throughput below SLA: {dl_throughput:.2f} < {sla['min_throughput_dl']}")
            
            if ul_throughput < sla["min_throughput_ul"]:
                score *= 0.7
                violations.append(f"UL throughput below SLA: {ul_throughput:.2f} < {sla['min_throughput_ul']}")
                
            if latency > sla["max_latency"]:
                score *= 0.8
                violations.append(f"Latency above SLA: {latency:.2f} > {sla['max_latency']}")
                
        elif sst == 2:  # mMTC
            # Connection success rate
            conn_attempts = current_metrics.get("RRC.ConnEstabAtt", 1)
            conn_success = current_metrics.get("RRC.ConnEstabSucc", 0)
            success_rate = conn_success / conn_attempts if conn_attempts > 0 else 1.0
            
            if success_rate < sla["min_success_rate"]:
                score *= 0.6
                violations.append(f"Connection success rate below SLA: {success_rate:.3f} < {sla['min_success_rate']}")
                
        elif sst == 3:  # URLLC
            # Ultra-low latency and reliability
            latency = current_metrics.get("DRB.PacketDelayDl", 0)
            packet_loss = current_metrics.get("DRB.PacketLossRateDl", 0)
            
            if latency > sla["max_latency"]:
                score *= 0.3  # Severe penalty for URLLC latency violation
                violations.append(f"CRITICAL: URLLC latency violation: {latency:.3f} > {sla['max_latency']}")
                
            if packet_loss > sla["max_packet_loss"]:
                score *= 0.3  # Severe penalty for URLLC packet loss
                violations.append(f"CRITICAL: URLLC packet loss violation: {packet_loss:.5f} > {sla['max_packet_loss']}")
        
        # Log violations
        if violations:
            self.sla_violations[sst] += len(violations)
            print(f"[SLICE_MGMT] SST={sst} SLA violations: {violations}")
            
        return score, violations

    def predict_resource_need(self, sst, current_metrics):
        """Predict future resource needs based on trends"""
        history = self.metrics_history[sst]
        
        # Simple trend analysis - can be enhanced with ML models
        trend_factor = 1.0
        
        if sst == 1:  # eMBB - predict based on throughput trends
            if len(history["DRB.UEThpDl"]) >= 5:
                recent_avg = sum(list(history["DRB.UEThpDl"])[-5:]) / 5
                older_avg = sum(list(history["DRB.UEThpDl"])[:-5]) / max(len(history["DRB.UEThpDl"]) - 5, 1)
                if recent_avg > older_avg * 1.2:  # 20% increase trend
                    trend_factor = 1.3
                elif recent_avg < older_avg * 0.8:  # 20% decrease trend
                    trend_factor = 0.8
                    
        elif sst == 2:  # mMTC - predict based on connection attempts
            if len(history["RRC.ConnEstabAtt"]) >= 5:
                recent_attempts = sum(list(history["RRC.ConnEstabAtt"])[-5:])
                older_attempts = sum(list(history["RRC.ConnEstabAtt"])[:-5])
                if recent_attempts > older_attempts * 1.5:  # Surge in connections
                    trend_factor = 1.4
                    
        elif sst == 3:  # URLLC - conservative prediction, always ensure headroom
            trend_factor = 1.1  # Always maintain 10% headroom for URLLC
            
        return trend_factor

    def optimize_prb_allocation(self, slice_metrics):
        """Advanced PRB allocation optimization algorithm"""
        print(f"\n[SLICE_MGMT] === PRB Optimization Cycle ===")
        
        # Calculate performance scores for all slices
        slice_scores = {}
        slice_needs = {}
        
        for sst in self.slice_config.keys():
            current_metrics = slice_metrics.get(f'sst{sst}', {})
            
            # Calculate performance score
            score, violations = self.calculate_slice_score(sst, current_metrics)
            slice_scores[sst] = score
            
            # Predict resource needs
            trend_factor = self.predict_resource_need(sst, current_metrics)
            
            # Calculate needed allocation adjustment
            current_alloc = self.slice_config[sst]["prb"]["current"]
            priority = self.slice_config[sst]["priority"]
            
            # Need more resources if score is low or trend is increasing
            if score < 0.8 or trend_factor > 1.1:
                # Higher priority slices get more aggressive increases
                adjustment = self.slice_config[sst]["prb"]["step"] * (4 - priority) * (1 / score)
                slice_needs[sst] = min(current_alloc + adjustment, self.slice_config[sst]["prb"]["max"])
            elif score > 0.95 and trend_factor < 0.9:
                # Can reduce allocation if performing well and trend is decreasing
                adjustment = self.slice_config[sst]["prb"]["step"] * priority * 0.5
                slice_needs[sst] = max(current_alloc - adjustment, self.slice_config[sst]["prb"]["min"])
            else:
                slice_needs[sst] = current_alloc
                
            print(f"[SLICE_MGMT] SST={sst} ({self.slice_config[sst]['name']}): "
                  f"Score={score:.3f}, Current={current_alloc}%, Needed={slice_needs[sst]:.1f}%")
        
        # Normalize allocations to fit within total PRB pool
        total_needed = sum(slice_needs.values())
        if total_needed > self.total_prb_pool:
            # Priority-based scaling
            scaling_factor = self.total_prb_pool / total_needed
            for sst in slice_needs.keys():
                priority_weight = (4 - self.slice_config[sst]["priority"]) / 6  # 0.5, 0.33, 0.17
                slice_needs[sst] = slice_needs[sst] * (scaling_factor + priority_weight * (1 - scaling_factor))
            
            # Ensure minimums are met
            for sst in slice_needs.keys():
                slice_needs[sst] = max(slice_needs[sst], self.slice_config[sst]["prb"]["min"])
                
            # Final normalization
            total_after_min = sum(slice_needs.values())
            if total_after_min > self.total_prb_pool:
                excess = total_after_min - self.total_prb_pool
                for sst in sorted(slice_needs.keys(), key=lambda x: self.slice_config[x]["priority"], reverse=True):
                    reduction = min(excess, slice_needs[sst] - self.slice_config[sst]["prb"]["min"])
                    slice_needs[sst] -= reduction
                    excess -= reduction
                    if excess <= 0:
                        break
        
        return slice_needs

    def send_prb_control_message(self, e2_node_id, sst, new_allocation):
        """Send PRB allocation control message to gNB"""
        try:
            ue_id = 0  # Apply to all UEs in the slice
            min_prb = int(new_allocation * 0.8)  # 80% guaranteed
            max_prb = int(new_allocation * 1.2)  # 120% burst capacity
            
            print(f"[SLICE_MGMT] Sending PRB control for SST={sst}: {new_allocation:.1f}% "
                  f"(min={min_prb}%, max={max_prb}%)")
            
            # Send RAN Control message
            self.e2sm_rc.control_slice_level_prb_quota(
                e2_node_id, ue_id, min_prb, max_prb, 
                dedicated_prb_ratio=100, ack_request=1
            )
            
            # Update internal state
            self.slice_config[sst]["prb"]["current"] = new_allocation
            
            # Log decision
            decision = {
                "timestamp": datetime.datetime.now().isoformat(),
                "sst": sst,
                "old_allocation": self.slice_config[sst]["prb"]["current"],
                "new_allocation": new_allocation,
                "reason": "Performance optimization"
            }
            self.allocation_decisions.append(decision)
            
        except Exception as e:
            print(f"[SLICE_MGMT] Error sending PRB control for SST={sst}: {e}")

    def process_kmp_data(self, kmp_data):
        """Process KMP data and trigger resource optimization"""
        timestamp = time.time()
        
        # Store metrics in history
        for sst_key, metrics in kmp_data.items():
            if sst_key.startswith('sst'):
                sst = int(sst_key[3:])  # Extract SST number
                for metric_name, value in metrics.items():
                    self.metrics_history[sst][metric_name].append(value)
        
        # Check if it's time for allocation optimization
        if timestamp - self.last_allocation_time >= self.allocation_interval:
            new_allocations = self.optimize_prb_allocation(kmp_data)
            
            # Apply new allocations
            for sst, new_alloc in new_allocations.items():
                current_alloc = self.slice_config[sst]["prb"]["current"]
                if abs(new_alloc - current_alloc) >= 2:  # Only change if significant difference
                    self.send_prb_control_message(self.e2_node_id, sst, new_alloc)
            
            self.last_allocation_time = timestamp
            
            # Print summary
            total_allocated = sum(new_allocations.values())
            print(f"[SLICE_MGMT] Total PRB allocated: {total_allocated:.1f}%")
            print(f"[SLICE_MGMT] SLA violations - eMBB: {self.sla_violations[1]}, "
                  f"mMTC: {self.sla_violations[2]}, URLLC: {self.sla_violations[3]}")

    def subscription_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg, kmp_report_style, ue_id):
        """Handle KMP subscription callbacks"""
        print(f"\n[SLICE_MGMT] RIC Indication from {e2_agent_id}, Subscription: {subscription_id}")
        
        try:
            # Extract KMP data
            indication_hdr = self.e2sm_kmp.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kmp.extract_meas_data(indication_msg)
            
            print(f"[SLICE_MGMT] Measurement period: {meas_data.get('granulPeriod', 'N/A')} ms")
            
            # Convert to slice-specific metrics
            slice_metrics = {}
            for sst in self.slice_config.keys():
                slice_metrics[f'sst{sst}'] = {}
                
                # Extract metrics for this slice
                for metric in self.slice_config[sst]["metrics"]:
                    value = None
                    if "measData" in meas_data and metric in meas_data["measData"]:
                        value = meas_data["measData"][metric]
                        if isinstance(value, list) and value:
                            value = value[0]
                    elif "ueMeasData" in meas_data:
                        # Aggregate UE-level metrics
                        values = []
                        for ue_id, ue_meas in meas_data["ueMeasData"].items():
                            if metric in ue_meas.get("measData", {}):
                                ue_value = ue_meas["measData"][metric]
                                if isinstance(ue_value, list) and ue_value:
                                    values.append(ue_value[0])
                                else:
                                    values.append(ue_value)
                        if values:
                            value = sum(values) / len(values)  # Average across UEs
                    
                    if value is not None:
                        slice_metrics[f'sst{sst}'][metric] = value
            
            # Process the metrics
            self.process_kmp_data(slice_metrics)
            
        except Exception as e:
            print(f"[SLICE_MGMT] Error processing KMP data: {e}")

    @xAppBase.start_function
    def start(self, e2_node_id, kmp_report_style, ue_ids, metric_names):
        self.e2_node_id = e2_node_id
        
        print(f"[SLICE_MGMT] Starting Intelligent Slice Management xApp")
        print(f"[SLICE_MGMT] E2 Node: {e2_node_id}")
        print(f"[SLICE_MGMT] KMP Report Style: {kmp_report_style}")
        print(f"[SLICE_MGMT] Monitoring metrics: {metric_names}")
        
        # Subscribe to KMP reports
        report_period = 1000  # 1 second
        granul_period = 1000  # 1 second
        
        subscription_callback = lambda agent, sub, hdr, msg: self.subscription_callback(
            agent, sub, hdr, msg, kmp_report_style, None)
        
        try:
            if kmp_report_style == 1:
                self.e2sm_kmp.subscribe_report_service_style_1(
                    e2_node_id, report_period, metric_names, granul_period, subscription_callback)
            elif kmp_report_style == 3:
                # Use style 3 for UE-conditional reporting
                matchingConds = [{'matchingCondChoice': ('testCondInfo', {
                    'testType': ('ul-rSRP', 'true'), 
                    'testExpr': 'lessthan', 
                    'testValue': ('valueInt', 1000)
                })}]
                
                self.e2sm_kmp.subscribe_report_service_style_3(
                    e2_node_id, report_period, matchingConds, metric_names, granul_period, subscription_callback)
            else:
                print(f"[SLICE_MGMT] KMP Report Style {kmp_report_style} not supported")
                return
                
            print(f"[SLICE_MGMT] Successfully subscribed to KMP reports")
            
        except Exception as e:
            print(f"[SLICE_MGMT] Error subscribing to KMP reports: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Intelligent Slice Management xApp')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8094, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4564, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--kmp_ran_func_id", type=int, default=2, help="E2SM KMP RAN function ID")
    parser.add_argument("--rc_ran_func_id", type=int, default=3, help="E2SM RC RAN function ID")
    parser.add_argument("--kmp_report_style", type=int, default=1, help="KMP report style")
    parser.add_argument("--ue_ids", type=str, default='0', help="UE ID")
    parser.add_argument("--metrics", type=str, 
                       default='DRB.UEThpDl,DRB.UEThpUl,RRU.PrbUsedDl,RRU.PrbUsedUl,DRB.PacketDelayDl,RRC.ConnEstabAtt,RRC.ConnEstabSucc', 
                       help="Metrics name as comma-separated string")

    args = parser.parse_args()
    
    e2_node_id = args.e2_node_id
    ue_ids = list(map(int, args.ue_ids.split(",")))
    kmp_report_style = args.kmp_report_style
    metrics = args.metrics.split(",")

    # Create Intelligent Slice Management xApp (following simple_xapp pattern)
    xapp = IntelligentSliceMgmtXapp(args.http_server_port, args.rmr_port)
    xapp.e2sm_kmp.set_ran_func_id(args.kmp_ran_func_id)
    xapp.e2sm_rc.set_ran_func_id(args.rc_ran_func_id)

    # Connect exit signals
    signal.signal(signal.SIGQUIT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)
    signal.signal(signal.SIGINT, xapp.signal_handler)

    # Start xApp
    xapp.start(e2_node_id, kmp_report_style, ue_ids, metrics)