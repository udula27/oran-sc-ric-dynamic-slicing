#!/usr/bin/env python3

"""
Intelligent Slice Management xApp for ORAN-SC RIC
- Monitors KPIs from all 3 network slices (eMBB, mMTC, URLLC)
- Dynamically allocates PRB resources based on slice-specific requirements
- Implements intelligent algorithms for resource optimization
"""

import argparse
import signal
import json
import time
import threading
import logging
from datetime import datetime
from lib.xAppBase import xAppBase

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SliceManagementXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(SliceManagementXapp, self).__init__(config, http_server_port, rmr_port)
        
        # Slice configuration with predefined parameters
        self.slice_config = {
            1: {  # eMBB - Enhanced Mobile Broadband
                "name": "eMBB",
                "priority": 3,
                "min_prb": 20,     # Minimum 20% PRB allocation
                "max_prb": 60,     # Maximum 60% PRB allocation
                "target_throughput_dl": 50,  # Mbps
                "target_throughput_ul": 25,  # Mbps
                "latency_threshold": 50,     # ms
                "bler_threshold": 0.1,       # 10%
                "kpis": ["DRB.UEThpDl", "DRB.UEThpUl", "RRU.PrbUsedDl", "RRU.PrbUsedUl", 
                        "PDCP.PacketDelayDl", "RRC.ConnMax", "QosFlow.UEThpDl"]
            },
            2: {  # mMTC - Massive Machine Type Communications
                "name": "mMTC", 
                "priority": 1,
                "min_prb": 5,      # Minimum 5% PRB allocation
                "max_prb": 25,     # Maximum 25% PRB allocation
                "target_connections": 1000,  # Max simultaneous connections
                "energy_efficiency": 0.95,   # Target efficiency
                "access_success_rate": 0.99, # 99%
                "kpis": ["RRC.ConnEstabAtt", "RRC.ConnEstabSucc", "PRACH.DetectedPreamble",
                        "MAC.UERACHPreamblesSent", "RRC.ConnMax"]
            },
            3: {  # URLLC - Ultra-Reliable Low-Latency Communications
                "name": "URLLC",
                "priority": 5,
                "min_prb": 15,     # Minimum 15% PRB allocation (guaranteed)
                "max_prb": 40,     # Maximum 40% PRB allocation
                "target_latency": 1,      # 1ms target
                "reliability": 0.99999,   # 99.999%
                "max_packet_loss": 0.001, # 0.1%
                "kpis": ["PDCP.PacketDelayDl", "PDCP.PacketDelayUl", "RLC.PacketLossRateDl",
                        "RLC.PacketLossRateUl", "MAC.SchedFailures"]
            }
        }
        
        # Current PRB allocations (DL, UL)
        self.current_prb_allocation = {
            1: [30, 25],  # eMBB: 30% DL, 25% UL
            2: [15, 20],  # mMTC: 15% DL, 20% UL  
            3: [25, 20]   # URLLC: 25% DL, 20% UL
        }
        
        # Historical metrics for trend analysis
        self.metrics_history = {1: [], 2: [], 3: []}
        self.control_decisions = []
        
        # Control loop parameters
        self.control_interval = 5  # seconds
        self.last_control_time = time.time()
        
        logger.info("Slice Management xApp initialized with intelligent resource allocation")

    def kmp_subscription_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg, kmp_report_style, ue_id):
        """Process KMP indications from gNB"""
        logger.info(f"KMP Indication from {e2_agent_id} (Sub: {subscription_id})")
        
        try:
            # Extract measurement data
            indication_hdr = self.e2sm_kmp.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kmp.extract_meas_data(indication_msg)
            
            timestamp = datetime.now()
            
            # Process metrics for each slice
            slice_metrics = self.extract_slice_metrics(meas_data, timestamp)
            
            # Store historical data
            for sst, metrics in slice_metrics.items():
                if len(self.metrics_history[sst]) > 50:  # Keep last 50 measurements
                    self.metrics_history[sst].pop(0)
                self.metrics_history[sst].append({
                    'timestamp': timestamp,
                    'metrics': metrics
                })
            
            # Trigger intelligent resource allocation if needed
            if time.time() - self.last_control_time > self.control_interval:
                self.intelligent_resource_allocation(slice_metrics)
                self.last_control_time = time.time()
                
        except Exception as e:
            logger.error(f"Error processing KMP indication: {e}")

    def extract_slice_metrics(self, meas_data, timestamp):
        """Extract and organize metrics by slice"""
        slice_metrics = {1: {}, 2: {}, 3: {}}
        
        # Process cell-level measurements
        if "measData" in meas_data:
            for metric_name, values in meas_data["measData"].items():
                value = values[0] if isinstance(values, list) and values else values
                
                # Assign metrics to appropriate slices based on metric type
                if metric_name in ["DRB.UEThpDl", "DRB.UEThpUl", "QosFlow.UEThpDl"]:
                    slice_metrics[1][metric_name] = value  # eMBB
                elif metric_name in ["RRC.ConnEstabAtt", "RRC.ConnEstabSucc", "PRACH.DetectedPreamble"]:
                    slice_metrics[2][metric_name] = value  # mMTC
                elif metric_name in ["PDCP.PacketDelayDl", "PDCP.PacketDelayUl", "RLC.PacketLossRateDl"]:
                    slice_metrics[3][metric_name] = value  # URLLC
                else:
                    # Common metrics shared across slices
                    for sst in [1, 2, 3]:
                        slice_metrics[sst][metric_name] = value
        
        # Process UE-level measurements
        if "ueMeasData" in meas_data:
            for ue_id, ue_data in meas_data["ueMeasData"].items():
                # Associate UE with slice based on UE ID or other criteria
                # For now, distribute UEs across slices for demonstration
                target_slice = (int(ue_id) % 3) + 1
                
                if "measData" in ue_data:
                    for metric_name, values in ue_data["measData"].items():
                        value = values[0] if isinstance(values, list) and values else values
                        slice_metrics[target_slice][f"UE_{ue_id}_{metric_name}"] = value
        
        return slice_metrics

    def intelligent_resource_allocation(self, current_metrics):
        """Intelligent algorithm for dynamic PRB allocation"""
        logger.info("=== Intelligent Resource Allocation Analysis ===")
        
        new_allocations = {}
        total_dl_allocation = 0
        total_ul_allocation = 0
        
        # Analyze each slice
        for sst, config in self.slice_config.items():
            metrics = current_metrics.get(sst, {})
            current_alloc = self.current_prb_allocation[sst]
            
            # Calculate slice performance score
            performance_score = self.calculate_slice_performance(sst, metrics, config)
            
            # Determine new allocation based on performance and priority
            new_dl, new_ul = self.calculate_optimal_allocation(sst, performance_score, metrics, config)
            
            new_allocations[sst] = [new_dl, new_ul]
            total_dl_allocation += new_dl
            total_ul_allocation += new_ul
            
            logger.info(f"Slice {config['name']} (SST={sst}):")
            logger.info(f"  Performance Score: {performance_score:.2f}")
            logger.info(f"  Current Allocation: DL={current_alloc[0]}%, UL={current_alloc[1]}%")
            logger.info(f"  Proposed Allocation: DL={new_dl}%, UL={new_ul}%")
        
        # Normalize allocations if they exceed 100%
        if total_dl_allocation > 100 or total_ul_allocation > 100:
            logger.warning("Total allocation exceeds 100%, normalizing...")
            new_allocations = self.normalize_allocations(new_allocations)
        
        # Apply new allocations
        self.apply_resource_allocations(new_allocations)

    def calculate_slice_performance(self, sst, metrics, config):
        """Calculate performance score for a slice (0-1, higher is better)"""
        score = 0.5  # Baseline score
        
        if sst == 1:  # eMBB
            # Throughput-based scoring
            dl_throughput = metrics.get("DRB.UEThpDl", 0)
            ul_throughput = metrics.get("DRB.UEThpUl", 0)
            
            if dl_throughput > config["target_throughput_dl"]:
                score += 0.3
            elif dl_throughput > config["target_throughput_dl"] * 0.7:
                score += 0.1
            else:
                score -= 0.2
                
            if ul_throughput > config["target_throughput_ul"]:
                score += 0.2
            elif ul_throughput > config["target_throughput_ul"] * 0.7:
                score += 0.05
            else:
                score -= 0.15
                
        elif sst == 2:  # mMTC
            # Connection success rate based scoring
            conn_attempts = metrics.get("RRC.ConnEstabAtt", 1)
            conn_success = metrics.get("RRC.ConnEstabSucc", 0)
            success_rate = conn_success / conn_attempts if conn_attempts > 0 else 0
            
            if success_rate > config["access_success_rate"]:
                score += 0.4
            elif success_rate > config["access_success_rate"] * 0.9:
                score += 0.1
            else:
                score -= 0.3
                
        elif sst == 3:  # URLLC
            # Latency and reliability based scoring
            dl_delay = metrics.get("PDCP.PacketDelayDl", float('inf'))
            ul_delay = metrics.get("PDCP.PacketDelayUl", float('inf'))
            packet_loss = metrics.get("RLC.PacketLossRateDl", 1.0)
            
            if dl_delay < config["target_latency"]:
                score += 0.3
            elif dl_delay < config["target_latency"] * 2:
                score += 0.1
            else:
                score -= 0.3
                
            if packet_loss < config["max_packet_loss"]:
                score += 0.2
            else:
                score -= 0.2
        
        return max(0, min(1, score))  # Clamp between 0 and 1

    def calculate_optimal_allocation(self, sst, performance_score, metrics, config):
        """Calculate optimal PRB allocation for a slice"""
        current_dl, current_ul = self.current_prb_allocation[sst]
        
        # Base allocation on priority and performance
        priority_factor = config["priority"] / 5.0  # Normalize priority
        
        # Calculate adjustment based on performance
        if performance_score < 0.3:
            # Poor performance, increase allocation
            adjustment = 1.5
        elif performance_score < 0.6:
            # Average performance, slight increase
            adjustment = 1.2
        elif performance_score > 0.8:
            # Good performance, can reduce allocation
            adjustment = 0.8
        else:
            # Maintain current allocation
            adjustment = 1.0
        
        # Calculate new allocations
        new_dl = min(config["max_prb"], max(config["min_prb"], 
                    int(current_dl * adjustment * priority_factor)))
        new_ul = min(config["max_prb"], max(config["min_prb"], 
                    int(current_ul * adjustment * priority_factor)))
        
        return new_dl, new_ul

    def normalize_allocations(self, allocations):
        """Normalize allocations to ensure they don't exceed 100%"""
        total_dl = sum(alloc[0] for alloc in allocations.values())
        total_ul = sum(alloc[1] for alloc in allocations.values())
        
        if total_dl > 100:
            dl_factor = 100.0 / total_dl
            for sst in allocations:
                allocations[sst][0] = int(allocations[sst][0] * dl_factor)
        
        if total_ul > 100:
            ul_factor = 100.0 / total_ul
            for sst in allocations:
                allocations[sst][1] = int(allocations[sst][1] * ul_factor)
        
        return allocations

    def apply_resource_allocations(self, new_allocations):
        """Send RAN Control messages to apply new PRB allocations"""
        for sst, (new_dl, new_ul) in new_allocations.items():
            current_dl, current_ul = self.current_prb_allocation[sst]
            
            if new_dl != current_dl or new_ul != current_ul:
                logger.info(f"Applying new allocation for {self.slice_config[sst]['name']}: "
                           f"DL {current_dl}→{new_dl}%, UL {current_ul}→{new_ul}%")
                
                try:
                    # Send E2SM-RC control message for PRB allocation
                    ue_id = 0  # Cell-level control
                    control_message = {
                        "slice_id": sst,
                        "prb_dl": new_dl,
                        "prb_ul": new_ul,
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    # Use E2SM-RC to control slice-level PRB quota
                    self.e2sm_rc.control_slice_level_prb_quota(
                        self.e2_node_id, ue_id, new_dl, new_ul, 
                        dedicated_prb_ratio=100, ack_request=1
                    )
                    
                    # Update current allocation
                    self.current_prb_allocation[sst] = [new_dl, new_ul]
                    
                    # Log control decision
                    self.control_decisions.append({
                        'timestamp': datetime.now().isoformat(),
                        'slice': sst,
                        'old_allocation': [current_dl, current_ul],
                        'new_allocation': [new_dl, new_ul],
                        'reason': 'Performance-based optimization'
                    })
                    
                except Exception as e:
                    logger.error(f"Failed to apply allocation for slice {sst}: {e}")

    @xAppBase.start_function
    def start(self, e2_node_id, kmp_report_style):
        """Start the slice management xApp"""
        self.e2_node_id = e2_node_id
        
        logger.info("=== Starting Intelligent Slice Management xApp ===")
        logger.info(f"E2 Node ID: {e2_node_id}")
        logger.info(f"KMP Report Style: {kmp_report_style}")
        
        # Subscribe to KMP reports for comprehensive slice monitoring
        report_period = 1000  # 1 second
        granul_period = 1000  # 1 second
        
        # Comprehensive metric list for all slices
        all_metrics = []
        for sst, config in self.slice_config.items():
            all_metrics.extend(config["kpis"])
        
        # Remove duplicates
        all_metrics = list(set(all_metrics))
        
        logger.info(f"Subscribing to metrics: {all_metrics}")
        
        # Subscribe based on report style
        if kmp_report_style == 1:
            self.e2sm_kmp.subscribe_report_service_style_1(
                e2_node_id, report_period, all_metrics, granul_period, 
                self.kmp_subscription_callback
            )
        else:
            logger.error(f"KMP Report Style {kmp_report_style} not supported yet")
            return
        
        logger.info("Slice Management xApp started successfully!")
        logger.info("Monitoring slice performance and optimizing resource allocation...")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Intelligent Slice Management xApp')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8093, help="HTTP server listen port")
    parser.add_argument("--rmr_port", type=int, default=4563, help="RMR port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--kmp_ran_func_id", type=int, default=2, help="E2SM-KMP RAN function ID")
    parser.add_argument("--rc_ran_func_id", type=int, default=3, help="E2SM-RC RAN function ID")
    parser.add_argument("--kmp_report_style", type=int, default=1, help="KMP report style")

    args = parser.parse_args()
    
    # Create Slice Management xApp
    slice_xapp = SliceManagementXapp(args.config, args.http_server_port, args.rmr_port)
    slice_xapp.e2sm_kmp.set_ran_func_id(args.kmp_ran_func_id)
    slice_xapp.e2sm_rc.set_ran_func_id(args.rc_ran_func_id)

    # Connect exit signals
    signal.signal(signal.SIGQUIT, slice_xapp.signal_handler)
    signal.signal(signal.SIGTERM, slice_xapp.signal_handler)
    signal.signal(signal.SIGINT, slice_xapp.signal_handler)

    # Start xApp
    slice_xapp.start(args.e2_node_id, args.kmp_report_style)