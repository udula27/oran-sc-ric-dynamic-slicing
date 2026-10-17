#!/usr/bin/env python3

"""
Slice Management Dashboard
Real-time monitoring and visualization of slice performance and resource allocation
"""

import tkinter as tk
from tkinter import ttk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import json
import time
import threading
from collections import defaultdict, deque
import datetime

class SliceManagementDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("ORAN-SC RIC - Intelligent Slice Management Dashboard")
        self.root.geometry("1400x900")
        
        # Data storage
        self.slice_data = {
            1: {"name": "eMBB", "color": "blue", "metrics": deque(maxlen=100), "prb_history": deque(maxlen=100)},
            2: {"name": "mMTC", "color": "green", "metrics": deque(maxlen=100), "prb_history": deque(maxlen=100)},
            3: {"name": "URLLC", "color": "red", "metrics": deque(maxlen=100), "prb_history": deque(maxlen=100)}
        }
        
        self.sla_violations = defaultdict(int)
        self.allocation_history = deque(maxlen=200)
        
        # Create GUI
        self.create_widgets()
        
        # Start data simulation (replace with real data feed)
        self.start_data_simulation()
        
    def create_widgets(self):
        # Create main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Title
        title_label = ttk.Label(main_frame, text="Intelligent Slice Management Dashboard", 
                               font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))
        
        # Slice status frame
        status_frame = ttk.LabelFrame(main_frame, text="Slice Status", padding="10")
        status_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.slice_status_vars = {}
        for i, (sst, info) in enumerate(self.slice_data.items()):
            # Slice name
            ttk.Label(status_frame, text=f"{info['name']} (SST={sst}):", 
                     font=("Arial", 10, "bold")).grid(row=0, column=i*2, padx=(20, 5), sticky=tk.W)
            
            # Status variables
            self.slice_status_vars[sst] = {
                "prb_alloc": tk.StringVar(value="40%"),
                "throughput": tk.StringVar(value="0 Mbps"),
                "latency": tk.StringVar(value="0 ms"),
                "violations": tk.StringVar(value="0"),
                "status": tk.StringVar(value="Normal")
            }
            
            # Status display
            status_text = ttk.Frame(status_frame)
            status_text.grid(row=1, column=i*2, columnspan=2, padx=(20, 20), pady=5, sticky=tk.W)
            
            ttk.Label(status_text, text="PRB Allocation:").grid(row=0, column=0, sticky=tk.W)
            ttk.Label(status_text, textvariable=self.slice_status_vars[sst]["prb_alloc"]).grid(row=0, column=1, padx=(10, 0), sticky=tk.W)
            
            ttk.Label(status_text, text="Throughput:").grid(row=1, column=0, sticky=tk.W)
            ttk.Label(status_text, textvariable=self.slice_status_vars[sst]["throughput"]).grid(row=1, column=1, padx=(10, 0), sticky=tk.W)
            
            ttk.Label(status_text, text="Latency:").grid(row=2, column=0, sticky=tk.W)
            ttk.Label(status_text, textvariable=self.slice_status_vars[sst]["latency"]).grid(row=2, column=1, padx=(10, 0), sticky=tk.W)
            
            ttk.Label(status_text, text="SLA Violations:").grid(row=3, column=0, sticky=tk.W)
            ttk.Label(status_text, textvariable=self.slice_status_vars[sst]["violations"]).grid(row=3, column=1, padx=(10, 0), sticky=tk.W)
            
            ttk.Label(status_text, text="Status:").grid(row=4, column=0, sticky=tk.W)
            status_label = ttk.Label(status_text, textvariable=self.slice_status_vars[sst]["status"])
            status_label.grid(row=4, column=1, padx=(10, 0), sticky=tk.W)
        
        # Charts frame
        charts_frame = ttk.Frame(main_frame)
        charts_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Create matplotlib figures
        self.fig, ((self.ax1, self.ax2), (self.ax3, self.ax4)) = plt.subplots(2, 2, figsize=(14, 8))
        
        # PRB Allocation over time
        self.ax1.set_title("PRB Allocation Over Time")
        self.ax1.set_ylabel("PRB Allocation (%)")
        self.ax1.set_xlabel("Time")
        self.ax1.grid(True)
        
        # Throughput comparison
        self.ax2.set_title("Slice Throughput")
        self.ax2.set_ylabel("Throughput (Mbps)")
        self.ax2.set_xlabel("Time")
        self.ax2.grid(True)
        
        # Latency tracking
        self.ax3.set_title("Slice Latency")
        self.ax3.set_ylabel("Latency (ms)")
        self.ax3.set_xlabel("Time")
        self.ax3.grid(True)
        
        # SLA Violations
        self.ax4.set_title("SLA Violations Count")
        self.ax4.set_ylabel("Number of Violations")
        self.ax4.set_xlabel("Slice")
        
        # Embed matplotlib in tkinter
        self.canvas = FigureCanvasTkAgg(self.fig, charts_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Control buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=3, column=0, columnspan=3, pady=(10, 0))
        
        ttk.Button(button_frame, text="Start xApps", command=self.start_xapps).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="Stop xApps", command=self.stop_xapps).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="Reset Stats", command=self.reset_stats).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="Export Data", command=self.export_data).pack(side=tk.LEFT, padx=(0, 10))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
    def start_data_simulation(self):
        """Simulate real-time data updates (replace with actual data feed)"""
        def update_data():
            while True:
                timestamp = time.time()
                
                # Simulate metrics for each slice
                for sst, info in self.slice_data.items():
                    if sst == 1:  # eMBB
                        throughput = 30 + 20 * np.sin(timestamp / 10) + np.random.normal(0, 5)
                        latency = 15 + 5 * np.sin(timestamp / 15) + np.random.normal(0, 2)
                        prb_alloc = 40 + 10 * np.sin(timestamp / 20) + np.random.normal(0, 3)
                    elif sst == 2:  # mMTC
                        throughput = 5 + 2 * np.sin(timestamp / 12) + np.random.normal(0, 1)
                        latency = 50 + 10 * np.sin(timestamp / 18) + np.random.normal(0, 5)
                        prb_alloc = 15 + 5 * np.sin(timestamp / 25) + np.random.normal(0, 2)
                    else:  # URLLC
                        throughput = 10 + 5 * np.sin(timestamp / 8) + np.random.normal(0, 2)
                        latency = 1 + 0.5 * np.sin(timestamp / 6) + np.random.normal(0, 0.2)
                        prb_alloc = 25 + 8 * np.sin(timestamp / 30) + np.random.normal(0, 3)
                    
                    # Ensure realistic bounds
                    throughput = max(0, throughput)
                    latency = max(0.1, latency)
                    prb_alloc = max(5, min(70, prb_alloc))
                    
                    # Store data
                    info["metrics"].append({
                        "timestamp": timestamp,
                        "throughput": throughput,
                        "latency": latency
                    })
                    info["prb_history"].append({
                        "timestamp": timestamp,
                        "allocation": prb_alloc
                    })
                    
                    # Check for SLA violations
                    if (sst == 1 and throughput < 20) or \
                       (sst == 2 and latency > 100) or \
                       (sst == 3 and latency > 2):
                        self.sla_violations[sst] += 1
                
                # Update GUI in main thread
                self.root.after(0, self.update_gui)
                
                time.sleep(1)  # Update every second
        
        # Start background thread
        thread = threading.Thread(target=update_data, daemon=True)
        thread.start()
    
    def update_gui(self):
        """Update GUI elements with latest data"""
        # Update status displays
        for sst, info in self.slice_data.items():
            if info["metrics"]:
                latest = info["metrics"][-1]
                latest_prb = info["prb_history"][-1] if info["prb_history"] else {"allocation": 0}
                
                self.slice_status_vars[sst]["prb_alloc"].set(f"{latest_prb['allocation']:.1f}%")
                self.slice_status_vars[sst]["throughput"].set(f"{latest['throughput']:.1f} Mbps")
                self.slice_status_vars[sst]["latency"].set(f"{latest['latency']:.1f} ms")
                self.slice_status_vars[sst]["violations"].set(str(self.sla_violations[sst]))
                
                # Determine status
                status = "Normal"
                if sst == 1 and latest['throughput'] < 20:
                    status = "SLA Violation"
                elif sst == 2 and latest['latency'] > 100:
                    status = "SLA Violation"
                elif sst == 3 and latest['latency'] > 2:
                    status = "CRITICAL"
                elif latest['throughput'] < 5:
                    status = "Low Performance"
                
                self.slice_status_vars[sst]["status"].set(status)
        
        # Update charts
        self.update_charts()
    
    def update_charts(self):
        """Update matplotlib charts"""
        # Clear axes
        for ax in [self.ax1, self.ax2, self.ax3]:
            ax.clear()
        
        # PRB Allocation chart
        self.ax1.set_title("PRB Allocation Over Time")
        self.ax1.set_ylabel("PRB Allocation (%)")
        self.ax1.grid(True)
        
        for sst, info in self.slice_data.items():
            if info["prb_history"]:
                times = [datetime.datetime.fromtimestamp(item["timestamp"]) for item in info["prb_history"]]
                allocations = [item["allocation"] for item in info["prb_history"]]
                self.ax1.plot(times, allocations, label=f"{info['name']} (SST={sst})", 
                             color=info["color"], linewidth=2)
        
        self.ax1.legend()
        self.ax1.tick_params(axis='x', rotation=45)
        
        # Throughput chart
        self.ax2.set_title("Slice Throughput")
        self.ax2.set_ylabel("Throughput (Mbps)")
        self.ax2.grid(True)
        
        for sst, info in self.slice_data.items():
            if info["metrics"]:
                times = [datetime.datetime.fromtimestamp(item["timestamp"]) for item in info["metrics"]]
                throughputs = [item["throughput"] for item in info["metrics"]]
                self.ax2.plot(times, throughputs, label=f"{info['name']} (SST={sst})", 
                             color=info["color"], linewidth=2)
        
        self.ax2.legend()
        self.ax2.tick_params(axis='x', rotation=45)
        
        # Latency chart
        self.ax3.set_title("Slice Latency")
        self.ax3.set_ylabel("Latency (ms)")
        self.ax3.grid(True)
        
        for sst, info in self.slice_data.items():
            if info["metrics"]:
                times = [datetime.datetime.fromtimestamp(item["timestamp"]) for item in info["metrics"]]
                latencies = [item["latency"] for item in info["metrics"]]
                self.ax3.plot(times, latencies, label=f"{info['name']} (SST={sst})", 
                             color=info["color"], linewidth=2)
        
        self.ax3.legend()
        self.ax3.tick_params(axis='x', rotation=45)
        
        # SLA Violations bar chart
        self.ax4.clear()
        self.ax4.set_title("SLA Violations Count")
        self.ax4.set_ylabel("Number of Violations")
        
        slices = [f"{self.slice_data[sst]['name']}\n(SST={sst})" for sst in [1, 2, 3]]
        violations = [self.sla_violations[sst] for sst in [1, 2, 3]]
        colors = [self.slice_data[sst]["color"] for sst in [1, 2, 3]]
        
        bars = self.ax4.bar(slices, violations, color=colors, alpha=0.7)
        
        # Add value labels on bars
        for bar, value in zip(bars, violations):
            height = bar.get_height()
            self.ax4.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                         f'{value}', ha='center', va='bottom')
        
        # Refresh canvas
        self.canvas.draw()
    
    def start_xapps(self):
        """Start xApps via deployment script"""
        import subprocess
        try:
            subprocess.run(["/home/manamperige/Documents/srsRAN_Project/oran-sc-ric/deploy_slice_management.sh", "intelligent"], 
                          check=True)
            print("[DASHBOARD] xApps started successfully")
        except subprocess.CalledProcessError as e:
            print(f"[DASHBOARD] Error starting xApps: {e}")
    
    def stop_xapps(self):
        """Stop xApps via deployment script"""
        import subprocess
        try:
            subprocess.run(["/home/manamperige/Documents/srsRAN_Project/oran-sc-ric/deploy_slice_management.sh", "stop"], 
                          check=True)
            print("[DASHBOARD] xApps stopped successfully")
        except subprocess.CalledProcessError as e:
            print(f"[DASHBOARD] Error stopping xApps: {e}")
    
    def reset_stats(self):
        """Reset all statistics"""
        for sst in self.slice_data.keys():
            self.slice_data[sst]["metrics"].clear()
            self.slice_data[sst]["prb_history"].clear()
            self.sla_violations[sst] = 0
        
        self.allocation_history.clear()
        print("[DASHBOARD] Statistics reset")
    
    def export_data(self):
        """Export current data to JSON file"""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"slice_data_export_{timestamp}.json"
        
        export_data = {
            "timestamp": timestamp,
            "slice_data": {},
            "sla_violations": dict(self.sla_violations)
        }
        
        for sst, info in self.slice_data.items():
            export_data["slice_data"][sst] = {
                "name": info["name"],
                "metrics": list(info["metrics"]),
                "prb_history": list(info["prb_history"])
            }
        
        try:
            with open(filename, 'w') as f:
                json.dump(export_data, f, indent=2, default=str)
            print(f"[DASHBOARD] Data exported to {filename}")
        except Exception as e:
            print(f"[DASHBOARD] Error exporting data: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    dashboard = SliceManagementDashboard(root)
    root.mainloop()