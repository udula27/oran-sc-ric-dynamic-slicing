#!/usr/bin/env python3

"""
Integrated closed-loop test xApp.

Features:
- Tracks UE context via E2SM-RC Report Style 4 indications.
- Tracks UE throughput via E2SM-KPM (Report Style 4).
- Tracks per-slice PRB usage (RRU.PrbUsedDl/Ul with SLICE_ID_LABEL).
- Applies simple closed-loop RC control:
  If UE throughput in a slice drops below threshold, move PRB budget from an idle slice.
"""

import argparse
import signal
import time
import datetime
from threading import Lock

from ricxappframe.xapp_frame import rmr
from ricxappframe.e2ap.asn1 import IndicationMsg

from lib.xAppBase import xAppBase
from lib.e2sm_kpm_module import e2sm_types


class TestXapp1(xAppBase):
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
                 target_slices,
                 sd,
                 cell_prbs=162):
        super(TestXapp1, self).__init__(config, http_server_port, rmr_port)

        self.e2sm_kpm.set_ran_func_id(ran_func_kpm)
        self.e2sm_rc.set_ran_func_id(ran_func_rc)

        self.report_period_ms = report_period_ms
        self.throughput_threshold = throughput_threshold
        self.idle_prb_threshold = idle_prb_threshold
        self.prb_shift_step = prb_shift_step
        self.control_cooldown_s = control_cooldown_s
        self.ue_attach_grace_s = ue_attach_grace_s
        self.activation_throughput_kbps = activation_throughput_kbps
        self.min_consecutive_breaches = min_consecutive_breaches
        self.init_equal_split = init_equal_split
        self.init_min_ratio = init_min_ratio
        self.target_slices = sorted(set(target_slices))
        self.sd = sd
        self.cell_prbs = max(1, int(cell_prbs))

        self.state_lock = Lock()

        # UE context from RC Style 4: ue_id -> {'slice': {'sst': int, 'sd': int|None}, 'last_seen': ts}
        self.ue_context = {}

        # Last UE throughput snapshot from KPM: ue_id -> {'dl': float|None, 'ul': float|None, 'ts': float}
        self.ue_throughput = {}

        # UE traffic activation state: ue_id -> {'activated': bool}
        # A UE becomes activated when any throughput sample exceeds activation_throughput_kbps.
        self.ue_traffic_state = {}

        # Consecutive below-threshold counters: ue_id -> int
        self.ue_breach_streak = {}

        # Seconds without an RC indication before a UE is pruned as disconnected.
        # RC Style 4 is event-triggered (not periodic), so absent UEs are never
        # listed; we rely on this timeout to detect disconnects.  Use a generous
        # 30-second floor: a stably-connected UE produces no RC events after the
        # initial admit, so a 5-second floor causes immediate false-pruning.
        self.ue_disconnect_timeout_s = max(30.0, 10.0 * (float(report_period_ms) / 1000.0))

        # Last per-slice PRB usage from KPM: sst -> {'dl': float|None, 'ul': float|None, 'ts': float}
        self.slice_prb = {sst: {'dl': None, 'ul': None, 'ts': 0.0} for sst in self.target_slices}

        # Local policy state (simple PRB ratio model used by RC control)
        self.slice_policy = {
            sst: {
                'min': int(self.init_min_ratio),
                'max': 40,
                'ded': 0
            }
            for sst in self.target_slices
        }

        self.last_control_ts = 0.0

        self.control_ack_count = 0
        self.control_failure_count = 0
        self.indication_total_count = 0
        self.indication_kpm_count = 0
        self.indication_rc_count = 0
        self.unmapped_indication_count = 0
        self.rc_callback_count = 0
        self.rc_empty_payload_count = 0

    # ----------------------------- Logging helpers -----------------------------

    @staticmethod
    def _ts_str():
        return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _log(self, msg):
        print(f"[{self._ts_str()}] {msg}")

    # ----------------------------- RC Style 4 parsing -----------------------------

    @staticmethod
    def _decode_rc_style4_indication(indication_msg_bytes):
        """
        Best-effort parser aligned with existing validator xApp in this workspace.

        Returns:
          list of dicts: [{'ue_id': int|str, 's_nssai': {'sst': int|None, 'sd': int|None}}]
        """
        ue_info_list = []
        if not indication_msg_bytes or len(indication_msg_bytes) < 2:
            return ue_info_list

        data = indication_msg_bytes

        # Known compact 14-byte format used by this patch line.
        if len(data) <= 20:
            f1ap_id = data[5] if len(data) >= 6 else None
            sst = data[13] if len(data) >= 14 else None
            ue_id = f1ap_id if f1ap_id is not None else 'unknown'
            ue_info_list.append({
                'ue_id': ue_id,
                's_nssai': {'sst': sst, 'sd': None}
            })
            return ue_info_list

        # Fallback pattern scan for larger payloads.
        for idx in range(len(data) - 4):
            potential_sst = data[idx]
            if 1 <= potential_sst <= 255:
                potential_sd = (data[idx + 1] << 16) | (data[idx + 2] << 8) | data[idx + 3]
                ue_info_list.append({
                    'ue_id': 'unknown',
                    's_nssai': {'sst': potential_sst, 'sd': potential_sd if potential_sd > 0 else None}
                })
                break

        return ue_info_list

    # ----------------------------- Callbacks -----------------------------

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
            return

        if not current_ues:
            self.rc_empty_payload_count += 1
            if self.rc_empty_payload_count <= 5:
                self._log(
                    f"[RC] Empty/undecodable UE list in RC indication (count={self.rc_empty_payload_count}, bytes={len(indication_msg)})"
                )

        now_ts = time.time()
        with self.state_lock:
            for ue in current_ues:
                ue_id = ue['ue_id']
                if ue_id is None:
                    continue
                prev = self.ue_context.get(ue_id)
                if prev is None:
                    # New UE: full initialisation.
                    self.ue_context[ue_id] = {
                        'slice': ue['s_nssai'],
                        'connected_at': now_ts,
                        'last_seen': now_ts
                    }
                    self.ue_traffic_state.setdefault(ue_id, {'activated': False})
                    self.ue_breach_streak.setdefault(ue_id, 0)
                    slice_info = ue['s_nssai']
                    self._log(
                        f"[UE_EVENT][CONNECT] ue_id={ue_id} slice_sst={slice_info.get('sst')} slice_sd={slice_info.get('sd')}"
                    )
                else:
                    # Existing UE: freshen last_seen.  A large gap since the last
                    # RC indication implies the UE re-attached; reset traffic-
                    # activation and breach streak so the attach-grace period applies.
                    gap = now_ts - float(prev.get('last_seen', now_ts) or now_ts)
                    if gap > self.ue_disconnect_timeout_s:
                        self.ue_context[ue_id]['connected_at'] = now_ts
                        self.ue_traffic_state[ue_id] = {'activated': False}
                        self.ue_breach_streak[ue_id] = 0
                        slice_info = ue['s_nssai']
                        self._log(
                            f"[UE_EVENT][RECONNECT] ue_id={ue_id} gap_s={gap:.1f} "
                            f"slice_sst={slice_info.get('sst')} slice_sd={slice_info.get('sd')}"
                        )
                    self.ue_context[ue_id]['slice'] = ue['s_nssai']
                    self.ue_context[ue_id]['last_seen'] = now_ts

            # Prune UEs not seen within the disconnect timeout.  RC Style 4 is
            # event-triggered so absent UEs are never mentioned; this is the only
            # mechanism to detect disconnects in the base class.
            stale_ids = [
                uid for uid, ctx in self.ue_context.items()
                if (now_ts - float(ctx.get('last_seen', 0.0) or 0.0)) > self.ue_disconnect_timeout_s
            ]
            for ue_id in stale_ids:
                old = self.ue_context.pop(ue_id, None)
                self.ue_traffic_state.pop(ue_id, None)
                self.ue_breach_streak.pop(ue_id, None)
                slice_info = old['slice'] if old else {'sst': None, 'sd': None}
                self._log(
                    f"[UE_EVENT][DISCONNECT] ue_id={ue_id} "
                    f"slice_sst={slice_info.get('sst')} slice_sd={slice_info.get('sd')}"
                )

    def ue_throughput_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        _ = (e2_agent_id, subscription_id)
        try:
            _ = self.e2sm_kpm.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
        except Exception as ex:
            self._log(f"[KPM_UE][ERROR] Failed to decode throughput indication: {ex}")
            return

        with self.state_lock:
            if 'ueMeasData' in meas_data:
                for ue_id, ue_meas in meas_data['ueMeasData'].items():
                    per_ue_data = ue_meas.get('measData', {})
                    dl_vals = per_ue_data.get('DRB.UEThpDl', [])
                    ul_vals = per_ue_data.get('DRB.UEThpUl', [])

                    dl_val = float(dl_vals[0]) if dl_vals else None
                    ul_val = float(ul_vals[0]) if ul_vals else None

                    self.ue_throughput[ue_id] = {
                        'dl': dl_val,
                        'ul': ul_val,
                        'ts': time.time()
                    }

                    # Mark UE as traffic-activated once it shows meaningful throughput.
                    max_sample = max(float(dl_val or 0.0), float(ul_val or 0.0))
                    if max_sample >= self.activation_throughput_kbps:
                        state = self.ue_traffic_state.setdefault(ue_id, {'activated': False})
                        if not state['activated']:
                            state['activated'] = True
                            self._log(
                                f"[UE_TRAFFIC][ACTIVATED] ue_id={ue_id} throughput={max_sample:.2f} kbps"
                            )

        self._evaluate_control_policy(e2_agent_id)

    def _slice_prb_callback(self, sst, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        _ = (e2_agent_id, subscription_id)
        try:
            _ = self.e2sm_kpm.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
        except Exception as ex:
            self._log(f"[KPM_PRB][ERROR] Failed to decode slice PRB indication for SST={sst}: {ex}")
            return

        if 'measData' not in meas_data:
            return

        dl_vals = meas_data['measData'].get('RRU.PrbUsedDl', [])
        ul_vals = meas_data['measData'].get('RRU.PrbUsedUl', [])
        dl_val = float(dl_vals[0]) if dl_vals else None
        ul_val = float(ul_vals[0]) if ul_vals else None

        with self.state_lock:
            self.slice_prb[sst] = {
                'dl': dl_val,
                'ul': ul_val,
                'ts': time.time()
            }

        self._evaluate_control_policy(e2_agent_id)

    # ----------------------------- Subscriptions -----------------------------

    def _subscribe_rc_style4(self, e2_node_id):
        # Keep same approach as validated RC Style 4 validator in workspace.
        event_trigger_def = bytes([])
        action_def = bytes([])

        self.subscribe(
            e2_node_id=e2_node_id,
            ran_function_id=self.e2sm_rc.ran_func_id,
            event_trigger_def=event_trigger_def,
            action_def=action_def,
            indication_callback=self.rc_style4_indication_callback,
            e2sm_type=e2sm_types.E2SM_RC
        )
        self._log(f"[SUBSCRIBE] RC Style 4 UE-context subscription sent (RAN Function ID={self.e2sm_rc.ran_func_id})")

    def _subscribe_ue_throughput(self, e2_node_id):
        report_period = self.report_period_ms
        granul_period = self.report_period_ms

        metric_names = ['DRB.UEThpDl', 'DRB.UEThpUl']
        # Dummy condition that matches all UEs (same as existing examples).
        matching_ue_conds = [
            {'testCondInfo': {'testType': ('ul-rSRP', 'true'), 'testExpr': 'lessthan', 'testValue': ('valueInt', 1000)}}
        ]

        callback = lambda agent, sub, hdr, msg: self.ue_throughput_callback(agent, sub, hdr, msg)
        self.e2sm_kpm.subscribe_report_service_style_4(
            e2_node_id,
            report_period,
            matching_ue_conds,
            metric_names,
            granul_period,
            callback
        )
        self._log(f"[SUBSCRIBE] KPM UE throughput subscription sent (metrics={metric_names})")

    def _subscribe_slice_prb(self, e2_node_id, sst):
        report_period = self.report_period_ms
        granul_period = self.report_period_ms

        metric_names = ['RRU.PrbUsedDl', 'RRU.PrbUsedUl']
        event_trigger_def = self.e2sm_kpm.e2sm_kpm_compiler.pack_event_trigger_def(report_period)

        meas_info_list = []
        for metric_name in metric_names:
            metric_def = {
                'measType': ('measName', metric_name),
                'labelInfoList': [{
                    'measLabel': {
                        'sliceID': {
                            'sST': sst.to_bytes(1, byteorder='big'),
                            'sD': self.sd.to_bytes(3, byteorder='big')
                        }
                    }
                }]
            }
            meas_info_list.append(metric_def)

        action_def_dict = {
            'ric-Style-Type': 1,
            'actionDefinition-formats': ('actionDefinition-Format1', {
                'measInfoList': meas_info_list,
                'granulPeriod': granul_period
            })
        }
        action_def = self.e2sm_kpm.e2sm_kpm_compiler.asn1_compiler.encode(
            'E2SM-KPM-ActionDefinition',
            action_def_dict
        )

        callback = lambda agent, sub, hdr, msg: self._slice_prb_callback(sst, agent, sub, hdr, msg)
        self.subscribe(
            e2_node_id,
            self.e2sm_kpm.ran_func_id,
            event_trigger_def,
            action_def,
            callback,
            e2sm_types.E2SM_KPM
        )
        self._log(f"[SUBSCRIBE] KPM slice PRB subscription sent for SST={sst}")

    # ----------------------------- Control logic -----------------------------

    def _safe_prb_load(self, sst):
        val = self.slice_prb.get(sst, {})
        dl = val.get('dl')
        ul = val.get('ul')
        if dl is None and ul is None:
            return None
        return float(dl or 0.0) + float(ul or 0.0)

    def _active_ue_count_per_slice(self):
        counts = {sst: 0 for sst in self.target_slices}
        for _, ctx in self.ue_context.items():
            sl = ctx.get('slice', {})
            sst = sl.get('sst')
            if sst in counts:
                counts[sst] += 1
        return counts

    def _map_ue_to_slice(self, ue_id):
        """
        Prefer explicit RC context mapping. Fallback for 1-UE setup when IDs differ in encoding.
        """
        if ue_id in self.ue_context:
            return self.ue_context[ue_id].get('slice', {}).get('sst')

        # Fallback for type/encoding differences (e.g., str/int)
        ue_id_str = str(ue_id)
        for known_ue in self.ue_context.keys():
            if str(known_ue) == ue_id_str:
                return self.ue_context[known_ue].get('slice', {}).get('sst')

        # Single-UE fallback: if exactly one tracked UE exists, use its slice.
        if len(self.ue_context) == 1:
            only_ue = next(iter(self.ue_context.values()))
            return only_ue.get('slice', {}).get('sst')

        return None

    def _send_slice_prb_control(self, e2_node_id, sst, policy):
        # Always send min_prb_ratio=0 to the gNB regardless of policy['min'].
        #
        # policy['min'] is the xApp's internal quota floor: the lowest percentage
        # the xApp will ever assign as max_prb_ratio for this slice.  It is enforced
        # entirely within the xApp (via max(min_quota[sst], ema_quota[sst]) in the
        # evaluation loop) and must NOT be forwarded to the gNB as a radio-layer
        # minimum guarantee.
        #
        # When min_prb_ratio > 0 is sent to the gNB, the inter-slice scheduler
        # creates TWO candidates per slot for the affected slice:
        #   1. HIGH-priority (min guarantee): {0, min_prbs}  — one PDCCH/DCI used
        #   2. NORMAL (up to max):            {min_prbs, max_prbs}  — same UE, but
        #      a UE can only receive ONE PDCCH per slot, so the gNB's PDCCH
        #      allocator rejects the second attempt, yielding 0 more PRBs.
        # The result is that the slice UE is permanently capped at the minimum
        # guarantee even though the quota (max) is higher, because the single
        # available PDCCH slot was consumed by the first (min-guarantee) candidate.
        #
        # Sending min_prb_ratio=0 collapses this into a single candidate {0, max}
        # with normal priority, allowing the UE to be scheduled up to max_prbs in
        # one PDCCH/PDSCH grant — making full use of the xApp-controlled quota.
        #
        # slice_policy['max'] is stored as a float accumulator internally so that
        # sub-1% fractional changes can accumulate across cycles before crossing
        # the integer rounding boundary.  The E2 RC message uses valueInt, so the
        # ASN.1 encoder will truncate any non-integer (e.g. 45.75 → 45 instead of
        # the intended 46).  Explicitly round here so the gNB always receives the
        # integer value that matches what _normalized_slice_util treats as the
        # active quota, preventing a permanent offset where the xApp believes the
        # gNB is at quota N+1 but the gNB is still enforcing quota N.
        self.e2sm_rc.control_slice_level_prb_quota(
            e2_node_id=e2_node_id,
            ue_id=0,
            min_prb_ratio=0,
            max_prb_ratio=int(round(float(policy['max']))),
            dedicated_prb_ratio=policy['ded'],
            ack_request=1,
            sst=sst,
            sd=self.sd,
            plmn_string='00101'
        )

    def _build_near_equal_max_ratios(self):
        """
        Split 100% max ratio across configured slices as evenly as possible.
        Example for 3 slices -> [34, 33, 33].
        """
        num_slices = len(self.target_slices)
        if num_slices == 0:
            return {}

        base = 100 // num_slices
        rem = 100 % num_slices

        ratios = {}
        for idx, sst in enumerate(self.target_slices):
            ratios[sst] = base + (1 if idx < rem else 0)
        return ratios

    def _apply_initial_equal_split(self, e2_node_id):
        """
        Send initial RC controls to set near-equal PRB policy across slices.
        Executed once at startup before threshold-triggered adaptation.
        """
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

            self._send_slice_prb_control(e2_node_id, sst, policy)
            self._log(
                f"[RC_CONTROL_EVENT][INIT] sst={sst} min={policy['min']} max={policy['max']} ded={policy['ded']}"
            )

    def _evaluate_control_policy(self, e2_node_id):
        now = time.time()

        with self.state_lock:
            if now - self.last_control_ts < self.control_cooldown_s:
                return

            ue_counts = self._active_ue_count_per_slice()

            # Find first UE that breaches threshold and has a known slice.
            target = None
            for ue_id, thp in self.ue_throughput.items():
                ue_slice = self._map_ue_to_slice(ue_id)
                if ue_slice not in self.target_slices:
                    continue

                # Guard 1: Ignore low/zero samples briefly after UE connect.
                ue_ctx = self.ue_context.get(ue_id)
                if ue_ctx is not None:
                    connected_at = ue_ctx.get('connected_at', ue_ctx.get('last_seen', now))
                    if (now - connected_at) < self.ue_attach_grace_s:
                        continue

                # Guard 2: Ignore low/zero samples until traffic is activated at least once.
                traffic_state = self.ue_traffic_state.get(ue_id, {'activated': False})
                if not traffic_state.get('activated', False):
                    continue

                dl = thp.get('dl')
                ul = thp.get('ul')
                if dl is None and ul is None:
                    continue

                # Simple policy input: use DL when present; otherwise UL.
                effective = dl if dl is not None else ul
                if effective is None:
                    self.ue_breach_streak[ue_id] = 0
                    continue

                if effective < self.throughput_threshold:
                    current_streak = self.ue_breach_streak.get(ue_id, 0) + 1
                    self.ue_breach_streak[ue_id] = current_streak

                    if current_streak < self.min_consecutive_breaches:
                        continue

                    target = {
                        'ue_id': ue_id,
                        'slice': ue_slice,
                        'dl': dl,
                        'ul': ul,
                        'effective': effective,
                        'streak': current_streak
                    }
                    break
                else:
                    self.ue_breach_streak[ue_id] = 0

            if target is None:
                return

            target_sst = target['slice']

            # Idle donor slice criteria:
            # 1) different slice
            # 2) no UEs OR PRB load below idle threshold
            # 3) has room to decrease max PRB by prb_shift_step
            donors = []
            for sst in self.target_slices:
                if sst == target_sst:
                    continue
                load = self._safe_prb_load(sst)
                is_idle = (ue_counts.get(sst, 0) == 0) or (load is not None and load <= self.idle_prb_threshold)
                if not is_idle:
                    continue

                donor_policy = self.slice_policy[sst]
                if donor_policy['max'] - self.prb_shift_step < donor_policy['min']:
                    continue

                donors.append((sst, load if load is not None else -1.0, ue_counts.get(sst, 0)))

            if not donors:
                self._log(
                    f"[THRESHOLD_BREACH] ue_id={target['ue_id']} sst={target_sst} "
                    f"throughput={target['effective']:.2f} < {self.throughput_threshold:.2f}, but no idle donor slice found"
                )
                return

            # Prefer least active donor, then lowest PRB load.
            donors.sort(key=lambda x: (x[2], x[1]))
            donor_sst = donors[0][0]

            target_policy = self.slice_policy[target_sst]
            donor_policy = self.slice_policy[donor_sst]

            # Shift PRB ratio budget from donor.max to target.max.
            new_target_max = min(100, target_policy['max'] + self.prb_shift_step)
            if new_target_max == target_policy['max']:
                self._log(
                    f"[THRESHOLD_BREACH] ue_id={target['ue_id']} sst={target_sst} "
                    f"throughput={target['effective']:.2f} < {self.throughput_threshold:.2f}, target max already at limit"
                )
                return

            new_donor_max = donor_policy['max'] - self.prb_shift_step
            if new_donor_max < donor_policy['min']:
                self._log(
                    f"[THRESHOLD_BREACH] ue_id={target['ue_id']} sst={target_sst} "
                    f"throughput={target['effective']:.2f} < {self.throughput_threshold:.2f}, donor cannot reduce further"
                )
                return

            self._log(
                f"[THRESHOLD_BREACH] ue_id={target['ue_id']} sst={target_sst} "
                f"throughput={target['effective']:.2f} < {self.throughput_threshold:.2f} "
                f"(streak={target['streak']}/{self.min_consecutive_breaches})"
            )

            # Apply donor decrease first, then target increase.
            donor_policy['max'] = int(new_donor_max)
            target_policy['max'] = int(new_target_max)

            self._send_slice_prb_control(e2_node_id, donor_sst, donor_policy)
            self._log(
                f"[RC_CONTROL_EVENT] donor_sst={donor_sst} min={donor_policy['min']} max={donor_policy['max']} ded={donor_policy['ded']}"
            )

            self._send_slice_prb_control(e2_node_id, target_sst, target_policy)
            self._log(
                f"[RC_CONTROL_EVENT] target_sst={target_sst} min={target_policy['min']} max={target_policy['max']} ded={target_policy['ded']}"
            )

            self.last_control_ts = now
            # Reset streak after action to require a fresh consecutive-breach window.
            self.ue_breach_streak[target['ue_id']] = 0

    # ----------------------------- RMR loop override -----------------------------

    def _run(self):
        """
        Override base loop to add explicit RC control ACK/FAILURE logging.
        """
        while self.running:
            try:
                sbuf = rmr.rmr_torcv_msg(self.rmr_client, None, 100)
                summary = rmr.message_summary(sbuf)
            except Exception:
                continue

            if summary[rmr.RMR_MS_MSG_STATE] == 0:
                msg_type = summary['message type']

                if msg_type == 12050:  # RIC_INDICATION
                    e2_agent_id = str(summary['meid'].decode('utf-8'))
                    data = rmr.get_payload(sbuf)
                    try:
                        e2_event_instance_id = summary['subscription id']

                        # Be robust against type mismatches between REST mapping and RMR summary
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

                elif msg_type == 12042:  # RIC_CONTROL_FAILURE
                    self.control_failure_count += 1
                    self._log(f"[RC_CONTROL_EVENT][FAILURE] total_failure={self.control_failure_count}")

            rmr.rmr_free_msg(sbuf)

    @xAppBase.start_function
    def start(self, e2_node_id):
        self._log("=" * 90)
        self._log("test_xapp_1 - Integrated Closed-Loop Dynamic Slice Control")
        self._log("=" * 90)
        self._log(f"E2 Node ID: {e2_node_id}")
        self._log(f"KPM RAN Function ID: {self.e2sm_kpm.ran_func_id}")
        self._log(f"RC  RAN Function ID: {self.e2sm_rc.ran_func_id}")
        self._log(f"Slices: {self.target_slices} (SD={self.sd})")
        self._log(f"Report period: {self.report_period_ms} ms")
        self._log(f"Throughput threshold: {self.throughput_threshold}")
        self._log(f"Idle PRB threshold (DL+UL): {self.idle_prb_threshold}")
        self._log(f"PRB shift step: {self.prb_shift_step}")
        self._log(f"Control cooldown: {self.control_cooldown_s} s")
        self._log(f"UE attach grace: {self.ue_attach_grace_s} s")
        self._log(f"Traffic activation threshold: {self.activation_throughput_kbps} kbps")
        self._log(f"Min consecutive breaches: {self.min_consecutive_breaches}")
        self._log("=" * 90)

        self._subscribe_rc_style4(e2_node_id)
        self._subscribe_ue_throughput(e2_node_id)
        for sst in self.target_slices:
            self._subscribe_slice_prb(e2_node_id, sst)
            time.sleep(0.3)

        # Wait for the E2SM-RC subscription to be acknowledged by the gNB before sending
        # the initial RC control messages.  The style-4 RC subscription request is sent
        # first, but the gNB must complete the E2AP subscription handshake before it will
        # accept RC control messages.  Without this delay the control messages are sent
        # while the subscription is still in-flight and are silently dropped by the RIC or
        # the gNB.  3 s is conservative; typical ACK round-trip is <1 s.
        self._log('[INIT] Waiting 3 s for E2SM-RC subscription ACK before sending initial policy ...')
        time.sleep(3.0)

        # Apply startup baseline policy before threshold-triggered control loop.
        self._apply_initial_equal_split(e2_node_id)

        self._log("[INFO] All subscriptions sent. Closed-loop controller is active.")
        self._log("[INFO] Press Ctrl+C to stop.")

        # Enter the RMR receive loop. self.running must be True for _run() to process
        # messages. Without this, the process stays alive (HTTP server keeps a background
        # thread) but no RIC_INDICATION messages are ever dispatched to callbacks, so
        # KPM metrics never arrive, UE context is never populated, and the dashboard
        # state file is never updated after startup.
        self.running = True
        self._run()

    def signal_handler(self, sig, frame):
        self._log("=" * 90)
        self._log("test_xapp_1 summary")
        self._log(f"Tracked UEs: {len(self.ue_context)}")
        self._log(
            f"Indications: total={self.indication_total_count}, kpm={self.indication_kpm_count}, rc={self.indication_rc_count}, unmapped={self.unmapped_indication_count}"
        )
        self._log(
            f"RC callbacks: total={self.rc_callback_count}, empty_payloads={self.rc_empty_payload_count}"
        )
        self._log(f"Control ACK count: {self.control_ack_count}")
        self._log(f"Control FAILURE count: {self.control_failure_count}")
        self._log(f"Current slice policy: {self.slice_policy}")
        self._log("=" * 90)
        super().signal_handler(sig, frame)


def parse_slices(text):
    vals = []
    for item in text.split(','):
        item = item.strip()
        if not item:
            continue
        sst = int(item)
        if not (1 <= sst <= 255):
            raise ValueError(f"SST must be in [1,255], got {sst}")
        vals.append(sst)
    if not vals:
        raise ValueError("At least one SST must be provided")
    return vals


def main():
    parser = argparse.ArgumentParser(
        description='test_xapp_1 - Integrated RC+KPM closed-loop slice control test xApp'
    )
    parser.add_argument('--config', type=str, default='', help='xApp config path (optional)')
    parser.add_argument('--http_server_port', type=int, default=8096, help='HTTP server port')
    parser.add_argument('--rmr_port', type=int, default=4563, help='RMR port')
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
    parser.add_argument('--slices', type=str, default='1,2,3',
                        help='Comma-separated SST list to monitor/control, e.g. 1,2,3')
    parser.add_argument('--sd', type=int, default=0,
                        help='Slice SD used in KPM label + RC control (0..16777215)')
    parser.add_argument('--cell_prbs', type=int, default=162,
                        help='Total PRBs available in the cell (used for PRB saturation detection)')

    args = parser.parse_args()

    routed_default_ports = {4560, 4561, 4562, 4563}
    if args.rmr_port not in routed_default_ports:
        print(
            f"[WARNING] RMR port {args.rmr_port} may not be routable with current RIC route table. "
            f"Common configured ports: {sorted(routed_default_ports)}"
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

    target_slices = parse_slices(args.slices)

    print("=" * 90)
    print("Initializing test_xapp_1")
    print("=" * 90)
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
    print(f"HTTP port: {args.http_server_port}, RMR port: {args.rmr_port}")
    print("=" * 90)

    xapp = TestXapp1(
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
        target_slices=target_slices,
        sd=args.sd
    )

    signal.signal(signal.SIGQUIT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)
    signal.signal(signal.SIGINT, xapp.signal_handler)

    xapp.start(args.e2_node_id)


if __name__ == '__main__':
    main()
