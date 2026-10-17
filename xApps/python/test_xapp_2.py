#!/usr/bin/env python3

import argparse
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


class TestXapp2(TestXapp1):
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
        super(TestXapp2, self).__init__(
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
        self.policy_min_quota_defaults = list(min_quotas) if min_quotas else [20, 30, 7]
        # Per-slice throughput thresholds (bps).  All three slices use 50 Mbps so
        # that ordinary iperf traffic at a few Mbps still registers as a deficit
        # and the PRB-saturation path (below) is the primary expansion signal.
        self.policy_threshold_bps_defaults = [50e6, 50e6, 1e6]

        # TDD downlink duty-cycle fraction: KPM RRU.PrbUsedDl is averaged across
        # ALL slots in the granularity window (DL + UL + special).  For N310 TDD
        # 6DL+1Special(6DL sym/14)+3UL the fraction is (6*14+6)/(10*14) ≈ 0.643.
        # Set to 1.0 for FDD cells.  Used to scale PRB_SAT_THRESHOLD so that a
        # fully-loaded TDD slice correctly triggers the saturation expansion path.
        self.tdd_dl_fraction = float(max(0.1, min(1.0, tdd_dl_fraction)))

        self.policy_min_quota_by_sst = {}
        self.policy_threshold_bps_by_sst = {}
        self.policy_sharing_weights = {}
        # Reset PRBs after 40 s of no traffic across all slices (even with UEs connected).
        self.idle_reset_timeout_s = 40.0
        self.last_non_idle_traffic_ts = datetime.datetime.now().timestamp()
        # Tracks the timestamp when ue_context first became empty.  None while UEs
        # are present.  PRBs reset after this has been set for >= 10 s.
        self.no_ues_since_ts = None
        self.initial_prb_max_by_sst = {}
        self.initial_prb_baseline_ready = False
        # Mirror the parent's updated floor (30 s).  RC Style 4 is event-triggered;
        # a healthy UE may produce no RC events for many minutes.
        self.ue_disconnect_timeout_s = max(30.0, 10.0 * (float(self.report_period_ms) / 1000.0))

        self.control_event_lock = Lock()
        self.control_event_log = []
        self.max_control_events = 200
        self._pending_control_reason = None
        self._init_policy_profile()

        self._flush_dashboard_state()
        self._start_dashboard()

    def _build_near_equal_max_ratios(self):
        """Fixed initial PRB split: SST order 0→45%, 1→30%, 2→25%.  Falls back to
        the parent's near-equal distribution when fewer or more than 3 slices are
        configured."""
        if len(self.target_slices) == 3:
            fixed = [50, 30, 20]  # STATIC_POLICY: SST1=50%, SST2=30%, SST3=20%
            return {sst: fixed[idx] for idx, sst in enumerate(self.target_slices)}
        return super()._build_near_equal_max_ratios()

    def _dashboard_script_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_xapp_2_dashboard.py')

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
                # Per-UE lookup failed: RC and KPM may encode ue_id differently.
                # Fall back to the slice-level PRB timestamp (updated every
                # report_period_ms by the KPM subscription) so a UE that has
                # active slice traffic is never pruned due to an ID-encoding mismatch.
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

    def _init_policy_profile(self):
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
            embb_sst = slices[0]
            emergency_sst = slices[1]
            iot_sst = slices[2]

            # Both eMBB and Emergency can only expand into IoT's idle surplus.
            # They must NEVER borrow from each other — cross-slice stealing between
            # SST1 and SST2 causes the fair-contention behaviour where one active
            # slice steadily drains the other's PRB budget.
            self.policy_sharing_weights = {
                emergency_sst: {
                    iot_sst: 1.0
                },
                embb_sst: {
                    iot_sst: 1.0
                }
            }
        else:
            self.policy_sharing_weights = {}
            self._log('[POLICY][WARN] Fewer than 3 slices configured; sharing graph disabled')

        self._log(
            f"[POLICY] min_quota={self.policy_min_quota_by_sst}, thresh_bps={self.policy_threshold_bps_by_sst}"
        )

    def _normalized_slice_util(self, sst):
        """Return the fraction [0, inf) of the slice's own PRB quota currently in use.
        Values < 1.0 mean under-utilised; >= 1.0 means at or above budget ceiling.

        IMPORTANT: slice_policy['max'] is stored as a float to allow sub-integer
        per-cycle accumulation.  However the E2 RC control message always sends
        int(round(quota_pct)) to the gNB.  We must use that same integer here so
        the denominator matches the PRBs actually allocated by the gNB.  Using the
        raw float inflates allocated_prbs and artificially reduces utilization,
        pushing it below _PRB_SAT_THRESHOLD and blocking further PRB transfers.
        """
        load = self._safe_prb_load(sst)   # dl_raw + ul_raw (absolute PRB count)
        if load is None:
            return None
        quota_pct_raw = float((self.slice_policy.get(sst) or {}).get('max') or 0)
        # Use the integer quota that was actually sent to the gNB via E2 RC control.
        quota_pct = float(int(round(quota_pct_raw)))
        if quota_pct <= 0.0:
            return 0.0
        allocated_prbs = quota_pct / 100.0 * float(self.cell_prbs)
        if allocated_prbs <= 0.0:
            return 0.0
        return float(load) / allocated_prbs

    def _compute_avg_tp_bps_per_slice_locked(self):
        per_slice_samples = {sst: [] for sst in self.target_slices}

        # Idle floor: below this kbps the UE is treated as non-contributing (stopped
        # traffic or residual control-plane only).  Avoids residual UL ACK traffic
        # (dl=None, ul=tiny) producing an enormous deficit when DL drops out.
        idle_floor_kbps = max(5.0, self.activation_throughput_kbps / 20.0)

        for ue_id, thp in self.ue_throughput.items():
            sst = self._map_ue_to_slice(ue_id)
            if sst not in per_slice_samples:
                continue

            dl = thp.get('dl')
            ul = thp.get('ul')
            # Use max(DL, UL) so that stopping DL traffic (dl=None) while UL carries
            # residual control traffic (tiny ul) does not masquerade as low-throughput.
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
            # Use max(DL, UL) — consistent with _compute_avg_tp_bps_per_slice_locked.
            # The previous 'dl if dl is not None else ul' formula returned False when
            # DL was None (e.g. brief KPM gap) even if UL indicated the UE was active,
            # causing last_non_idle_traffic_ts to stop updating and idle_reset to fire.
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
            f"[POLICY][RESET_TO_INITIAL] reason={reason} changed_slices={changed_slices} baseline={self.initial_prb_max_by_sst}"
        )
        return True

    def _evaluate_control_policy(self, e2_node_id, force=False):
        now = datetime.datetime.now().timestamp()

        with self.state_lock:
            # Eagerly prune stale UEs on every evaluation cycle.  Since RC Style 4
            # is event-triggered, a UE that disconnects without triggering an event
            # would stay in ue_context forever without this periodic check.
            self._prune_stale_ues_locked(now)

            has_active = self._has_active_traffic_locked()
            if has_active:
                self.last_non_idle_traffic_ts = now

            # RC Style 4 is event-triggered, so ue_context may be transiently
            # empty between RC events even while UEs are active.  Only treat the
            # cell as truly empty when KPM also shows no active traffic, so a
            # brief RC-event gap does not block the entire policy evaluation path.
            no_ues_now = len(self.ue_context) == 0 and not has_active
            if no_ues_now:
                if self.no_ues_since_ts is None:
                    self.no_ues_since_ts = now
            else:
                self.no_ues_since_ts = None

            # ---- STATIC_POLICY_START -----------------------------------------------
            # Dynamic reallocation is disabled. PRBs are fixed at startup values:
            #   SST 1 → 50 %  |  SST 2 → 30 %  |  SST 3 → 20 %
            # To restore dynamic policy: remove the 'return' below and delete this
            # comment block through the STATIC_POLICY_END marker.
            return
            # ---- STATIC_POLICY_END -------------------------------------------------

            # Condition 1: no UEs across all slices for more than 10 s.
            no_ues_long_enough = (
                self.no_ues_since_ts is not None
                and (now - self.no_ues_since_ts) >= 10.0
            )
            # Condition 2: no traffic across all slices for more than 40 s
            # (even when UEs are still connected).
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

            # Per-slice active UE count (needed for saturation-based deficit guard).
            ue_counts = self._active_ue_count_per_slice()

            _PRB_SAT_THRESHOLD = 0.90 * self.tdd_dl_fraction

            deficit = {}
            for sst in self.target_slices:
                threshold = float(self.policy_threshold_bps_by_sst.get(sst, 0.0))
                tp = float(avg_tp.get(sst, 0.0))
                quota = float(current_quota[sst])

                if tp <= 0.0 or tp >= threshold:
                    deficit[sst] = 0.0
                    continue

                # Guard: only grow quota when the slice is also PRB-constrained.
                #
                # A throughput shortfall (tp < threshold) can have two causes:
                #   (a) The slice does not have enough PRBs — quota should grow.
                #   (b) The UE's peak demand is below the threshold at the current
                #       MCS / SINR — more PRBs would not help.
                #
                # KPM PRB utilisation distinguishes the two cases:
                #   • util >= _PRB_SAT_THRESHOLD  → case (a), PRB-bottlenecked.
                #   • util <  _PRB_SAT_THRESHOLD  → case (b), quota already exceeds
                #     demand; growing it further only steals budget from other slices
                #     without improving throughput.
                #   • util is None                → no KPM data; allow deficit so the
                #     saturation pass (Signal B) can still drive quota growth when
                #     the slice is clearly active.
                #
                # Example: SST2 iperf at 46.7 Mbps, threshold 50 Mbps, quota 70 %
                # (113 PRBs), actual use 53 PRBs → util = 0.47 < 0.546 → NOT
                # PRB-limited → deficit = 0, quota stays at its current level.
                util_for_gate = util_prb.get(sst)
                if util_for_gate is not None and util_for_gate < _PRB_SAT_THRESHOLD:
                    deficit[sst] = 0.0
                    self._log(
                        f"[POLICY][TP_DEFICIT_SUPPRESSED] sst={sst} tp={tp:.0f}bps "
                        f"threshold={threshold:.0f}bps but util={util_for_gate:.3f} < "
                        f"prb_sat_threshold={_PRB_SAT_THRESHOLD:.3f} → slice not PRB-limited, "
                        f"deficit suppressed"
                    )
                    continue

                required = quota * (threshold / max(tp, 1e-9)) - quota
                deficit[sst] = max(0.0, required)

            # PRB-saturation deficit: detect slices that are PRB-bottlenecked even
            # when their throughput is at or above the target (throughput path gives
            # deficit=0 in that case).  Two independent signals are checked:
            #
            #   Signal A — KPM RRU.PrbUsedDl shows high utilization.
            #     RRU.PrbUsedDl is averaged across ALL slots.  For N310 TDD
            #     6D+1S+3U the DL duty-cycle is ~0.643, so even 100% DL PRB fill
            #     only reads as ~0.637 of the quota.  Scaling by tdd_dl_fraction
            #     keeps the threshold reachable: 0.90 × 0.643 = 0.579.
            #     Borrowing is triggered only when ≥90% of DL slots are saturated.
            #
            #   Signal B — UE in RC context + per-UE throughput ≥ 1 % of target.
            #     When srsRAN does not report RRU.PrbUsedDl for a slice (util=None)
            #     the primary signal is unavailable.  If a UE IS registered and the
            #     per-UE KPM throughput exceeds a minimal activity floor (1 % of the
            #     slice's target), the slice is clearly active and likely PRB-limited
            #     (the throughput path already set deficit=0 because tp ≥ threshold).
            #     Inject a conservative step-sized deficit so the borrowing path fires.
            #
            # Guard: skip completely idle slices — no RC-registered UEs AND no KPM
            # PRB activity (util=None or 0).  Either signal alone is sufficient.
            for sst in self.target_slices:
                if deficit.get(sst, 0.0) > 0.0:
                    continue  # tp-based deficit already active

                util = util_prb.get(sst)
                has_ues = ue_counts.get(sst, 0) > 0
                tp = float(avg_tp.get(sst, 0.0))

                # Skip demonstrably idle slices (no UEs in RC context AND no PRB signal).
                if not has_ues and (util is None or util <= 0.0):
                    continue

                # Signal A: KPM slice PRB data shows saturation.
                prb_saturated = (util is not None and util >= _PRB_SAT_THRESHOLD)

                # Signal B: UE present in RC context, slice PRB data unavailable (util=None),
                # but per-UE throughput confirms the slice is actively carrying traffic.
                # Threshold: 1 % of the slice target (e.g. 500 kbps for a 50 Mbps target).
                # This distinguishes idle/just-connected UEs (tp ≈ 0) from iperf-loaded ones.
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

            for receiver_sst in self.target_slices:
                required = deficit.get(receiver_sst, 0.0)
                if required <= 0.0:
                    continue

                graph = self.policy_sharing_weights.get(receiver_sst, {})
                if not graph:
                    continue

                eligible = []
                for donor_sst, weight in graph.items():
                    util = util_prb.get(donor_sst)
                    # Guard: only block a donor when the KPM metric is completely
                    # absent (util is None) AND the slice has connected UEs.  This
                    # catches the genuine metric-gap case (gNB stopped reporting
                    # s_nssai PRBs after a UE reconnect / slice reassignment).
                    #
                    # Do NOT block when util == 0.0 — that is a valid KPM reading
                    # meaning the slice is genuinely idle (UEs connected but not
                    # transmitting).  The previous guard (util is None or util <= 0.0)
                    # incorrectly treated a confirmed-zero as a metric gap, which
                    # prevented idle donor slices with attached UEs from ever
                    # donating PRBs to a saturated receiver.
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

                weight_sum = sum(weight for _, weight in eligible)
                if weight_sum <= 0.0:
                    continue

                for donor_sst, weight in eligible:
                    weighted_need = required * (weight / weight_sum)
                    transfer = min(weighted_need, donor_available[donor_sst])
                    if transfer <= 0.0:
                        continue

                    donor_available[donor_sst] -= transfer
                    total_out[donor_sst] += transfer
                    total_in[receiver_sst] += transfer

            for sst in self.target_slices:
                proposed_quota[sst] = current_quota[sst] + total_in[sst] - total_out[sst]
                proposed_quota[sst] = max(min_quota[sst], min(100.0, proposed_quota[sst]))

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
                    f"sst={sst} tp={avg_tp[sst]:.1f}bps util={util_prb[sst]} deficit={deficit[sst]:.2f} "
                    f"quota(old={current_quota[sst]:.2f},new={ema_quota[sst]:.2f})"
                )
            self._log("[POLICY][REALLOC] " + " | ".join(log_chunks))

    def _subscription_response_callback(self, name, path, data, ctype):
        return super(TestXapp2, self)._subscription_response_callback(name, path, data, ctype)

    def subscribe(self, e2_node_id, ran_function_id, event_trigger_def, action_def, indication_callback, e2sm_type):
        # Retry for up to ~5 minutes on 503 / Service Unavailable so that a slow
        # E2 node or RIC restart does not abort the subscription thread before
        # _apply_initial_equal_split is called.  Exponential backoff starts at 1 s
        # and is capped at 8 s to avoid long silent gaps in the logs.
        max_attempts = 120
        base_delay_s = 1.0
        max_delay_s = 8.0

        last_ex = None
        for attempt in range(1, max_attempts + 1):
            try:
                return super(TestXapp2, self).subscribe(
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

                self._log(
                    f"[SUBSCRIBE][ERROR] attempt={attempt}/{max_attempts} failed: {ex}"
                )
                raise

        if last_ex is not None:
            raise last_ex

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
        try:
            with self.state_lock:
                state = self._build_dashboard_state_locked()

            state_dir = os.path.dirname(os.path.abspath(self.dashboard_state_file))
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)

            # Use a unique temp file per call so concurrent threads never race on the
            # same path.  Previously all threads shared the single fixed path
            # '<state>.json.tmp'; when two threads called os.replace() concurrently the
            # second rename raised ENOENT because the first had already removed the
            # directory entry.
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
                f"Install streamlit in dashboard env or set --dashboard_python <path-to-streamlit-env-python>"
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

    def rc_style4_indication_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        _ = (indication_hdr,)

        self.rc_callback_count += 1
        if self.rc_callback_count == 1:
            self._log(
                f"[RC] First RC Style 4 indication received from {e2_agent_id} (sub_id={subscription_id}, bytes={len(indication_msg)})"
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
                    f"[RC] Empty/undecodable UE list in RC indication (count={self.rc_empty_payload_count}, bytes={len(indication_msg)})"
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
                        f"[UE_EVENT][CONNECT] ue_id={key} slice_sst={slice_info.get('sst')} slice_sd={slice_info.get('sd')}"
                    )
                else:
                    # Existing UE: update slice and liveness timestamp.
                    # NOTE: do NOT use a gap-based reconnect heuristic here.
                    # RC Style 4 is event-triggered; a UE can be connected for
                    # hours without producing any RC events, so a large gap is
                    # normal steady-state behaviour, not evidence of a reconnect.
                    # True reconnects are handled naturally: after ue_disconnect_timeout_s
                    # the UE is pruned, and the next RC event for that ue_id enters
                    # the is_new=True branch above, resetting all state correctly.
                    self.ue_context[key]['slice'] = ue['s_nssai']
                    self.ue_context[key]['last_seen'] = now_ts

            self._prune_stale_ues_locked(now_ts)

        if new_ue_detected:
            self._evaluate_control_policy(e2_agent_id, force=True)
        self._flush_dashboard_state()

    def ue_throughput_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        super(TestXapp2, self).ue_throughput_callback(e2_agent_id, subscription_id, indication_hdr, indication_msg)
        self._flush_dashboard_state()

    def _slice_prb_callback(self, sst, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        super(TestXapp2, self)._slice_prb_callback(sst, e2_agent_id, subscription_id, indication_hdr, indication_msg)
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
            policy['min'] = policy['max']  # STATIC_POLICY: min == max, quota is fixed

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

        super(TestXapp2, self)._send_slice_prb_control(e2_node_id, sst, policy)
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

    def _run(self):
        """
        Override base loop to:
        - keep ACK/FAILURE counters in dashboard state up-to-date
        - persist control ACK/FAILURE events for CLI-style dashboard pane
        """
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
        super(TestXapp2, self).signal_handler(sig, frame)


def main():
    parser = argparse.ArgumentParser(
        description='test_xapp_2 - Integrated RC+KPM closed-loop slice control test xApp with Streamlit dashboard'
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
                             '(e.g. "5,5,5"). Overrides hardcoded defaults [20,30,10]. '
                             'Values are clamped to [0, 100].')
    parser.add_argument('--slices', type=str, default='1,2,3',
                        help='Comma-separated SST list to monitor/control, e.g. 1,2,3')
    parser.add_argument('--sd', type=int, default=0,
                        help='Slice SD used in KPM label + RC control (0..16777215)')
    parser.add_argument('--dashboard_enabled', type=int, default=1,
                        help='Launch Streamlit dashboard automatically (1=enabled, 0=disabled)')
    parser.add_argument('--dashboard_port', type=int, default=8502,
                        help='Streamlit dashboard TCP port')
    parser.add_argument('--dashboard_state_file', type=str, default='/tmp/test_xapp_2_state.json',
                        help='JSON state file shared between xApp and dashboard')
    parser.add_argument('--dashboard_refresh_seconds', type=float, default=0.5,
                        help='Dashboard browser auto-refresh period in seconds')
    parser.add_argument('--dashboard_python', type=str, default='',
                        help='Python interpreter used to launch Streamlit (defaults to current Python)')
    parser.add_argument('--cell_prbs', type=int, default=162,
                        help='Total PRBs in the cell; used for PRB-quota utilization and saturation detection')
    parser.add_argument('--tdd_dl_fraction', type=float, default=0.6429,
                        help='TDD downlink duty-cycle fraction: fraction of slots that are DL '
                             '(KPM PRB metrics are averaged over all slots, so this scales the '
                             'PRB-saturation threshold to keep it reachable). '
                             'N310 6D+1S(6 DL sym)+3U = 0.6429. FDD = 1.0.')

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
    print('Initializing test_xapp_2')
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
    print(f"TDD DL fraction: {args.tdd_dl_fraction} (effective PRB-sat threshold: {0.90 * args.tdd_dl_fraction:.3f})")
    print(f"HTTP port: {args.http_server_port}, RMR port: {args.rmr_port}")
    print('=' * 90)

    xapp = TestXapp2(
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