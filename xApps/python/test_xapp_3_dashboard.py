#!/usr/bin/env python3
# test_xapp_3_dashboard.py — Streamlit dashboard for test_xapp_3.
#
# Asymmetric sharing-graph policy banner:
#   min_quota  = [20, 30, 10]
#   thresh_bps = [50 Mbps, 5 Mbps, 1 Mbps]
#   Emergency ← eMBB (α=0.7), Emergency ← IoT (γ=0.3), eMBB ← IoT (1.0)

import argparse
import json
import os
import time
from datetime import datetime

import pandas as pd
import streamlit as st


SLICE_COLORS = ['#3B82F6', '#10B981', '#EF4444']
SLICE_SHADES = ['#BFDBFE', '#A7F3D0', '#FECACA']

# xapp3 sharing graph edges rendered in the UI
SHARING_GRAPH_ROWS = [
    ('Emergency (SST[1])', 'eMBB (SST[0])',     'α = 0.70'),
    ('Emergency (SST[1])', 'IoT (SST[2])',      'γ = 0.30'),
    ('eMBB (SST[0])',      'IoT (SST[2])',      '1.00'),
]


def parse_slices(text):
    vals = []
    for item in text.split(','):
        token = item.strip()
        if not token:
            continue
        vals.append(int(token))
    return vals


def normalize_target_slices(raw_slices, fallback_slices):
    source = raw_slices if isinstance(raw_slices, list) and raw_slices else fallback_slices
    normalized = []
    seen = set()
    for item in source:
        try:
            sst = int(item)
        except Exception:
            continue
        if sst in seen:
            continue
        seen.add(sst)
        normalized.append(sst)
    return normalized


def load_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fp:
            return json.load(fp)
    except Exception:
        return {}


def fmt_num(val):
    if val is None:
        return 'N/A'
    return f'{float(val):.2f}'


def color_map_for_slices(target_slices):
    return {
        sst: {
            'color': SLICE_COLORS[idx % len(SLICE_COLORS)],
            'shade': SLICE_SHADES[idx % len(SLICE_SHADES)]
        }
        for idx, sst in enumerate(target_slices)
    }


def get_theme_colors():
    theme_base = st.get_option('theme.base') or 'light'
    if theme_base.lower() == 'dark':
        return {
            'center_bg': '#1F2937',
            'center_label': '#D1D5DB',
            'center_value': '#F9FAFB',
            'empty_ring': '#6B7280'
        }
    return {
        'center_bg': '#FFFFFF',
        'center_label': '#6B7280',
        'center_value': '#111827',
        'empty_ring': '#D1D5DB'
    }


def _conic_segments(parts, empty_ring_color):
    total = sum(max(0.0, float(v)) for _, v, _ in parts)
    if total <= 0.0:
        return f'{empty_ring_color} 0deg 360deg'

    start = 0.0
    segments = []
    for _, value, color in parts:
        val = max(0.0, float(value))
        if val <= 0.0:
            continue
        deg = (val / total) * 360.0
        end = start + deg
        segments.append(f'{color} {start:.2f}deg {end:.2f}deg')
        start = end

    if start < 360.0:
        segments.append(f'{empty_ring_color} {start:.2f}deg 360deg')

    return ', '.join(segments)


def render_donut(parts, center_label, center_value=None, size=210, theme_colors=None):
    theme_colors = theme_colors or get_theme_colors()
    gradient = _conic_segments(parts, theme_colors['empty_ring'])
    inner_size = int(size * 0.58)

    center_html = f"<div style='font-size:12px;color:{theme_colors['center_label']};'>{center_label}</div>"
    if center_value is not None:
        center_html += f"<div style='font-size:18px;font-weight:700;color:{theme_colors['center_value']};'>{center_value}</div>"

    donut_html = f"""
    <div style='display:flex;justify-content:center;align-items:center;width:100%;'>
      <div style='position:relative;width:{size}px;height:{size}px;border-radius:50%;background:conic-gradient({gradient});'>
        <div style='position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:{inner_size}px;height:{inner_size}px;border-radius:50%;background:{theme_colors['center_bg']};display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;'>
          {center_html}
        </div>
      </div>
    </div>
    """
    st.markdown(donut_html, unsafe_allow_html=True)


def render_policy_info_pane(target_slices):
    """Display xapp3 policy constants and sharing graph."""
    st.subheader('xApp 3 Policy — Asymmetric Sharing Graph')

    slices = list(target_slices)
    labels = {0: 'eMBB', 1: 'Emergency', 2: 'IoT'}
    min_quotas = [20, 30, 10]
    thresh_mbps = [50.0, 5.0, 1.0]

    rows = []
    for idx, sst in enumerate(slices):
        label = labels.get(idx, f'SST{sst}')
        mq = min_quotas[idx] if idx < len(min_quotas) else '—'
        thr = thresh_mbps[idx] if idx < len(thresh_mbps) else '—'
        rows.append({'Slice (SST)': f'SST {sst} ({label})', 'Min Quota (%)': mq, 'Throughput Target': f'{thr} Mbps'})

    st.table(pd.DataFrame(rows))

    if len(slices) >= 3:
        embb = slices[0]
        emrg = slices[1]
        iot = slices[2]
        graph_rows = [
            {'Receiver': f'Emergency (SST {emrg})', 'Donor': f'eMBB (SST {embb})', 'Weight': 'α = 0.70'},
            {'Receiver': f'Emergency (SST {emrg})', 'Donor': f'IoT (SST {iot})',   'Weight': 'γ = 0.30'},
            {'Receiver': f'eMBB (SST {embb})',      'Donor': f'IoT (SST {iot})',   'Weight': '1.00'},
        ]
        st.markdown('**Sharing graph** (directed edges, donor must have util < 0.5)')
        st.table(pd.DataFrame(graph_rows))
    else:
        st.info('Sharing graph requires ≥ 3 slices.')


def render_prb_pane(state, target_slices, slice_colors, theme_colors, cell_prbs=162):
    st.subheader('PRB Allocation / Utilization')
    left, right = st.columns(2)

    with left:
        st.markdown('**PRB Allocation (RC Control Policy max ratio)**')
        policy = state.get('slice_policy', {})
        alloc_parts = []
        for sst in target_slices:
            entry = policy.get(str(sst), {})
            max_ratio = int(entry.get('max', 0))
            alloc_parts.append((f'SST {sst}', max_ratio, slice_colors[sst]['color']))

        render_donut(alloc_parts, center_label='PRB Allocation', theme_colors=theme_colors)
        legend_html = ''.join(
            f"<div style='display:flex;align-items:center;gap:8px;margin:4px 0;'>"
            f"<span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:{slice_colors[sst]['color']};'></span>"
            f"<span>SST {sst}: {int(state.get('slice_policy', {}).get(str(sst), {}).get('max', 0))}%</span>"
            f"</div>"
            for sst in target_slices
        )
        st.markdown(legend_html, unsafe_allow_html=True)

    with right:
        st.markdown('**PRB Utilization (KPM Style 1, DL + UL)**')
        prb = state.get('slice_prb', {})
        policy = state.get('slice_policy', {})
        util_cols = st.columns(max(len(target_slices), 1))
        for idx, sst in enumerate(target_slices):
            with util_cols[idx]:
                entry = prb.get(str(sst), {})
                dl = entry.get('dl')
                ul = entry.get('ul')
                dl_prbs = float(dl or 0.0)
                ul_prbs = float(ul or 0.0)
                policy_max_pct = float(policy.get(str(sst), {}).get('max', 0) or 0)
                allocated_prbs = policy_max_pct / 100.0 * cell_prbs if cell_prbs > 0 else 0.0
                if allocated_prbs > 0.0:
                    dl_pct = max(0.0, min(100.0, dl_prbs / allocated_prbs * 100.0))
                else:
                    dl_pct = 0.0
                parts = [
                    (f'SST {sst}', dl_pct, slice_colors[sst]['color']),
                    ('Remaining', max(0.0, 100.0 - dl_pct), theme_colors['empty_ring'])
                ]
                render_donut(parts, center_label=f'SST {sst}', center_value=f'{dl_pct:.1f}%', theme_colors=theme_colors)
                dl_str = f'{int(round(dl_prbs))} PRBs' if dl is not None else 'N/A'
                ul_str = f'{int(round(ul_prbs))} PRBs' if ul is not None else 'N/A'
                st.caption(f'DL: {dl_str}  |  UL: {ul_str}')


def render_ue_list_pane(state, target_slices, slice_colors):
    st.subheader('Connected UEs by Slice (RC Report Style 4)')
    cols = st.columns(max(len(target_slices), 1))
    ues_by_slice = state.get('ues_by_slice', {})

    if not target_slices:
        cols[0].write('No slices configured')
        return

    for idx, sst in enumerate(target_slices):
        with cols[idx]:
            raw_ue_list = ues_by_slice.get(str(sst), [])
            ue_list = []
            seen_ue_ids = set()
            for item in raw_ue_list:
                ue_id = str(item.get('ue_id', 'unknown'))
                if ue_id in seen_ue_ids:
                    continue
                seen_ue_ids.add(ue_id)
                ue_list.append(item)

            rows = ''.join(
                f"<li style='margin:4px 0;'>UE {item.get('ue_id', 'unknown')}</li>"
                for item in ue_list
            )
            if not rows:
                rows = "<li style='margin:4px 0;'>No connected UEs</li>"

            box_html = f"""
            <div style='background:{slice_colors[sst]['shade']};padding:12px;border-radius:10px;min-height:180px;'>
              <div style='font-weight:700;margin-bottom:8px;'>Slice SST {sst}</div>
              <ul style='padding-left:20px;margin:0;'>
                {rows}
              </ul>
            </div>
            """
            st.markdown(box_html, unsafe_allow_html=True)


def render_throughput_pane(state, target_slices, slice_colors):
    st.subheader('UE Throughput by Slice (KPM Report Style 4)')
    throughput_by_slice = state.get('throughput_by_slice', {})

    history_root = st.session_state.setdefault('throughput_history', {})
    global_history = history_root.setdefault('global', {})

    has_live_samples = False
    active_series = set()
    for sst in target_slices:
        ue_list = throughput_by_slice.get(str(sst), [])
        if ue_list:
            has_live_samples = True
        for item in ue_list:
            ue_id = str(item.get('ue_id', 'unknown'))
            series_name = f'SST {sst} | UE {ue_id}'
            active_series.add(series_name)
            val = item.get('effective')
            sample = float(val) if val is not None else 0.0
            series = global_history.setdefault(series_name, [])
            series.append(sample)
            if len(series) > 80:
                del series[:-80]

    for series_name in list(global_history.keys()):
        if series_name not in active_series:
            global_history.pop(series_name, None)

    if global_history:
        max_len = max(len(vals) for vals in global_history.values())
        chart_data = {}
        for series_name, vals in global_history.items():
            padding = [None] * (max_len - len(vals))
            chart_data[series_name] = padding + vals
        st.line_chart(pd.DataFrame(chart_data), width='stretch')
    else:
        st.line_chart(pd.DataFrame({'throughput_kbps': []}), width='stretch')

    if not has_live_samples:
        st.caption('No UE throughput samples')


def render_prb_timeseries_pane(state, target_slices, slice_colors, cell_prbs):
    st.subheader('Slice PRB Utilization & Allocation Over Time')

    prb_state = state.get('slice_prb', {})
    policy_state = state.get('slice_policy', {})

    prb_hist = st.session_state.setdefault('prb_timeseries_history', {'utilized': {}, 'allocated': {}})

    for sst in target_slices:
        series_name = f'SST {sst}'

        dl_raw = prb_state.get(str(sst), {}).get('dl')
        util_val = float(dl_raw) if dl_raw is not None else 0.0
        util_series = prb_hist['utilized'].setdefault(series_name, [])
        util_series.append(util_val)
        if len(util_series) > 160:
            del util_series[:-160]

        max_pct = float(policy_state.get(str(sst), {}).get('max', 0) or 0)
        alloc_val = max_pct / 100.0 * cell_prbs
        alloc_series = prb_hist['allocated'].setdefault(series_name, [])
        alloc_series.append(alloc_val)
        if len(alloc_series) > 160:
            del alloc_series[:-160]

    colors = [slice_colors[sst]['color'] for sst in target_slices]
    col_names = [f'SST {sst}' for sst in target_slices]

    st.markdown('**PRBs Utilized per Slice (DL, KPM RRU.PrbUsedDl)**')
    util_data = prb_hist['utilized']
    if util_data:
        max_len = max(len(v) for v in util_data.values())
        chart_rows = {}
        for name in col_names:
            vals = util_data.get(name, [])
            chart_rows[name] = [None] * (max_len - len(vals)) + vals
        st.line_chart(pd.DataFrame(chart_rows), color=colors)
    else:
        st.line_chart(pd.DataFrame({'PRBs': []}))

    st.markdown('**PRBs Allocated per Slice (RC Policy max% × cell PRBs)**')
    alloc_data = prb_hist['allocated']
    if alloc_data:
        max_len = max(len(v) for v in alloc_data.values())
        chart_rows = {}
        for name in col_names:
            vals = alloc_data.get(name, [])
            chart_rows[name] = [None] * (max_len - len(vals)) + vals
        st.line_chart(pd.DataFrame(chart_rows), color=colors)
    else:
        st.line_chart(pd.DataFrame({'PRBs': []}))


def render_control_events_pane(state):
    st.subheader('RRC Control Messages')
    events = state.get('control_events', [])

    if not events:
        st.code('No control messages yet.', language='text')
        return

    lines = []
    for event in events[-30:]:
        ts = event.get('ts', 'n/a')
        msg_type = event.get('message_type', 'UNKNOWN')
        sst = event.get('sst')
        reason = event.get('reason', '')
        details = event.get('details', '')
        policy = event.get('policy') or {}

        scope = f' sst={sst}' if sst is not None else ''

        policy_str = ''
        if policy:
            policy_str = (
                f" min={policy.get('min', 'n/a')}"
                f" max={policy.get('max', 'n/a')}"
                f" ded={policy.get('ded', 'n/a')}"
            )

        detail_str = f" details={details}" if details else ''
        lines.append(f"[{ts}] {msg_type}{scope}{policy_str} reason={reason}{detail_str}")

    st.code('\n'.join(lines), language='text')


def parse_args():
    """Display iperf3 slice performance results from slice-performance-test.sh."""
    st.subheader('iperf3 Slice Performance Results')

    results = load_iperf_results()
    if not results or not results.get('slices'):
        st.info(
            'No iperf3 results yet.  Run the performance test from a UE:\n\n'
            '```\n./slice-performance-test.sh --ue\n```'
        )
        return

    test_time = results.get('test_time', 'N/A')
    duration  = results.get('test_duration_s', 'N/A')
    try:
        ts_text = datetime.fromisoformat(test_time).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        ts_text = str(test_time)

    st.caption(f'Last test: {ts_text}  |  Duration per phase: {duration} s  |  '
               f'Results file: {IPERF_RESULTS_FILE}')

    slice_data = results.get('slices', {})
    label_map  = {0: 'eMBB', 1: 'URLLC/Emergency', 2: 'IoT/mMTC'}

    # ── Summary table ────────────────────────────────────────────────────────
    rows = []
    for idx, sst in enumerate(target_slices):
        sst_str = str(sst)
        d       = slice_data.get(sst_str, {})
        if not d:
            continue
        name       = d.get('name', label_map.get(idx, f'SST{sst}'))
        target     = d.get('target_mbps', 0)
        tcp_dl     = d.get('tcp_dl_mbps', 0)
        tcp_ul     = d.get('tcp_ul_mbps', 0)
        udp_dl     = d.get('udp_dl_mbps', 0)
        jitter     = d.get('udp_jitter_ms', 'N/A')
        loss       = d.get('udp_loss_pct', 'N/A')
        q_before   = d.get('prb_quota_pct_before', '—')
        q_after    = d.get('prb_quota_pct_after',  '—')
        status     = d.get('status', 'N/A')

        jitter_str = f"{float(jitter):.3f} ms" if str(jitter) not in ('N/A', '') else 'N/A'
        loss_str   = f"{float(loss):.1f} %"    if str(loss)   not in ('N/A', '') else 'N/A'
        quota_str  = f"{q_before}% → {q_after}%"
        pct_target = f"{float(tcp_dl) / float(target) * 100.0:.1f} %" if float(target) > 0 else '—'

        rows.append({
            'Slice':         f'SST {sst}  {name}',
            'Target (Mbps)': float(target),
            'TCP DL (Mbps)': float(tcp_dl),
            'TCP UL (Mbps)': float(tcp_ul),
            'UDP DL (Mbps)': float(udp_dl),
            'Jitter':        jitter_str,
            'Pkt Loss':      loss_str,
            'PRB Quota':     quota_str,
            '% of Target':   pct_target,
            'Status':        status,
        })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ── TCP DL bar chart vs target ────────────────────────────────────────────
    chart_rows = []
    for idx, sst in enumerate(target_slices):
        sst_str = str(sst)
        d       = slice_data.get(sst_str, {})
        if not d:
            continue
        name   = d.get('name', label_map.get(idx, f'SST{sst}'))
        tcp_dl = float(d.get('tcp_dl_mbps', 0) or 0)
        target = float(d.get('target_mbps', 0) or 0)
        chart_rows.append({'Slice': f'SST{sst} {name}', 'TCP DL (Mbps)': tcp_dl, 'Target (Mbps)': target})

    if chart_rows:
        st.markdown('**TCP Downlink throughput vs per-slice target**')
        chart_df = pd.DataFrame(chart_rows).set_index('Slice')
        st.bar_chart(chart_df)

    # ── Per-slice detail cards ────────────────────────────────────────────────
    cols = st.columns(max(len(target_slices), 1))
    for idx, sst in enumerate(target_slices):
        sst_str = str(sst)
        d       = slice_data.get(sst_str, {})
        if not d:
            continue
        shade  = slice_colors[sst]['shade']
        color  = slice_colors[sst]['color']
        name   = d.get('name', label_map.get(idx, f'SST{sst}'))
        tcp_dl = d.get('tcp_dl_mbps', 0)
        tcp_ul = d.get('tcp_ul_mbps', 0)
        udp_dl = d.get('udp_dl_mbps', 0)
        target = d.get('target_mbps', 0)
        status = d.get('status', 'N/A')
        q_chg  = f"{d.get('prb_quota_pct_before','?')}% → {d.get('prb_quota_pct_after','?')}%"
        status_icon = '✅' if status == 'PASS' else ('⚠️' if status == 'MARGINAL' else '❌')

        card_html = (
            f"<div style='background:{shade};padding:14px;border-radius:10px;'>"
            f"<div style='font-weight:700;font-size:15px;'>{status_icon} SST {sst}  {name}</div>"
            f"<hr style='margin:6px 0;border:0;border-top:1px solid {color};'/>"
            f"<table style='width:100%;font-size:13px;'>"
            f"<tr><td>Target</td><td style='text-align:right'><b>{target} Mbps</b></td></tr>"
            f"<tr><td>TCP DL</td><td style='text-align:right'><b>{tcp_dl} Mbps</b></td></tr>"
            f"<tr><td>TCP UL</td><td style='text-align:right'><b>{tcp_ul} Mbps</b></td></tr>"
            f"<tr><td>UDP DL</td><td style='text-align:right'><b>{udp_dl} Mbps</b></td></tr>"
            f"<tr><td>PRB Quota</td><td style='text-align:right'><b>{q_chg}</b></td></tr>"
            f"<tr><td>Status</td><td style='text-align:right'><b>{status}</b></td></tr>"
            f"</table></div>"
        )
def parse_args():
    parser = argparse.ArgumentParser(description='test_xapp_3 Streamlit dashboard')
    parser.add_argument('--state-file', type=str, default='/tmp/test_xapp_3_state.json')
    parser.add_argument('--refresh-seconds', type=float, default=0.5)
    parser.add_argument('--slices', type=str, default='1,2,3')
    parser.add_argument('--cell-prbs', type=int, default=162,
                        help='Total available PRBs in the cell')
    return parser.parse_args()


def main():
    args = parse_args()

    refresh_seconds = max(0.2, float(args.refresh_seconds))

    st.set_page_config(page_title='test_xapp_3 dashboard', layout='wide')
    st.title('test_xapp_3 — Asymmetric Sharing-Graph Slice Controller Dashboard')

    state = load_state(args.state_file)

    default_slices = parse_slices(args.slices)
    target_slices = normalize_target_slices(state.get('target_slices', default_slices), default_slices)
    slice_colors = color_map_for_slices(target_slices)
    theme_colors = get_theme_colors()

    updated_at = state.get('updated_at')
    if updated_at:
        try:
            ts_text = datetime.fromisoformat(updated_at).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            ts_text = str(updated_at)
        st.caption(f'Last update: {ts_text}')
    else:
        st.caption('Waiting for first state update from xApp...')

    counts = state.get('counts', {})
    m1, m2, m3 = st.columns(3)
    m1.metric('Tracked UEs', counts.get('tracked_ues', 0))
    m2.metric('Control ACK', counts.get('control_ack', 0))
    m3.metric('Control FAILURE', counts.get('control_failure', 0))

    render_ue_list_pane(state, target_slices, slice_colors)
    st.divider()
    render_prb_pane(state, target_slices, slice_colors, theme_colors, args.cell_prbs)
    st.divider()
    render_throughput_pane(state, target_slices, slice_colors)
    st.divider()
    render_prb_timeseries_pane(state, target_slices, slice_colors, args.cell_prbs)
    st.divider()
    render_policy_info_pane(target_slices)
    st.divider()
    render_control_events_pane(state)

    time.sleep(refresh_seconds)
    st.rerun()


if __name__ == '__main__':
    main()
