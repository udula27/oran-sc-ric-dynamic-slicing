#!/usr/bin/env python3
# test_xapp_3.py — Closed-loop slice controller with asymmetric sharing graph.
#
# Policy (xapp3 variant):
#   min_quota  = [20, 30, 10]  # PRBs; never go below (SST order: eMBB, Emergency, IoT)
#   thresh_bps = [50e6, 5e6, 1e6]  # per-slice throughput targets
#
#   Sharing graph (directed edges, weights):
#     Emergency  ← eMBB   (α=0.7)   Emergency borrows from eMBB (primary)
#     Emergency  ← IoT    (γ=0.3)   Emergency borrows from IoT (secondary)
#     eMBB       ← IoT    (1.0)     eMBB can only borrow from IoT
#     IoT: no borrowing rights (guaranteed slice)
#
#   Every control loop (KPM Style 1 + RC Report Style 4):
#     1. Compute avg_tp[s] and util_PRB[s]
#     2. deficit[s] = 0 if not violating else current_quota[s] * (thresh[s]/avg_tp[s]) - current_quota[s]
#     3. Identify donors (util < 0.5 and has graph edge)
#     4. Allocate proportionally with beta=0.5 smoothness + EMA (0.7 old + 0.3 new)
#     5. Send E2 CONTROL message with new quotas via RC Action 2.6
#     6. On UE connect/disconnect, force re-run

import argparse
import csv
import datetime
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from threading import Lock

from ricxappframe.xapp_frame import rmr
from ricxappframe.e2ap.asn1 import IndicationMsg
from lib.e2sm_kpm_module import e2sm_types

from test_xapp_1 import TestXapp1, parse_slices

# Resolved at import time — always points to the directory containing this file
# regardless of the working directory the xApp is launched from.
_XAPP_DIR = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))


def _route_table_has_port(seed_rt_path, message_type, rmr_port):
    try:
        if not seed_rt_path or not os.path.exists(seed_rt_path):
            return None

        route_token = f":{int(rmr_port)}"
        with open(seed_rt_path, 'r', encoding='utf-8') as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if not line.startswith('rte|'):
                    continue

                parts = line.split('|', 2)
                if len(parts) < 3:
                    continue
                if parts[1].strip() != str(int(message_type)):
                    continue

                endpoints = parts[2]
                if '#' in endpoints:
                    endpoints = endpoints.split('#', 1)[0]
                endpoints = endpoints.strip()
                return route_token in endpoints
    except Exception:
        return None

    return None


class TestXapp3(TestXapp1):
    def __init__(self,
                 config,
                 http_server_port,
                 rmr_port,
                 ran_func_kpm,
                 ran_func_rc,
                 report_period_ms,
                 throughput_threshold,
                 idle_prb_threshold,
                 prb_shift_step,
                 control_cooldown_s,
                 ue_attach_grace_s,
                 activation_throughput_kbps,
                 min_consecutive_breaches,
                 init_equal_split,
                 init_min_ratio,
                 min_quotas,
                 target_slices,
                 sd,
                 cell_prbs,
                 tdd_dl_fraction,
                 dashboard_enabled,
                 dashboard_port,
                 dashboard_state_file,
                 dashboard_refresh_seconds,
                 dashboard_python):
        super(TestXapp3, self).__init__(
            config=config,
            http_server_port=http_server_port,
            rmr_port=rmr_port,
            ran_func_kpm=ran_func_kpm,
            ran_func_rc=ran_func_rc,
            report_period_ms=report_period_ms,
            throughput_threshold=throughput_threshold,
            idle_prb_threshold=idle_prb_threshold,
            prb_shift_step=prb_shift_step,
            control_cooldown_s=control_cooldown_s,
            ue_attach_grace_s=ue_attach_grace_s,
            activation_throughput_kbps=activation_throughput_kbps,
            min_consecutive_breaches=min_consecutive_breaches,
            init_equal_split=init_equal_split,
            init_min_ratio=init_min_ratio,
            target_slices=target_slices,
            sd=sd,
            cell_prbs=cell_prbs
        )

        self.dashboard_enabled = bool(dashboard_enabled)
        self.dashboard_port = int(dashboard_port)
        self.dashboard_state_file = dashboard_state_file
        self.dashboard_refresh_seconds = float(dashboard_refresh_seconds)
        self.dashboard_python = dashboard_python.strip() if dashboard_python else sys.executable
        self.dashboard_process = None

        self.policy_beta = 0.5
        self.policy_ema_old = 0.7
        self.policy_ema_new = 0.3

        # Policy xapp3:
        #   min_quota  = [20, 30, 10]  — eMBB never below 20%, Emergency below 30%, IoT below 10%
        #   thresh_bps = [50e6, 5e6, 1e6]  — per-slice throughput targets
        self.policy_min_quota_defaults = list(min_quotas) if min_quotas else [20, 30, 10]
        self.policy_threshold_bps_defaults = [50e6, 5e6, 1e6]

        # TDD downlink duty-cycle fraction (same semantics as test_xapp_2).
        self.tdd_dl_fraction = float(max(0.1, min(1.0, tdd_dl_fraction)))

        self.policy_min_quota_by_sst = {}
        self.policy_threshold_bps_by_sst = {}
        self.policy_sharing_weights = {}

        # Reset PRBs after 40 s of continuous idle (no active traffic).
        # 40 s is safely above the typical 10-15 s inter-test pause, so
        # back-to-back iperf runs preserve accumulated quota.  The previous
        # 120 s workaround is no longer needed now that the E2 float-truncation
        # bug (Issue 10) is fixed and quotas actually advance at the gNB.
        self.idle_reset_timeout_s = 40.0
        self.last_non_idle_traffic_ts = datetime.datetime.now().timestamp()
        self.no_ues_since_ts = None
        self.initial_prb_max_by_sst = {}
        self.initial_prb_baseline_ready = False
        self.ue_disconnect_timeout_s = max(30.0, 10.0 * (float(self.report_period_ms) / 1000.0))

        self.control_event_lock = Lock()
        self.control_event_log = []
        self.max_control_events = 200
        self._pending_control_reason = None

        # Real-time CSV telemetry output (common timestamp across all three files).
        # Written to /tmp/ so they are accessible via: docker cp python_xapp_runner:/tmp/telemetry_*.csv .
        self.csv_ue_throughput_path = '/tmp/telemetry_ue_throughput.csv'
        self.csv_slice_prb_util_path = '/tmp/telemetry_slice_prb_utilization.csv'
        self.csv_slice_prb_alloc_path = '/tmp/telemetry_slice_prb_allocation.csv'
        self._csv_lock = Lock()
        self._init_csv_files()

        self._init_policy_profile()

        self._flush_dashboard_state()
        self._start_dashboard()

    # ------------------------------------------------------------------
    # Policy profile initialisation
    # ------------------------------------------------------------------

    def _init_policy_profile(self):
        """Set per-slice min quotas, throughput thresholds, and the xapp3 sharing graph.

        Sharing graph (3-slice case):
          Emergency (slices[1]) borrows from eMBB (slices[0]) with weight α=0.7
                                  and from IoT (slices[2]) with weight γ=0.3.
          eMBB      (slices[0]) can only borrow from IoT (slices[2]) with weight 1.0.
          IoT has no borrowing rights (guaranteed slice).
        """
        slices = list(self.target_slices)
        for idx, sst in enumerate(slices):
            if idx < len(self.policy_min_quota_defaults):
                min_quota = int(self.policy_min_quota_defaults[idx])
                threshold = float(self.policy_threshold_bps_defaults[idx])
            else:
                min_quota = int(self.policy_min_quota_defaults[-1])
                threshold = float(self.policy_threshold_bps_defaults[-1])

            self.policy_min_quota_by_sst[sst] = min_quota
            self.policy_threshold_bps_by_sst[sst] = threshold

            if sst in self.slice_policy:
                self.slice_policy[sst]['min'] = min_quota
                if self.slice_policy[sst]['max'] < min_quota:
                    self.slice_policy[sst]['max'] = min_quota

        if len(slices) >= 3:
            embb_sst = slices[0]        # SST order position 0 → eMBB
            emergency_sst = slices[1]   # SST order position 1 → Emergency
            iot_sst = slices[2]         # SST order position 2 → IoT

            # xapp3 asymmetric sharing graph:
            #   Emergency borrows from eMBB (α=0.7) and IoT (γ=0.3).
            #   eMBB      can only borrow from IoT (weight=1.0).
            #   IoT       has no borrowing rights.
            #
            # The 0.7/0.3 split means Emergency preferentially drains the best-effort
            # eMBB slice first, but can also use idle IoT budget as a secondary source.
            # Borrowing is gated by donor util < 0.5, so IoT is only drained when it
            # is genuinely idle.
            self.policy_sharing_weights = {
                emergency_sst: {
                    embb_sst: 0.7,    # α: Emergency borrows from eMBB (primary)
                    iot_sst:  0.3,
                        # γ: Emergency borrows from IoT (secondary)
                },
                embb_sst: {
                    iot_sst: 1.0,
                        # eMBB borrows from IoT only
                }
            }
        else:
            self.policy_sharing_weights = {}
            self._log('[POLICY][WARN] Fewer than 3 slices configured; sharing graph disabled')

        self._log(
            f"[POLICY] min_quota={self.policy_min_quota_by_sst}, "
            f"thresh_bps={self.policy_threshold_bps_by_sst}"
        )

    # ------------------------------------------------------------------
    # Initial allocation helpers
    # ------------------------------------------------------------------

    def _apply_initial_equal_split(self, e2_node_id):
        super()._apply_initial_equal_split(e2_node_id)
        self._flush_dashboard_state()

    def _build_near_equal_max_ratios(self):
        """Fixed initial PRB split for 3 slices: eMBB→45%, Emergency→30%, IoT→25%.
        Falls back to the parent equal-split for other counts."""
        if len(self.target_slices) == 3:
            fixed = [45, 30, 25]
            return {sst: fixed[idx] for idx, sst in enumerate(self.target_slices)}
        return super()._build_near_equal_max_ratios()

    # ------------------------------------------------------------------
    # Dashboard helpers
    # ------------------------------------------------------------------

    def _dashboard_script_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_xapp_3_dashboard.py')

    # ------------------------------------------------------------------
    # Internal utilities (identical logic to test_xapp_2)
    # ------------------------------------------------------------------

    def _get_e2_connection_status(self, e2_node_id):
        try:
            e2mgr_url = f"http://10.0.2.11:3800/v1/nodeb/{e2_node_id}"
            req = urllib.request.Request(e2mgr_url, method='GET')
            with urllib.request.urlopen(req, timeout=2) as resp:
                payload = resp.read().decode('utf-8', errors='ignore')
            data = json.loads(payload)
            return str(data.get('connectionStatus', 'UNKNOWN'))
        except urllib.error.HTTPError as ex:
            return f'HTTP_{ex.code}'
        except Exception:
            return 'UNKNOWN'

    def _record_control_event(self, message_type, reason, sst=None, policy=None, details=None):
        event = {
            'ts': datetime.datetime.now().isoformat(),
            'message_type': str(message_type),
            'reason': str(reason) if reason is not None else '',
            'sst': None if sst is None else int(sst),
            'policy': policy if isinstance(policy, dict) else None,
            'details': str(details) if details is not None else ''
        }
        with self.control_event_lock:
            self.control_event_log.append(event)
            if len(self.control_event_log) > self.max_control_events:
                del self.control_event_log[:-self.max_control_events]

    def _lookup_throughput_entry_locked(self, ue_id):
        if ue_id in self.ue_throughput:
            return self.ue_throughput.get(ue_id)

        ue_id_str = str(ue_id)
        for tracked_ue_id, entry in self.ue_throughput.items():
            if str(tracked_ue_id) == ue_id_str:
                return entry
        return None

    def _prune_stale_ues_locked(self, now_ts):
        stale_ids = []
        timeout = float(self.ue_disconnect_timeout_s)

        for ue_id, ctx in self.ue_context.items():
            last_seen = float(ctx.get('last_seen', 0.0) or 0.0)
            if (now_ts - last_seen) <= timeout:
                continue

            thp = self._lookup_throughput_entry_locked(ue_id)
            if thp is not None:
                thp_ts = float(thp.get('ts', 0.0) or 0.0)
            else:
                sst = ctx.get('slice', {}).get('sst')
                thp_ts = float((self.slice_prb.get(sst) or {}).get('ts', 0.0) or 0.0)
            if (now_ts - thp_ts) <= timeout:
                continue

            stale_ids.append(ue_id)

        for ue_id in stale_ids:
            old = self.ue_context.pop(ue_id, None)
            self.ue_traffic_state.pop(ue_id, None)
            self.ue_breach_streak.pop(ue_id, None)

            to_remove_thp = []
            for tracked_ue_id in list(self.ue_throughput.keys()):
                if str(tracked_ue_id) == str(ue_id):
                    to_remove_thp.append(tracked_ue_id)
            for tracked_ue_id in to_remove_thp:
                self.ue_throughput.pop(tracked_ue_id, None)

            slice_info = old['slice'] if old else {'sst': None, 'sd': None}
            self._log(
                f"[UE_EVENT][DISCONNECT][STALE_TIMEOUT] ue_id={ue_id} "
                f"slice_sst={slice_info.get('sst')} slice_sd={slice_info.get('sd')} timeout_s={timeout}"
            )

    def _normalized_slice_util(self, sst):
        """Return normalized PRB utilization using the integer quota sent to the gNB.

        slice_policy['max'] is stored as a float accumulator but E2 RC sends
        int(round(quota_pct)).  Use the integer so the denominator matches the
        actual PRBs allocated by the gNB, preventing artificial under-reporting
        of utilization that would block the PRB saturation signal.
        """
        load = self._safe_prb_load(sst)
        if load is None:
            return None
        quota_pct_raw = float((self.slice_policy.get(sst) or {}).get('max') or 0)
        # Match the integer sent to gNB via E2 RC control.
        quota_pct = float(int(round(quota_pct_raw)))
        if quota_pct <= 0.0:
            return 0.0
        allocated_prbs = quota_pct / 100.0 * float(self.cell_prbs)
        if allocated_prbs <= 0.0:
            return 0.0
        return float(load) / allocated_prbs

    def _compute_avg_tp_bps_per_slice_locked(self):
        per_slice_samples = {sst: [] for sst in self.target_slices}
        idle_floor_kbps = max(5.0, self.activation_throughput_kbps / 20.0)

        for ue_id, thp in self.ue_throughput.items():
            sst = self._map_ue_to_slice(ue_id)
            if sst not in per_slice_samples:
                continue

            dl = thp.get('dl')
            ul = thp.get('ul')
            effective_kbps = max(float(dl or 0.0), float(ul or 0.0))
            if effective_kbps < idle_floor_kbps:
                continue

            effective_bps = float(effective_kbps) * 1000.0
            per_slice_samples[sst].append(effective_bps)

        avg_tp = {}
        for sst, vals in per_slice_samples.items():
            if not vals:
                avg_tp[sst] = 0.0
            else:
                avg_tp[sst] = float(sum(vals) / len(vals))
        return avg_tp

    def _capture_initial_prb_baseline_locked(self):
        self.initial_prb_max_by_sst = {
            sst: int(self.slice_policy[sst]['max'])
            for sst in self.target_slices
            if sst in self.slice_policy
        }
        self.initial_prb_baseline_ready = len(self.initial_prb_max_by_sst) > 0
        if self.initial_prb_baseline_ready:
            self._log(f"[POLICY][BASELINE] initial_prb_max={self.initial_prb_max_by_sst}")

    def _is_at_initial_prb_locked(self):
        if not self.initial_prb_baseline_ready:
            return True
        for sst, baseline_max in self.initial_prb_max_by_sst.items():
            if int(self.slice_policy.get(sst, {}).get('max', baseline_max)) != int(baseline_max):
                return False
        return True

    def _has_active_traffic_locked(self):
        threshold_kbps = float(max(0.0, self.activation_throughput_kbps))
        for thp in self.ue_throughput.values():
            dl = thp.get('dl')
            ul = thp.get('ul')
            effective_kbps = max(float(dl or 0.0), float(ul or 0.0))
            if effective_kbps >= threshold_kbps:
                return True
        return False

    def _reset_to_initial_prb_locked(self, e2_node_id, reason):
        if not self.initial_prb_baseline_ready:
            return False

        changed_slices = []
        for sst, baseline_max in self.initial_prb_max_by_sst.items():
            if sst not in self.slice_policy:
                continue
            current_max = int(self.slice_policy[sst]['max'])
            if current_max != int(baseline_max):
                self.slice_policy[sst]['max'] = int(baseline_max)
                changed_slices.append(sst)

        if not changed_slices:
            return False

        for sst in changed_slices:
            self._pending_control_reason = f'idle_reset:{reason}'
            self._send_slice_prb_control(e2_node_id, sst, self.slice_policy[sst])

        self.last_control_ts = datetime.datetime.now().timestamp()
        self._log(
            f"[POLICY][RESET_TO_INITIAL] reason={reason} changed_slices={changed_slices} "
            f"baseline={self.initial_prb_max_by_sst}"
        )
        return True

    # ------------------------------------------------------------------
    # Core control-policy evaluation
    # ------------------------------------------------------------------

    def _evaluate_control_policy(self, e2_node_id, force=False):
        """Closed-loop PRB reallocation with xapp3 asymmetric sharing graph.

        Algorithm:
          1. Compute avg_tp[s] and util_PRB[s] from latest KPM + RC state.
          2. deficit[s] = 0 if not violating, else
                          current_quota[s] * (thresh[s] / avg_tp[s]) - current_quota[s]
             Suppressed when util < PRB_SAT_THRESHOLD (slice not PRB-limited).
             PRB-saturation signal injects a step-sized deficit for PRB-bottlenecked
             slices even when tp ≥ threshold.
          3. Donors: util < 0.5 and outgoing edge in sharing graph.
          4. Allocate proportionally via beta=0.5 + EMA(0.7 old, 0.3 new).
          5. Send E2 CONTROL via RC Action 2.6 for changed slices.
          6. On UE connect/disconnect this is called with force=True.
        """
        now = datetime.datetime.now().timestamp()

        with self.state_lock:
            self._prune_stale_ues_locked(now)

            has_active = self._has_active_traffic_locked()
            if has_active:
                self.last_non_idle_traffic_ts = now

            no_ues_now = len(self.ue_context) == 0 and not has_active
            if no_ues_now:
                if self.no_ues_since_ts is None:
                    self.no_ues_since_ts = now
            else:
                self.no_ues_since_ts = None

            no_ues_long_enough = (
                self.no_ues_since_ts is not None
                and (now - self.no_ues_since_ts) >= 10.0
            )
            idle_too_long = (now - self.last_non_idle_traffic_ts) >= self.idle_reset_timeout_s

            if no_ues_long_enough or idle_too_long:
                reason = 'no_ues_10s' if no_ues_long_enough else f'idle_timeout_{self.idle_reset_timeout_s}s'
                if not self._is_at_initial_prb_locked():
                    self._reset_to_initial_prb_locked(e2_node_id, reason)
                return

            if (not force) and (now - self.last_control_ts < self.control_cooldown_s):
                return

            current_quota = {
                sst: float(self.slice_policy[sst]['max'])
                for sst in self.target_slices
            }
            min_quota = {
                sst: float(self.policy_min_quota_by_sst.get(sst, self.slice_policy[sst]['min']))
                for sst in self.target_slices
            }

            avg_tp = self._compute_avg_tp_bps_per_slice_locked()
            util_prb = {
                sst: self._normalized_slice_util(sst)
                for sst in self.target_slices
            }

            ue_counts = self._active_ue_count_per_slice()

            # PRB saturation threshold (TDD-scaled): borrow only when ≥90% of DL slots are filled.
            _PRB_SAT_THRESHOLD = 0.90 * self.tdd_dl_fraction

            # Step 2: compute per-slice deficit.
            deficit = {}
            for sst in self.target_slices:
                threshold = float(self.policy_threshold_bps_by_sst.get(sst, 0.0))
                tp = float(avg_tp.get(sst, 0.0))
                quota = float(current_quota[sst])

                if tp <= 0.0 or tp >= threshold:
                    deficit[sst] = 0.0
                    continue

                # Suppress deficit when the slice is not PRB-limited.
                util_for_gate = util_prb.get(sst)
                if util_for_gate is not None and util_for_gate < _PRB_SAT_THRESHOLD:
                    deficit[sst] = 0.0
                    self._log(
                        f"[POLICY][TP_DEFICIT_SUPPRESSED] sst={sst} tp={tp:.0f}bps "
                        f"threshold={threshold:.0f}bps but util={util_for_gate:.3f} < "
                        f"prb_sat_threshold={_PRB_SAT_THRESHOLD:.3f} → deficit suppressed"
                    )
                    continue

                required = quota * (threshold / max(tp, 1e-9)) - quota
                deficit[sst] = max(0.0, required)

            # PRB-saturation deficit injection (Signals A and B — same logic as xapp2).
            for sst in self.target_slices:
                if deficit.get(sst, 0.0) > 0.0:
                    continue

                util = util_prb.get(sst)
                has_ues = ue_counts.get(sst, 0) > 0
                tp = float(avg_tp.get(sst, 0.0))

                if not has_ues and (util is None or util <= 0.0):
                    continue

                prb_saturated = (util is not None and util >= _PRB_SAT_THRESHOLD)
                min_active_bps = max(1e3, float(self.policy_threshold_bps_by_sst.get(sst, 0.0)) * 0.01)
                ue_active_no_prb_data = (has_ues and util is None and tp >= min_active_bps)

                if not prb_saturated and not ue_active_no_prb_data:
                    continue

                sat_deficit = float(self.prb_shift_step)
                deficit[sst] = sat_deficit
                signal = 'prb_util' if prb_saturated else 'ue_active_no_prb_data'
                util_str = f'{util:.3f}' if util is not None else 'None'
                self._log(
                    f"[POLICY][PRB_SAT] sst={sst} signal={signal} prb_util={util_str} "
                    f"tp={tp:.0f}bps (prb_threshold={_PRB_SAT_THRESHOLD:.3f}) "
                    f"injecting saturation deficit={sat_deficit:.1f}"
                )

            proposed_quota = dict(current_quota)
            donor_available = {
                sst: max(0.0, current_quota[sst] - min_quota[sst])
                for sst in self.target_slices
            }

            total_in = {sst: 0.0 for sst in self.target_slices}
            total_out = {sst: 0.0 for sst in self.target_slices}

            # Step 3 & 4: allocate from eligible donors proportionally.
            #
            # Two-pass approach so that when multiple receivers are saturated
            # simultaneously and compete for the same donor (e.g. both SST1
            # and SST2 flooded while SST3 is the only eligible donor), the
            # donor's available budget is split proportionally among all
            # receivers rather than being consumed entirely by whichever
            # receiver happens to be first in iteration order.
            #
            # Pass 1 — compute raw (uncapped) demand per (receiver, donor).
            #
            # Cap each receiver's demand at prb_shift_step before the sharing
            # computation.  The EMA filter (beta=0.5, 0.7/0.3) already limits
            # the actual per-cycle quota change to ~prb_shift_step regardless
            # of how large the raw deficit is.  An oversized throughput-based
            # deficit (e.g. SST1 at 17 Mbps vs 50 Mbps threshold → deficit=99%)
            # provides no extra growth beyond the step but monopolises the
            # donor's proportional budget — leaving receivers with smaller
            # deficits (e.g. SST2 at deficit=5) with almost nothing.
            # Capping at prb_shift_step makes every saturated receiver compete
            # on equal footing; the actual quota change is still β-smoothed.
            raw_demand = {}  # {(receiver_sst, donor_sst): float}

            for receiver_sst in self.target_slices:
                required = min(deficit.get(receiver_sst, 0.0), float(self.prb_shift_step))
                if required <= 0.0:
                    continue

                graph = self.policy_sharing_weights.get(receiver_sst, {})
                if not graph:
                    continue

                eligible = []
                for donor_sst, weight in graph.items():
                    util = util_prb.get(donor_sst)
                    # Guard: block only when the KPM metric is completely absent
                    # (util is None) AND the slice has connected UEs — genuine
                    # metric-gap case.  util == 0.0 is a valid confirmed-zero
                    # reading and must NOT be treated as a gap; it means the
                    # slice is idle (UEs connected but not transmitting) and is
                    # a legitimate donor.
                    if ue_counts.get(donor_sst, 0) > 0 and util is None:
                        continue
                    effective_util = util if util is not None else 0.0
                    if effective_util >= 0.5:
                        continue
                    if donor_available.get(donor_sst, 0.0) <= 0.0:
                        continue
                    if weight <= 0.0:
                        continue
                    eligible.append((donor_sst, float(weight)))

                if not eligible:
                    continue

                weight_sum = sum(w for _, w in eligible)
                if weight_sum <= 0.0:
                    continue

                for donor_sst, weight in eligible:
                    key = (receiver_sst, donor_sst)
                    raw_demand[key] = required * (weight / weight_sum)

            # Pass 2 — for each donor, if total raw demand from all receivers
            # exceeds its available budget, scale every receiver's share down
            # proportionally so no donor is over-drawn by one receiver alone.
            for donor_sst in self.target_slices:
                total_raw = sum(v for (r, d), v in raw_demand.items() if d == donor_sst)
                avail = donor_available.get(donor_sst, 0.0)
                if total_raw > avail and total_raw > 0.0:
                    scale = avail / total_raw
                    competing = [r for (r, d) in raw_demand if d == donor_sst]
                    for r in competing:
                        raw_demand[(r, donor_sst)] *= scale
                    self._log(
                        f"[POLICY][DONOR_SCALE] donor_sst={donor_sst} "
                        f"total_raw={total_raw:.2f} avail={avail:.2f} "
                        f"scale={scale:.3f} receivers={competing}"
                    )

            # Pass 3 — aggregate final transfers.
            for (receiver_sst, donor_sst), amount in raw_demand.items():
                if amount <= 0.0:
                    continue
                total_out[donor_sst] += amount
                total_in[receiver_sst] += amount

            for sst in self.target_slices:
                proposed_quota[sst] = current_quota[sst] + total_in[sst] - total_out[sst]
                proposed_quota[sst] = max(min_quota[sst], min(100.0, proposed_quota[sst]))

            # Step 4 continued: beta=0.5 smoothing + EMA(0.7 old, 0.3 new).
            smoothed_quota = {}
            ema_quota = {}
            for sst in self.target_slices:
                old = current_quota[sst]
                new = proposed_quota[sst]
                smooth = old + self.policy_beta * (new - old)
                ema = self.policy_ema_old * old + self.policy_ema_new * smooth
                smoothed_quota[sst] = smooth
                ema_quota[sst] = max(min_quota[sst], min(100.0, ema))

            # Store float EMA values internally so fractional per-cycle changes
            # (e.g. SST3's 0.225%/cycle from the 0.3 graph weight) accumulate
            # across cycles and eventually cross the 0.5 rounding boundary.
            # We only convert to int when: (a) detecting whether an E2 message is
            # needed, and (b) building the E2 payload in _send_slice_prb_control.
            # Without this, SST3's sub-1% share rounds to zero every cycle and the
            # slice never donates even though it is idle.
            #
            # Sum-cap: clamp ema_quota so the total stays ≤ 100% before storing,
            # preventing cumulative float drift above the cell limit.
            float_sum = sum(max(min_quota[sst], min(100.0, ema_quota[sst])) for sst in self.target_slices)
            if float_sum > 100.0:
                scale = 100.0 / float_sum
                for sst in self.target_slices:
                    ema_quota[sst] = max(min_quota[sst], ema_quota[sst] * scale)

            changed_slices = []
            for sst in self.target_slices:
                old_int = int(round(current_quota[sst]))
                new_float = max(min_quota[sst], min(100.0, ema_quota[sst]))
                new_int = int(round(new_float))
                if new_int != old_int:
                    self.slice_policy[sst]['max'] = new_float  # store float
                    changed_slices.append(sst)
                else:
                    # No integer change this cycle, but still store the float so
                    # the fractional part accumulates for the next cycle.
                    self.slice_policy[sst]['max'] = new_float

            if not changed_slices:
                return

            # Step 5: send E2 CONTROL for changed slices.
            for sst in changed_slices:
                policy = self.slice_policy[sst]
                if float(deficit.get(sst, 0.0)) > 0.0:
                    reason = (
                        f"throughput_deficit tp={avg_tp[sst]:.1f}bps "
                        f"threshold={self.policy_threshold_bps_by_sst.get(sst, 0.0):.1f}bps"
                    )
                else:
                    reason = 'donor_rebalance_for_other_slice'
                self._pending_control_reason = reason
                self._send_slice_prb_control(e2_node_id, sst, policy)

            self.last_control_ts = now

            log_chunks = []
            for sst in self.target_slices:
                log_chunks.append(
                    f"sst={sst} tp={avg_tp[sst]:.1f}bps util={util_prb[sst]} "
                    f"deficit={deficit[sst]:.2f} "
                    f"quota(old={current_quota[sst]:.2f},new={ema_quota[sst]:.2f})"
                )
            self._log("[POLICY][REALLOC] " + " | ".join(log_chunks))

    # ------------------------------------------------------------------
    # Subscription with retry
    # ------------------------------------------------------------------

    def _subscription_response_callback(self, name, path, data, ctype):
        return super(TestXapp3, self)._subscription_response_callback(name, path, data, ctype)

    def subscribe(self, e2_node_id, ran_function_id, event_trigger_def, action_def, indication_callback, e2sm_type):
        max_attempts = 120
        base_delay_s = 1.0
        max_delay_s = 8.0

        last_ex = None
        for attempt in range(1, max_attempts + 1):
            try:
                return super(TestXapp3, self).subscribe(
                    e2_node_id,
                    ran_function_id,
                    event_trigger_def,
                    action_def,
                    indication_callback,
                    e2sm_type
                )
            except Exception as ex:
                last_ex = ex
                ex_text = str(ex)
                is_503 = ('503' in ex_text) or ('Service Unavailable' in ex_text)
                if is_503:
                    delay = min(base_delay_s * (1.5 ** (attempt - 1)), max_delay_s)
                    conn_status = self._get_e2_connection_status(e2_node_id)
                    self._log(
                        f"[SUBSCRIBE][RETRY] attempt={attempt}/{max_attempts} failed with 503; "
                        f"E2 connection status for {e2_node_id}={conn_status}; retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
                    continue

                self._log(f"[SUBSCRIBE][ERROR] attempt={attempt}/{max_attempts} failed: {ex}")
                raise

        if last_ex is not None:
            raise last_ex

    # ------------------------------------------------------------------
    # Dashboard state
    # ------------------------------------------------------------------

    def _build_dashboard_state_locked(self):
        slices = sorted(self.target_slices)
        ues_by_slice = {str(sst): [] for sst in slices}
        throughput_by_slice = {str(sst): {} for sst in slices}
        ue_slice_lookup = {}

        for ue_id, ctx in self.ue_context.items():
            sst = ctx.get('slice', {}).get('sst')
            if sst in slices:
                ue_key = str(ue_id)
                ue_slice_lookup[ue_key] = sst
                ues_by_slice[str(sst)].append({
                    'ue_id': ue_key,
                    'connected_at': ctx.get('connected_at'),
                    'last_seen': ctx.get('last_seen')
                })

        for ue_id, thp in self.ue_throughput.items():
            ue_key = str(ue_id)
            sst = ue_slice_lookup.get(ue_key)
            if sst in slices:
                dl = thp.get('dl')
                ul = thp.get('ul')
                effective = dl if dl is not None else ul
                throughput_by_slice[str(sst)][ue_key] = {
                    'ue_id': ue_key,
                    'dl': dl,
                    'ul': ul,
                    'effective': effective,
                    'ts': thp.get('ts')
                }

        throughput_by_slice = {
            sst_key: list(entries.values())
            for sst_key, entries in throughput_by_slice.items()
        }

        slice_policy_state = {}
        slice_prb_state = {}
        for sst in slices:
            policy = self.slice_policy.get(sst, {'min': 0, 'max': 0, 'ded': 0})
            prb = self.slice_prb.get(sst, {'dl': None, 'ul': None, 'ts': 0.0})
            dl = prb.get('dl')
            ul = prb.get('ul')
            total = float(dl or 0.0) + float(ul or 0.0)

            slice_policy_state[str(sst)] = {
                'min': int(policy.get('min', 0)),
                'max': int(policy.get('max', 0)),
                'ded': int(policy.get('ded', 0))
            }
            slice_prb_state[str(sst)] = {
                'dl': dl,
                'ul': ul,
                'total': total,
                'ts': prb.get('ts', 0.0)
            }

        state = {
            'updated_at': datetime.datetime.now().isoformat(),
            'target_slices': slices,
            'slice_policy': slice_policy_state,
            'slice_prb': slice_prb_state,
            'ues_by_slice': ues_by_slice,
            'throughput_by_slice': throughput_by_slice,
            'counts': {
                'control_ack': int(self.control_ack_count),
                'control_failure': int(self.control_failure_count),
                'indications_total': int(self.indication_total_count),
                'indications_kpm': int(self.indication_kpm_count),
                'indications_rc': int(self.indication_rc_count),
                'indications_unmapped': int(self.unmapped_indication_count),
                'tracked_ues': len(self.ue_context)
            },
            'control_events': []
        }

        with self.control_event_lock:
            state['control_events'] = list(self.control_event_log)

        return state

    def _flush_dashboard_state(self):
        with self.state_lock:
            state = self._build_dashboard_state_locked()

        # Write CSV telemetry independently — never blocked by JSON state write.
        try:
            self._write_telemetry_csv(state)
        except Exception as csv_ex:
            self._log(f"[TELEMETRY_CSV][WARN] Failed to write CSV: {csv_ex}")

        # Write JSON state file for dashboard.
        try:
            state_dir = os.path.dirname(os.path.abspath(self.dashboard_state_file))
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)

            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=state_dir,
                prefix='.state_',
                suffix='.tmp',
                delete=False
            ) as fp:
                tmp_path = fp.name
                json.dump(state, fp, indent=2)
            os.replace(tmp_path, self.dashboard_state_file)
        except Exception as ex:
            self._log(f"[DASHBOARD][WARN] Failed to write state file: {ex}")

    def _init_csv_files(self):
        """Create CSV files with headers at startup if they don't already exist."""
        files_and_fields = [
            (self.csv_ue_throughput_path,   ['timestamp', 'ue_id', 'slice_sst', 'dl_kbps', 'ul_kbps']),
            (self.csv_slice_prb_util_path,  ['timestamp', 'slice_sst', 'prb_dl', 'prb_ul', 'prb_total']),
            (self.csv_slice_prb_alloc_path, ['timestamp', 'slice_sst', 'min_quota_pct', 'max_quota_pct', 'ded_quota_pct']),
        ]
        print(f"[TELEMETRY_CSV] Writing CSV files to: {os.path.dirname(self.csv_ue_throughput_path)}", flush=True)
        for path, fields in files_and_fields:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as fh:
                    csv.DictWriter(fh, fieldnames=fields).writeheader()
                print(f"[TELEMETRY_CSV] Created {path}", flush=True)
            except Exception as ex:
                print(f"[TELEMETRY_CSV][ERROR] Could not create {path}: {ex}", flush=True)

    def _write_telemetry_csv(self, state):
        """Append one row per UE/slice to three telemetry CSV files.

        All rows written in a single call share the same 'timestamp' value
        (ISO-8601) taken from state['updated_at'], giving a common index
        across telemetry_ue_throughput.csv, telemetry_slice_prb_utilization.csv,
        and telemetry_slice_prb_allocation.csv.
        """
        ts = state.get('updated_at', datetime.datetime.now().isoformat())

        with self._csv_lock:
            # ---- 1. UE-level throughput ----------------------------------------
            try:
                ue_rows = []
                for sst_key, entries in state.get('throughput_by_slice', {}).items():
                    for entry in entries:
                        ue_rows.append({
                            'timestamp': ts,
                            'ue_id': entry.get('ue_id', ''),
                            'slice_sst': sst_key,
                            'dl_kbps': entry.get('dl', ''),
                            'ul_kbps': entry.get('ul', ''),
                        })
                ue_fields = ['timestamp', 'ue_id', 'slice_sst', 'dl_kbps', 'ul_kbps']
                with open(self.csv_ue_throughput_path, 'a', newline='', encoding='utf-8') as fh:
                    writer = csv.DictWriter(fh, fieldnames=ue_fields)
                    writer.writerows(ue_rows)
            except Exception as ex:
                self._log(f"[TELEMETRY_CSV][WARN] ue_throughput write failed: {ex}")

            # ---- 2. Slice PRB utilization (from KPM measurements) ---------------
            try:
                util_rows = []
                for sst_key, prb in state.get('slice_prb', {}).items():
                    util_rows.append({
                        'timestamp': ts,
                        'slice_sst': sst_key,
                        'prb_dl': prb.get('dl', ''),
                        'prb_ul': prb.get('ul', ''),
                        'prb_total': prb.get('total', ''),
                    })
                util_fields = ['timestamp', 'slice_sst', 'prb_dl', 'prb_ul', 'prb_total']
                with open(self.csv_slice_prb_util_path, 'a', newline='', encoding='utf-8') as fh:
                    writer = csv.DictWriter(fh, fieldnames=util_fields)
                    writer.writerows(util_rows)
            except Exception as ex:
                self._log(f"[TELEMETRY_CSV][WARN] slice_prb_util write failed: {ex}")

            # ---- 3. Slice PRB allocation (RC policy quotas) ---------------------
            try:
                alloc_rows = []
                for sst_key, policy in state.get('slice_policy', {}).items():
                    alloc_rows.append({
                        'timestamp': ts,
                        'slice_sst': sst_key,
                        'min_quota_pct': policy.get('min', ''),
                        'max_quota_pct': policy.get('max', ''),
                        'ded_quota_pct': policy.get('ded', ''),
                    })
                alloc_fields = ['timestamp', 'slice_sst', 'min_quota_pct', 'max_quota_pct', 'ded_quota_pct']
                with open(self.csv_slice_prb_alloc_path, 'a', newline='', encoding='utf-8') as fh:
                    writer = csv.DictWriter(fh, fieldnames=alloc_fields)
                    writer.writerows(alloc_rows)
            except Exception as ex:
                self._log(f"[TELEMETRY_CSV][WARN] slice_prb_alloc write failed: {ex}")

    def _start_dashboard(self):
        if not self.dashboard_enabled:
            self._log('[DASHBOARD] Disabled by CLI option')
            return

        script_path = self._dashboard_script_path()
        if not os.path.exists(script_path):
            self._log(f'[DASHBOARD][WARN] Dashboard script missing: {script_path}')
            return

        cmd = [
            self.dashboard_python,
            '-m',
            'streamlit',
            'run',
            script_path,
            '--server.port',
            str(self.dashboard_port),
            '--server.address',
            '0.0.0.0',
            '--',
            '--state-file',
            self.dashboard_state_file,
            '--refresh-seconds',
            str(self.dashboard_refresh_seconds),
            '--slices',
            ','.join(str(sst) for sst in self.target_slices)
        ]

        try:
            self.dashboard_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self._log(
                f"[DASHBOARD] Streamlit started at http://0.0.0.0:{self.dashboard_port} "
                f"(pid={self.dashboard_process.pid}, python={self.dashboard_python})"
            )
        except Exception as ex:
            self.dashboard_process = None
            self._log(
                f"[DASHBOARD][WARN] Could not start Streamlit dashboard ({ex}). "
                f"Install streamlit in dashboard env or set --dashboard_python."
            )

    def _stop_dashboard(self):
        if self.dashboard_process is None:
            return
        if self.dashboard_process.poll() is not None:
            self.dashboard_process = None
            return

        try:
            self.dashboard_process.terminate()
            self.dashboard_process.wait(timeout=3)
        except Exception:
            try:
                self.dashboard_process.kill()
            except Exception:
                pass
        finally:
            self.dashboard_process = None

    # ------------------------------------------------------------------
    # UE state helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ue_id_matches(a, b):
        return str(a) == str(b)

    def _prune_disconnected_ue_state(self, disconnected_ids):
        if not disconnected_ids:
            return

        with self.state_lock:
            to_remove = []
            for tracked_ue_id in list(self.ue_throughput.keys()):
                for disconnected_id in disconnected_ids:
                    if self._ue_id_matches(tracked_ue_id, disconnected_id):
                        to_remove.append(tracked_ue_id)
                        break

            for ue_id in to_remove:
                self.ue_throughput.pop(ue_id, None)
                self.ue_traffic_state.pop(ue_id, None)
                self.ue_breach_streak.pop(ue_id, None)

            if to_remove:
                self._log(f"[UE_EVENT][THROUGHPUT_PRUNE] removed={sorted(str(x) for x in to_remove)}")

    # ------------------------------------------------------------------
    # Scheduler rebind trigger
    # ------------------------------------------------------------------

    def _send_rebind_rc_for_new_ue(self, e2_node_id):
        """Resend current PRB quotas for all slices to trigger rebind_active_ues_to_slices()
        at the gNB scheduler.

        The scheduler only moves UEs out of DEFAULT_DRB (slice id<2) inside
        handle_slice_reconfiguration_request(), which fires only when an RC CONTROL
        message is received.  When a UE connects after the startup equal-split has
        already been sent, the policy evaluation may find no quota change needed
        (zero traffic → zero deficit) and skip sending RC control entirely — leaving
        the new UE in DEFAULT_DRB where it bypasses all PRB enforcement gates.
        This method ensures an RC control is always sent on every new-UE event so
        that the scheduler rebinds the UE to its correct custom slice immediately.
        """
        with self.state_lock:
            slices_to_rebind = list(self.target_slices)

        for sst in slices_to_rebind:
            policy = self.slice_policy.get(sst)
            if policy is None:
                continue
            self._pending_control_reason = 'rebind_new_ue'
            self._send_slice_prb_control(e2_node_id, sst, policy)

        self._log(
            f"[REBIND] Sent RC control for all slices to trigger scheduler rebind "
            f"for newly attached UE (slices={slices_to_rebind})"
        )

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def rc_style4_indication_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        _ = (indication_hdr,)

        self.rc_callback_count += 1
        if self.rc_callback_count == 1:
            self._log(
                f"[RC] First RC Style 4 indication received from {e2_agent_id} "
                f"(sub_id={subscription_id}, bytes={len(indication_msg)})"
            )

        try:
            current_ues = self._decode_rc_style4_indication(indication_msg)
        except Exception as ex:
            self._log(f"[RC][ERROR] Failed to parse RC Style 4 indication: {ex}")
            self._flush_dashboard_state()
            return

        if not current_ues:
            self.rc_empty_payload_count += 1
            if self.rc_empty_payload_count <= 5:
                self._log(
                    f"[RC] Empty/undecodable UE list in RC indication "
                    f"(count={self.rc_empty_payload_count}, bytes={len(indication_msg)})"
                )

        new_ue_detected = False
        now_ts = datetime.datetime.now().timestamp()
        with self.state_lock:
            for ue in current_ues:
                ue_id = ue.get('ue_id')
                if ue_id is None:
                    continue

                key = ue_id
                ue_id_str = str(ue_id)
                for tracked_key in self.ue_context.keys():
                    if str(tracked_key) == ue_id_str:
                        key = tracked_key
                        break

                is_new = key not in self.ue_context
                if is_new:
                    self.ue_context[key] = {
                        'slice': ue['s_nssai'],
                        'connected_at': now_ts,
                        'last_seen': now_ts
                    }
                    self.ue_traffic_state.setdefault(key, {'activated': False})
                    self.ue_breach_streak.setdefault(key, 0)
                    new_ue_detected = True
                    slice_info = self.ue_context[key]['slice']
                    self._log(
                        f"[UE_EVENT][CONNECT] ue_id={key} "
                        f"slice_sst={slice_info.get('sst')} slice_sd={slice_info.get('sd')}"
                    )
                else:
                    self.ue_context[key]['slice'] = ue['s_nssai']
                    self.ue_context[key]['last_seen'] = now_ts

            self._prune_stale_ues_locked(now_ts)

        if new_ue_detected:
            # Always resend current slice quotas when a new UE is detected, regardless
            # of whether the policy evaluation will produce a quota change.
            # Rationale: the gNB scheduler only calls rebind_active_ues_to_slices()
            # inside handle_slice_reconfiguration_request(), which is triggered
            # exclusively by an incoming RC CONTROL message.  If the policy evaluation
            # below finds no deficit (e.g. the UE just connected and has no traffic
            # yet), changed_slices is empty and no RC message is sent — leaving the
            # new UE permanently in DEFAULT_DRB (slice id<2) which bypasses all PRB
            # quota enforcement gates and causes fair-sharing with other slices.
            # Resending current quotas here guarantees rebind happens on every attach
            # event without touching the policy logic.
            self._send_rebind_rc_for_new_ue(e2_agent_id)
            self._evaluate_control_policy(e2_agent_id, force=True)
        self._flush_dashboard_state()

    def ue_throughput_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        super(TestXapp3, self).ue_throughput_callback(e2_agent_id, subscription_id, indication_hdr, indication_msg)
        self._flush_dashboard_state()

    def _slice_prb_callback(self, sst, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        super(TestXapp3, self)._slice_prb_callback(sst, e2_agent_id, subscription_id, indication_hdr, indication_msg)
        self._flush_dashboard_state()

    def _apply_initial_equal_split(self, e2_node_id):
        if not self.init_equal_split:
            self._log("[INIT] Equal-split initialization disabled by CLI option")
            return

        if not self.target_slices:
            self._log("[INIT][WARN] No target slices configured; skipping equal-split initialization")
            return

        max_ratios = self._build_near_equal_max_ratios()
        self._log("[INIT] Applying initial near-equal PRB allocation across slices")

        for sst in self.target_slices:
            policy = self.slice_policy[sst]
            policy['max'] = int(max_ratios[sst])

            if policy['min'] > policy['max']:
                policy['min'] = max(0, policy['max'])

            self._pending_control_reason = 'startup_equal_split'
            self._send_slice_prb_control(e2_node_id, sst, policy)
            self._log(
                f"[RC_CONTROL_EVENT][INIT] sst={sst} min={policy['min']} max={policy['max']} ded={policy['ded']}"
            )

        with self.state_lock:
            self._capture_initial_prb_baseline_locked()
        self._flush_dashboard_state()

    def _send_slice_prb_control(self, e2_node_id, sst, policy):
        reason = self._pending_control_reason
        if reason is None:
            reason = 'policy_update'

        super(TestXapp3, self)._send_slice_prb_control(e2_node_id, sst, policy)
        self._record_control_event(
            message_type='RIC_CONTROL_REQUEST',
            reason=reason,
            sst=sst,
            policy={
                'min': int(policy.get('min', 0)),
                'max': int(policy.get('max', 0)),
                'ded': int(policy.get('ded', 0))
            },
            details=f"e2_node_id={e2_node_id}"
        )
        self._pending_control_reason = None

    # ------------------------------------------------------------------
    # Main RMR loop
    # ------------------------------------------------------------------

    def _run(self):
        """Override base loop to keep ACK/FAILURE counters in dashboard state."""
        while self.running:
            sbuf = None
            try:
                sbuf = rmr.rmr_torcv_msg(self.rmr_client, None, 100)
                summary = rmr.message_summary(sbuf)
            except Exception:
                continue

            try:
                msg_state = summary.get(rmr.RMR_MS_MSG_STATE, None)
                if msg_state != 0:
                    continue

                msg_type = summary.get('message type', summary.get('mtype', None))
                if msg_type is None:
                    continue

                if msg_type == 12050:  # RIC_INDICATION
                    meid_val = summary.get('meid', b'')
                    if isinstance(meid_val, bytes):
                        e2_agent_id = meid_val.decode('utf-8', errors='ignore')
                    else:
                        e2_agent_id = str(meid_val)

                    data = rmr.get_payload(sbuf)
                    try:
                        e2_event_instance_id = summary.get('subscription id', summary.get('sub_id'))
                        if e2_event_instance_id is None:
                            raise ValueError('missing subscription id in RMR summary')

                        candidate_keys = [e2_event_instance_id]
                        if isinstance(e2_event_instance_id, bytes):
                            try:
                                decoded = e2_event_instance_id.decode('utf-8')
                                candidate_keys.append(decoded)
                                if decoded.isdigit():
                                    candidate_keys.append(int(decoded))
                            except Exception:
                                pass
                        elif isinstance(e2_event_instance_id, str):
                            try:
                                candidate_keys.append(int(e2_event_instance_id))
                            except Exception:
                                pass
                        else:
                            candidate_keys.append(str(e2_event_instance_id))

                        ric_indication = IndicationMsg()
                        ric_indication.decode(data)

                        subscription_obj = None
                        for key in candidate_keys:
                            subscription_obj = self.my_subscriptions.get(key, None)
                            if subscription_obj is not None:
                                break

                        if subscription_obj is not None and subscription_obj.callback_func is not None:
                            cb = subscription_obj.callback_func
                            self.indication_total_count += 1
                            if subscription_obj.e2sm_type == e2sm_types.E2SM_KPM:
                                self.indication_kpm_count += 1
                                indication_hdr, indication_msg = self.e2sm_kpm.unpack_ric_indication(ric_indication)
                                cb(e2_agent_id, e2_event_instance_id, indication_hdr, indication_msg)
                            else:
                                self.indication_rc_count += 1
                                cb(e2_agent_id,
                                   e2_event_instance_id,
                                   ric_indication.indication_header,
                                   ric_indication.indication_message)
                        else:
                            self.unmapped_indication_count += 1
                            if self.unmapped_indication_count <= 10:
                                self._log(
                                    f"[RMR][WARN] indication for unmapped subscription_id={e2_event_instance_id} "
                                    f"(candidates={candidate_keys}, known_keys={list(self.my_subscriptions.keys())})"
                                )
                    except Exception as ex:
                        self._log(f"[RMR][ERROR] indication decode/callback failed: {ex}")

                elif msg_type == 12041:  # RIC_CONTROL_ACK
                    self.control_ack_count += 1
                    self._log(f"[RC_CONTROL_EVENT][ACK] total_ack={self.control_ack_count}")
                    self._record_control_event(
                        message_type='RIC_CONTROL_ACK',
                        reason='acknowledged_by_ran',
                        details=f"total_ack={self.control_ack_count}"
                    )
                    self._flush_dashboard_state()

                elif msg_type == 12042:  # RIC_CONTROL_FAILURE
                    self.control_failure_count += 1
                    self._log(f"[RC_CONTROL_EVENT][FAILURE] total_failure={self.control_failure_count}")
                    self._record_control_event(
                        message_type='RIC_CONTROL_FAILURE',
                        reason='rejected_or_failed_at_ran',
                        details=f"total_failure={self.control_failure_count}"
                    )
                    self._flush_dashboard_state()
            except Exception as ex:
                self._log(f"[RMR][ERROR] main loop processing failed: {ex}")
            finally:
                if sbuf is not None:
                    try:
                        rmr.rmr_free_msg(sbuf)
                    except Exception:
                        pass

    def signal_handler(self, sig, frame):
        self._flush_dashboard_state()
        self._stop_dashboard()
        super(TestXapp3, self).signal_handler(sig, frame)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            'test_xapp_3 - Integrated RC+KPM closed-loop slice control xApp with '
            'asymmetric sharing graph (Emergency borrows from eMBB α=0.7 and IoT γ=0.3; '
            'eMBB borrows from IoT only).'
        )
    )
    parser.add_argument('--config', type=str, default='', help='xApp config path (optional)')
    parser.add_argument('--http_server_port', type=int, default=8097, help='HTTP server port')
    parser.add_argument('--rmr_port', type=int, default=4560, help='RMR port')
    parser.add_argument('--e2_node_id', type=str, default='gnbd_001_001_00019b_0', help='E2 Node ID')
    parser.add_argument('--kpm_ran_func_id', type=int, default=2, help='KPM RAN Function ID')
    parser.add_argument('--rc_ran_func_id', type=int, default=3, help='RC RAN Function ID')
    parser.add_argument('--report_period_ms', type=int, default=1000, help='KPM report/granularity period (ms)')
    parser.add_argument('--throughput_threshold', type=float, default=1000.0,
                        help='Threshold for UE throughput breach (same unit as KPM DRB.UEThp*)')
    parser.add_argument('--idle_prb_threshold', type=float, default=20.0,
                        help='Idle slice threshold for (RRU.PrbUsedDl + RRU.PrbUsedUl)')
    parser.add_argument('--prb_shift_step', type=int, default=5,
                        help='PRB ratio step moved from donor slice to target slice')
    parser.add_argument('--control_cooldown_s', type=float, default=5.0,
                        help='Minimum seconds between control actions')
    parser.add_argument('--ue_attach_grace_s', type=float, default=20.0,
                        help='Ignore threshold checks for a UE during first N seconds after attach')
    parser.add_argument('--activation_throughput_kbps', type=float, default=100.0,
                        help='Minimum throughput to mark UE traffic as active before threshold checks')
    parser.add_argument('--min_consecutive_breaches', type=int, default=3,
                        help='Trigger RC action only after K consecutive below-threshold samples per UE')
    parser.add_argument('--init_equal_split', type=int, default=1,
                        help='Send initial near-equal RC allocation at startup (1=enabled, 0=disabled)')
    parser.add_argument('--init_min_ratio', type=int, default=10,
                        help='Initial min ratio used during startup equal-split control')
    parser.add_argument('--min_quotas', type=str, default='',
                        help='Comma-separated per-slice minimum PRB quota percentages in SST order '
                             '(e.g. "20,30,10"). Overrides hardcoded defaults [20,30,10]. '
                             'Values are clamped to [0, 100].')
    parser.add_argument('--slices', type=str, default='1,2,3',
                        help='Comma-separated SST list to monitor/control, e.g. 1,2,3')
    parser.add_argument('--sd', type=int, default=0,
                        help='Slice SD used in KPM label + RC control (0..16777215)')
    parser.add_argument('--dashboard_enabled', type=int, default=1,
                        help='Launch Streamlit dashboard automatically (1=enabled, 0=disabled)')
    parser.add_argument('--dashboard_port', type=int, default=8503,
                        help='Streamlit dashboard TCP port')
    parser.add_argument('--dashboard_state_file', type=str, default='/tmp/test_xapp_3_state.json',
                        help='JSON state file shared between xApp and dashboard')
    parser.add_argument('--dashboard_refresh_seconds', type=float, default=0.5,
                        help='Dashboard browser auto-refresh period in seconds')
    parser.add_argument('--dashboard_python', type=str, default='',
                        help='Python interpreter used to launch Streamlit (defaults to current Python)')
    parser.add_argument('--cell_prbs', type=int, default=162,
                        help='Total PRBs in the cell; used for PRB-quota utilization and saturation detection')
    parser.add_argument('--tdd_dl_fraction', type=float, default=0.6429,
                        help='TDD downlink duty-cycle fraction '
                             '(N310 6D+1S(6 DL sym)+3U = 0.6429; FDD = 1.0).')

    args = parser.parse_args()

    routed_default_ports = {4560, 4561, 4562, 4563}
    if args.rmr_port not in routed_default_ports:
        print(
            f"[WARNING] RMR port {args.rmr_port} may not be routable with current RIC route table. "
            f"Common configured ports: {sorted(routed_default_ports)}"
        )

    seed_rt_path = os.environ.get('RMR_SEED_RT', '')
    has_ind = _route_table_has_port(seed_rt_path, 12050, args.rmr_port)
    has_ack = _route_table_has_port(seed_rt_path, 12041, args.rmr_port)
    has_fail = _route_table_has_port(seed_rt_path, 12042, args.rmr_port)

    if has_ind is False:
        print(
            f"[WARNING] Route-table mismatch: selected RMR port {args.rmr_port} is not listed for "
            f"RIC_INDICATION (12050) in {seed_rt_path}."
        )

    if (has_ack is False) or (has_fail is False):
        print(
            f"[WARNING] Route-table mismatch: selected RMR port {args.rmr_port} is not listed for "
            f"RIC_CONTROL_ACK/FAILURE (12041/12042) in {seed_rt_path}. "
            f"Control ACK metrics may stay at zero."
        )

    if not (0 <= args.sd <= 0xFFFFFF):
        raise ValueError(f"SD must be in [0, 16777215], got {args.sd}")

    if args.prb_shift_step <= 0:
        raise ValueError('--prb_shift_step must be > 0')

    if args.ue_attach_grace_s < 0:
        raise ValueError('--ue_attach_grace_s must be >= 0')

    if args.activation_throughput_kbps < 0:
        raise ValueError('--activation_throughput_kbps must be >= 0')

    if args.min_consecutive_breaches < 1:
        raise ValueError('--min_consecutive_breaches must be >= 1')

    if not (0 <= args.init_min_ratio <= 100):
        raise ValueError('--init_min_ratio must be in [0, 100]')

    if not (0.1 <= args.tdd_dl_fraction <= 1.0):
        raise ValueError('--tdd_dl_fraction must be in [0.1, 1.0]')

    parsed_min_quotas = []
    if args.min_quotas.strip():
        for token in args.min_quotas.split(','):
            try:
                v = int(token.strip())
            except ValueError:
                raise ValueError(f'--min_quotas: non-integer value "{token.strip()}"')
            if not (0 <= v <= 100):
                raise ValueError(f'--min_quotas: value {v} out of range [0, 100]')
            parsed_min_quotas.append(v)

    if args.dashboard_port < 1 or args.dashboard_port > 65535:
        raise ValueError('--dashboard_port must be in [1, 65535]')

    if args.dashboard_refresh_seconds <= 0:
        raise ValueError('--dashboard_refresh_seconds must be > 0')

    if args.cell_prbs < 1:
        raise ValueError('--cell_prbs must be >= 1')

    target_slices = parse_slices(args.slices)

    print('=' * 90)
    print('Initializing test_xapp_3')
    print('Policy: asymmetric sharing — Emergency←eMBB(α=0.7) + Emergency←IoT(γ=0.3); eMBB←IoT')
    print('=' * 90)
    print(f"E2 Node ID: {args.e2_node_id}")
    print(f"KPM RAN Function ID: {args.kpm_ran_func_id}")
    print(f"RC RAN Function ID: {args.rc_ran_func_id}")
    print(f"Slices: {target_slices} (SD={args.sd})")
    print(f"Report period: {args.report_period_ms} ms")
    print(f"Throughput threshold: {args.throughput_threshold}")
    print(f"Idle PRB threshold: {args.idle_prb_threshold}")
    print(f"PRB shift step: {args.prb_shift_step}")
    print(f"Control cooldown: {args.control_cooldown_s} s")
    print(f"UE attach grace: {args.ue_attach_grace_s} s")
    print(f"Traffic activation threshold: {args.activation_throughput_kbps} kbps")
    print(f"Min consecutive breaches: {args.min_consecutive_breaches}")
    print(f"Init equal split: {bool(args.init_equal_split)}")
    print(f"Init min ratio: {args.init_min_ratio}")
    print(f"Min quotas (per-slice %): {parsed_min_quotas if parsed_min_quotas else '[20,30,10] (defaults)'}")
    print(f"Dashboard enabled: {bool(args.dashboard_enabled)}")
    print(f"Dashboard port: {args.dashboard_port}")
    print(f"Dashboard state file: {args.dashboard_state_file}")
    print(f"Dashboard refresh: {args.dashboard_refresh_seconds} s")
    print(f"Dashboard python: {args.dashboard_python if args.dashboard_python else sys.executable}")
    print(f"Cell PRBs: {args.cell_prbs}")
    print(
        f"TDD DL fraction: {args.tdd_dl_fraction} "
        f"(effective PRB-sat threshold: {0.90 * args.tdd_dl_fraction:.3f})"
    )
    print(f"HTTP port: {args.http_server_port}, RMR port: {args.rmr_port}")
    print('=' * 90)

    xapp = TestXapp3(
        config=args.config,
        http_server_port=args.http_server_port,
        rmr_port=args.rmr_port,
        ran_func_kpm=args.kpm_ran_func_id,
        ran_func_rc=args.rc_ran_func_id,
        report_period_ms=args.report_period_ms,
        throughput_threshold=args.throughput_threshold,
        idle_prb_threshold=args.idle_prb_threshold,
        prb_shift_step=args.prb_shift_step,
        control_cooldown_s=args.control_cooldown_s,
        ue_attach_grace_s=args.ue_attach_grace_s,
        activation_throughput_kbps=args.activation_throughput_kbps,
        min_consecutive_breaches=args.min_consecutive_breaches,
        init_equal_split=bool(args.init_equal_split),
        init_min_ratio=args.init_min_ratio,
        min_quotas=parsed_min_quotas,
        target_slices=target_slices,
        sd=args.sd,
        cell_prbs=args.cell_prbs,
        tdd_dl_fraction=args.tdd_dl_fraction,
        dashboard_enabled=bool(args.dashboard_enabled),
        dashboard_port=args.dashboard_port,
        dashboard_state_file=args.dashboard_state_file,
        dashboard_refresh_seconds=args.dashboard_refresh_seconds,
        dashboard_python=args.dashboard_python
    )

    signal.signal(signal.SIGQUIT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)
    signal.signal(signal.SIGINT, xapp.signal_handler)

    xapp.start(args.e2_node_id)


if __name__ == '__main__':
    main()
