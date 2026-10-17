111111#!/usr/bin/env python3
# test_xapp_3_dashboard_dash.py — Dash / Plotly dashboard for test_xapp_3.
#
# Asymmetric sharing-graph policy banner:
#   min_quota  = [20, 30, 10]
#   thresh_bps = [50 Mbps, 5 Mbps, 1 Mbps]
#   Emergency ← eMBB (α=0.7), Emergency ← IoT (γ=0.3), eMBB ← IoT (1.0)
#
# Run:
#   python3 test_xapp_3_dashboard_dash.py --state-file /tmp/test_xapp_3_state.json
#   or via the companion bash script:
#   ./run_test_xapp_3_docker_dual_venv_dash.sh

import argparse
import collections
import json
import os
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback_context, dcc, html

# ── Palette ──────────────────────────────────────────────────────────────────
SLICE_COLORS  = ['#3B82F6', '#10B981', '#EF4444']
SLICE_SHADES  = ['rgba(59,130,246,0.18)', 'rgba(16,185,129,0.18)', 'rgba(239,68,68,0.18)']
BG_DARK       = '#111827'
CARD_DARK     = '#1F2937'
BORDER_DARK   = '#374151'
TEXT_PRIMARY  = '#F9FAFB'
TEXT_MUTED    = '#9CA3AF'
ACCENT_GREEN  = '#10B981'
ACCENT_BLUE   = '#3B82F6'
PLOTLY_TEMPLATE = 'plotly_dark'

MAX_HISTORY   = 160   # data-points kept per series


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_state(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fp:
            return json.load(fp)
    except Exception:
        return {}


def parse_slices(text: str) -> list:
    out = []
    for tok in text.split(','):
        t = tok.strip()
        if t:
            try:
                out.append(int(t))
            except ValueError:
                pass
    return out


def normalize_target_slices(raw, fallback):
    src = raw if isinstance(raw, list) and raw else fallback
    seen, result = set(), []
    for item in src:
        try:
            if item is None or (isinstance(item, str) and not item.strip()):
                continue
            s = int(item)
        except Exception:
            continue
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def color_for_sst(sst, target_slices):
    idx = target_slices.index(sst) if sst in target_slices else 0
    return SLICE_COLORS[idx % len(SLICE_COLORS)]


def shade_for_sst(sst, target_slices):
    idx = target_slices.index(sst) if sst in target_slices else 0
    # Make the red shade more vivid
    if idx % len(SLICE_SHADES) == 2:
        return 'rgba(239,68,68,0.32)'
    return SLICE_SHADES[idx % len(SLICE_SHADES)]


# ── Plotly figure builders ────────────────────────────────────────────────────

def make_donut(parts, center_label, center_value=None, height=220):
    """parts = list of (label, value, color)"""
    labels  = [p[0] for p in parts]
    values  = [max(0.0, float(p[1])) for p in parts]
    colors  = [p[2] for p in parts]

    total = sum(values)
    if total <= 0:
        values  = [1]
        labels  = ['']
        colors  = [BORDER_DARK]

    annotation_text = center_label
    if center_value is not None:
        annotation_text = f'<b>{center_value}</b><br><span style="font-size:11px;color:{TEXT_MUTED}">{center_label}</span>'

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.6,
        marker=dict(colors=colors, line=dict(color=BG_DARK, width=2)),
        textinfo='none',
        hovertemplate='%{label}: %{value:.1f}<extra></extra>',
        sort=False,
    ))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(t=10, b=10, l=10, r=10),
        height=height,
        showlegend=False,
        annotations=[dict(
            text=annotation_text,
            x=0.5, y=0.5,
            font=dict(size=14, color=TEXT_PRIMARY),
            showarrow=False,
        )],
    )
    return fig


def make_line_chart(series_dict, title, y_label, height=300, colors=None):
    """series_dict = {name: [values...]}"""
    fig = go.Figure()
    names = list(series_dict.keys())
    # Fixed rolling window for x-axis
    x_vals = list(range(MAX_HISTORY))
    for i, name in enumerate(names):
        vals = series_dict[name]
        # Pad or trim to MAX_HISTORY (right-pad with None so plot starts at left)
        if len(vals) < MAX_HISTORY:
            padded = list(vals) + [None] * (MAX_HISTORY - len(vals))
        else:
            padded = list(vals)[-MAX_HISTORY:]
        clr = colors[i % len(colors)] if colors else SLICE_COLORS[i % len(SLICE_COLORS)]
        # Neon shade color (match line color, but transparent)
        if clr == '#3B82F6':
            fillcolor = 'rgba(59,130,246,0.18)'
        elif clr == '#10B981':
            fillcolor = 'rgba(16,185,129,0.18)'
        elif clr == '#EF4444':
            fillcolor = 'rgba(239,68,68,0.18)'
        else:
            fillcolor = clr
        fig.add_trace(go.Scatter(
            x=x_vals, y=padded,
            mode='lines',
            name=name,
            line=dict(color=clr, width=2, shape='spline', smoothing=1.3),
            hovertemplate='%{y:.2f}<extra>' + name + '</extra>',
            connectgaps=False,
            fill='tozeroy',
            fillcolor=fillcolor,
        ))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        height=height,
        margin=dict(t=20, b=30, l=45, r=10),
        title=dict(text=title, font=dict(size=13, color=TEXT_MUTED), x=0),
        xaxis=dict(title='Time (s)', showgrid=True, gridcolor=BORDER_DARK, zeroline=False, range=[0, MAX_HISTORY-1], fixedrange=True),
        yaxis=dict(title=y_label, showgrid=True, gridcolor=BORDER_DARK, zeroline=False),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                    font=dict(color=TEXT_PRIMARY)),
        hovermode='x unified',
        hoverlabel=dict(
            bgcolor=CARD_DARK,
            bordercolor=BORDER_DARK,
            font=dict(color=TEXT_PRIMARY, size=12),
        ),
    )
    return fig


def make_bar_chart(df, x_col, y_cols, colors, height=280):
    fig = go.Figure()
    for y_col, clr in zip(y_cols, colors):
        fig.add_trace(go.Bar(
            x=df[x_col], y=df[y_col],
            name=y_col,
            marker_color=clr,
            hovertemplate='%{x}<br>' + y_col + ': %{y:.2f}<extra></extra>',
        ))
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        height=height,
        barmode='group',
        margin=dict(t=10, b=40, l=50, r=20),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor=BORDER_DARK),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                    font=dict(color=TEXT_PRIMARY)),
    )
    return fig


# ── Layout helpers ─────────────────────────────────────────────────────────────

def card(children, padding='16px', margin_bottom='16px'):
    return html.Div(
        children,
        style={
            'background': CARD_DARK,
            'border': f'1px solid {BORDER_DARK}',
            'borderRadius': '12px',
            'padding': padding,
            'marginBottom': margin_bottom,
        }
    )


def section_title(text):
    return html.H3(text, style={'color': TEXT_PRIMARY, 'marginTop': '0', 'marginBottom': '12px',
                                'fontSize': '16px', 'fontWeight': '600'})


def metric_card(label, value):
    return html.Div(
        [
            html.Div(str(value), style={'fontSize': '20px', 'fontWeight': '700', 'color': TEXT_PRIMARY}),
            html.Div(label, style={'fontSize': '11px', 'color': TEXT_MUTED, 'marginTop': '2px'}),
        ],
        style={
            'background': CARD_DARK, 'border': f'1px solid {BORDER_DARK}',
            'borderRadius': '10px', 'padding': '8px 12px', 'textAlign': 'center',
            'flex': '1',
        }
    )


# ── App build ─────────────────────────────────────────────────────────────────

def build_app(args):
    app = Dash(
        __name__,
        title='test_xapp_3 Dashboard',
        update_title=None,
        suppress_callback_exceptions=True,
    )

    # Per-instance in-memory history (survives callbacks within one process)
    app._throughput_history: dict[str, collections.deque] = {}
    app._prb_util_history:   dict[str, collections.deque] = {}
    app._prb_alloc_history:  dict[str, collections.deque] = {}

    app.layout = html.Div(
        style={
            'background': BG_DARK,
            'height': '100vh',
            'overflow': 'hidden',
            'fontFamily': 'Inter, system-ui, sans-serif',
            'display': 'flex',
            'flexDirection': 'column',
            'padding': '8px 16px',
            'boxSizing': 'border-box',
            'gap': '8px',
        },
        children=[
            # caption
            html.Div(id='last-update-caption',
                     style={'color': TEXT_MUTED, 'fontSize': '11px', 'flexShrink': '0'}),

            # metrics row
            html.Div(id='metrics-row',
                     style={'display': 'flex', 'gap': '8px', 'flexShrink': '0'}),

            # Connected UEs by Slice
            html.Div(
                [
                    html.Div('Connected UEs by Slice',
                             style={'color': TEXT_PRIMARY, 'fontSize': '15px',
                                    'fontWeight': '600', 'marginBottom': '6px'}),
                    html.Div(id='ue-list-pane'),
                ],
                style={
                    'background': CARD_DARK,
                    'border': f'1px solid {BORDER_DARK}',
                    'borderRadius': '12px',
                    'padding': '8px 12px',
                    'flexShrink': '0',
                }
            ),

            # Row 3: PRB donuts (left) | Throughput (right)
            html.Div(
                style={'display': 'flex', 'gap': '8px', 'flex': '1', 'minHeight': '0'},
                children=[
                    html.Div(
                        [
                            html.Div('PRB Allocation / Utilization',
                                     style={'color': TEXT_PRIMARY, 'fontSize': '15px',
                                            'fontWeight': '600', 'marginBottom': '4px'}),
                            html.Div(id='prb-donut-pane',
                                     style={'height': 'calc(100% - 26px)', 'overflow': 'hidden'}),
                        ],
                        style={
                            'background': CARD_DARK, 'border': f'1px solid {BORDER_DARK}',
                            'borderRadius': '12px', 'padding': '8px 12px',
                            'flex': '1', 'minWidth': '0', 'overflow': 'hidden',
                        }
                    ),
                    html.Div(
                        [
                            html.Div('UE Throughput by Slice',
                                     style={'color': TEXT_PRIMARY, 'fontSize': '15px',
                                            'fontWeight': '600', 'marginBottom': '4px'}),
                            dcc.Graph(id='throughput-chart',
                                      config={'displayModeBar': False, 'responsive': True},
                                      style={'height': 'calc(100% - 26px)'}),
                        ],
                        style={
                            'background': CARD_DARK, 'border': f'1px solid {BORDER_DARK}',
                            'borderRadius': '12px', 'padding': '8px 12px',
                            'flex': '1', 'minWidth': '0', 'overflow': 'hidden',
                        }
                    ),
                ]
            ),

            # Row 4: PRB utilization (left) | PRB allocation (right)
            html.Div(
                style={'display': 'flex', 'gap': '8px', 'flex': '1', 'minHeight': '0'},
                children=[
                    html.Div(
                        [
                            html.Div('Slice PRB Utilization',
                                     style={'color': TEXT_PRIMARY, 'fontSize': '15px',
                                            'fontWeight': '600', 'marginBottom': '4px'}),
                            dcc.Graph(id='prb-util-chart',
                                      config={'displayModeBar': False, 'responsive': True},
                                      style={'height': 'calc(100% - 26px)'}),
                        ],
                        style={
                            'background': CARD_DARK, 'border': f'1px solid {BORDER_DARK}',
                            'borderRadius': '12px', 'padding': '8px 12px',
                            'flex': '1', 'minWidth': '0', 'overflow': 'hidden',
                        }
                    ),
                    html.Div(
                        [
                            html.Div('Slice PRB Allocation',
                                     style={'color': TEXT_PRIMARY, 'fontSize': '15px',
                                            'fontWeight': '600', 'marginBottom': '4px'}),
                            dcc.Graph(id='prb-alloc-chart',
                                      config={'displayModeBar': False, 'responsive': True},
                                      style={'height': 'calc(100% - 26px)'}),
                        ],
                        style={
                            'background': CARD_DARK, 'border': f'1px solid {BORDER_DARK}',
                            'borderRadius': '12px', 'padding': '8px 12px',
                            'flex': '1', 'minWidth': '0', 'overflow': 'hidden',
                        }
                    ),
                ]
            ),

            # hidden callback target (policy only)
            html.Div(id='policy-pane', style={'display': 'none'}),

            # RRC Control Events — compact scrollable strip
            html.Div(
                [
                    html.Div('RRC Control Events',
                             style={'color': TEXT_PRIMARY, 'fontSize': '13px',
                                    'fontWeight': '600', 'marginBottom': '4px'}),
                    html.Div(id='control-events-pane'),
                ],
                style={
                    'background': CARD_DARK,
                    'border': f'1px solid {BORDER_DARK}',
                    'borderRadius': '12px',
                    'padding': '6px 12px',
                    'flexShrink': '0',
                }
            ),

            dcc.Interval(id='interval', interval=int(args.refresh_seconds * 1000), n_intervals=0),
        ]
    )

    # ── Main callback ─────────────────────────────────────────────────────────
    @app.callback(
        Output('last-update-caption', 'children'),
        Output('metrics-row', 'children'),
        Output('ue-list-pane', 'children'),
        Output('prb-donut-pane', 'children'),
        Output('throughput-chart', 'figure'),
        Output('prb-util-chart', 'figure'),
        Output('prb-alloc-chart', 'figure'),
        Output('policy-pane', 'children'),
        Output('control-events-pane', 'children'),
        Input('interval', 'n_intervals'),
    )
    def refresh(_n):
        state = load_state(args.state_file)

        default_slices  = parse_slices(args.slices)
        target_slices   = normalize_target_slices(state.get('target_slices', default_slices), default_slices)
        cell_prbs       = args.cell_prbs
        colors          = [color_for_sst(s, target_slices) for s in target_slices]

        # ── caption ──────────────────────────────────────────────────────────
        updated_at = state.get('updated_at')
        if updated_at:
            try:
                caption = 'Last update: ' + datetime.fromisoformat(updated_at).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                caption = f'Last update: {updated_at}'
        else:
            caption = 'Waiting for first state update from xApp…'

        # ── metrics row ───────────────────────────────────────────────────────
        counts = state.get('counts', {})
        metrics_row = [
            metric_card('Tracked UEs',       counts.get('tracked_ues', 0)),
            metric_card('Control ACK',        counts.get('control_ack', 0)),
            metric_card('Control FAILURE',    counts.get('control_failure', 0)),
            metric_card('Indications (KPM)',  counts.get('indications_kpm', 0)),
            metric_card('Indications (RC)',   counts.get('indications_rc', 0)),
        ]

        # ── UE list pane ─────────────────────────────────────────────────────
        ues_by_slice = state.get('ues_by_slice', {})
        ue_cols = []
        for sst in target_slices:
            raw_list = ues_by_slice.get(str(sst), [])
            seen, ue_list = set(), []
            for item in raw_list:
                uid = str(item.get('ue_id', 'unknown'))
                if uid not in seen:
                    seen.add(uid)
                    ue_list.append(uid)
            shade = shade_for_sst(sst, target_slices)
            color = color_for_sst(sst, target_slices)
            items = [html.Li(uid, style={'margin': '4px 0', 'color': TEXT_PRIMARY}) for uid in ue_list] \
                    or [html.Li('No connected UEs', style={'margin': '4px 0', 'color': TEXT_MUTED})]
            ue_cols.append(html.Div(
                [
                    html.Div(f'Slice SST {sst}',
                             style={'fontWeight': '700', 'color': color, 'marginBottom': '8px'}),
                    html.Ul(items, style={'paddingLeft': '20px', 'margin': '0'}),
                ],
                style={'background': shade, 'border': f'1px solid {color}30',
                       'borderRadius': '10px', 'padding': '6px 8px', 'flex': '1'}
            ))
        ue_pane = html.Div(ue_cols, style={'display': 'flex', 'gap': '8px'})

        # ── PRB donuts ────────────────────────────────────────────────────────
        policy = state.get('slice_policy', {})
        prb    = state.get('slice_prb', {})

        # Allocation donut
        alloc_parts = [(f'SST {s}', float(policy.get(str(s), {}).get('max', 0) or 0),
                        color_for_sst(s, target_slices)) for s in target_slices]
        alloc_fig = make_donut(alloc_parts, 'PRB Allocation', height=None)

        # Utilization donuts
        util_donuts = [dcc.Graph(figure=alloc_fig,
                                 config={'displayModeBar': False, 'responsive': True},
                                 style={'flex': '1', 'minHeight': '0'})]
        for sst in target_slices:
            entry          = prb.get(str(sst), {})
            dl_prbs        = float(entry.get('dl') or 0.0)
            policy_max_pct = float(policy.get(str(sst), {}).get('max', 0) or 0)
            allocated      = policy_max_pct / 100.0 * cell_prbs if cell_prbs > 0 else 0.0
            dl_pct         = max(0.0, min(100.0, dl_prbs / allocated * 100.0)) if allocated > 0 else 0.0
            clr            = color_for_sst(sst, target_slices)
            parts = [(f'SST {sst}', dl_pct, clr), ('Remaining', max(0.0, 100.0 - dl_pct), BORDER_DARK)]
            ul_prbs = float(entry.get('ul') or 0.0)
            dl_str = f'{int(round(dl_prbs))} PRBs' if entry.get('dl') is not None else 'N/A'
            ul_str = f'{int(round(ul_prbs))} PRBs' if entry.get('ul') is not None else 'N/A'
            ufig = make_donut(parts, f'SST {sst}', f'{dl_pct:.1f}%', height=None)
            util_donuts.append(html.Div(
                [
                    dcc.Graph(figure=ufig,
                              config={'displayModeBar': False, 'responsive': True},
                              style={'flex': '1', 'minHeight': '0'}),
                    html.Div(f'DL: {dl_str}  |  UL: {ul_str}',
                             style={'textAlign': 'center', 'color': TEXT_MUTED,
                                    'fontSize': '11px', 'flexShrink': '0'})
                ],
                style={'flex': '1', 'display': 'flex', 'flexDirection': 'column', 'minHeight': '0'}
            ))

        prb_pane = html.Div(
            [
                util_donuts[0],
                html.Div(
                    [
                        html.Div('PRB Utilization',
                                 style={'color': TEXT_MUTED, 'fontSize': '12px', 'marginBottom': '4px',
                                        'fontWeight': '600', 'textAlign': 'center', 'flexShrink': '0'}),
                        html.Div(util_donuts[1:],
                                 style={'display': 'flex', 'gap': '8px', 'alignItems': 'stretch',
                                        'flex': '1', 'minHeight': '0'}),
                    ],
                    style={'flex': '3', 'display': 'flex', 'flexDirection': 'column', 'minHeight': '0'}
                ),
            ],
            style={'height': '100%', 'display': 'flex', 'gap': '8px'}
        )

        # ── Throughput time series ─────────────────────────────────────────────
        throughput_by_slice = state.get('throughput_by_slice', {})
        for sst in target_slices:
            for item in throughput_by_slice.get(str(sst), []):
                uid  = str(item.get('ue_id', 'unknown'))
                key  = f'SST {sst} | UE {uid}'
                val  = item.get('effective')
                sample = float(val) if val is not None else 0.0
                if key not in app._throughput_history:
                    app._throughput_history[key] = collections.deque(maxlen=MAX_HISTORY)
                app._throughput_history[key].append(sample)

        # prune stale keys
        active_tp = {
            f'SST {sst} | UE {str(item.get("ue_id","unknown"))}'
            for sst in target_slices
            for item in throughput_by_slice.get(str(sst), [])
        }
        for k in list(app._throughput_history.keys()):
            if k not in active_tp:
                del app._throughput_history[k]

        tp_series = {k: list(v) for k, v in app._throughput_history.items()}
        tp_colors = []
        for k in tp_series:
            for sst in target_slices:
                if f'SST {sst}' in k:
                    tp_colors.append(color_for_sst(sst, target_slices))
                    break
            else:
                tp_colors.append(SLICE_COLORS[0])

        tp_fig = make_line_chart(tp_series or {'(no data)': [0]}, '', 'Throughput (kbps)', height=None, colors=tp_colors)

        # ── PRB utilization / allocation histories ────────────────────────────
        for sst in target_slices:
            key_u = f'SST {sst}'
            dl_raw = prb.get(str(sst), {}).get('dl')
            util_v = float(dl_raw) if dl_raw is not None else 0.0
            if key_u not in app._prb_util_history:
                app._prb_util_history[key_u] = collections.deque(maxlen=MAX_HISTORY)
            app._prb_util_history[key_u].append(util_v)

            max_pct  = float(policy.get(str(sst), {}).get('max', 0) or 0)
            alloc_v  = max_pct / 100.0 * cell_prbs
            if key_u not in app._prb_alloc_history:
                app._prb_alloc_history[key_u] = collections.deque(maxlen=MAX_HISTORY)
            app._prb_alloc_history[key_u].append(alloc_v)

        util_series  = {k: list(v) for k, v in app._prb_util_history.items()}
        alloc_series = {k: list(v) for k, v in app._prb_alloc_history.items()}
        prb_colors   = [color_for_sst(sst, target_slices) for sst in target_slices]

        util_fig  = make_line_chart(util_series,  '', 'PRBs (DL)',       height=None, colors=prb_colors)
        alloc_fig2 = make_line_chart(alloc_series, '', 'PRBs Allocated',  height=None, colors=prb_colors)

        # ── Policy pane ───────────────────────────────────────────────────────
        labels       = {0: 'eMBB', 1: 'Emergency', 2: 'IoT'}
        min_quotas   = [20, 30, 10]
        thresh_mbps  = [50.0, 5.0, 1.0]
        policy_rows  = []
        for idx, sst in enumerate(target_slices):
            policy_rows.append({
                'Slice (SST)':        f'SST {sst} ({labels.get(idx, f"SST{sst}")})',
                'Min Quota (%)':      min_quotas[idx] if idx < len(min_quotas) else '—',
                'Throughput Target':  f'{thresh_mbps[idx]} Mbps' if idx < len(thresh_mbps) else '—',
            })

        policy_df = pd.DataFrame(policy_rows)

        def _tbl(df):
            header_cells = [html.Th(c, style={'padding': '8px 14px', 'textAlign': 'left',
                                               'color': TEXT_MUTED, 'fontSize': '12px',
                                               'borderBottom': f'1px solid {BORDER_DARK}'})
                            for c in df.columns]
            rows_html = []
            for _, row in df.iterrows():
                rows_html.append(html.Tr([
                    html.Td(str(v), style={'padding': '7px 14px', 'color': TEXT_PRIMARY, 'fontSize': '13px'})
                    for v in row
                ]))
            return html.Table(
                [html.Thead(html.Tr(header_cells)), html.Tbody(rows_html)],
                style={'width': '100%', 'borderCollapse': 'collapse',
                       'background': 'transparent', 'marginBottom': '14px'}
            )

        graph_rows = []
        if len(target_slices) >= 3:
            embb, emrg, iot = target_slices[0], target_slices[1], target_slices[2]
            graph_rows = [
                {'Receiver': f'Emergency (SST {emrg})', 'Donor': f'eMBB (SST {embb})', 'Weight': 'α = 0.70'},
                {'Receiver': f'Emergency (SST {emrg})', 'Donor': f'IoT (SST {iot})',   'Weight': 'γ = 0.30'},
                {'Receiver': f'eMBB (SST {embb})',      'Donor': f'IoT (SST {iot})',   'Weight': '1.00'},
            ]

        policy_pane = html.Div([
            _tbl(policy_df),
            html.Div('Sharing graph (directed edges — donor must have util < 0.5)',
                     style={'color': TEXT_MUTED, 'fontSize': '12px', 'marginBottom': '8px', 'fontWeight': '600'})
            if graph_rows else html.Span(),
            _tbl(pd.DataFrame(graph_rows)) if graph_rows else html.Div(
                'Sharing graph requires ≥ 3 slices.',
                style={'color': TEXT_MUTED, 'fontSize': '13px'}
            ),
        ])

        # ── Control events pane ───────────────────────────────────────────────
        events = state.get('control_events', [])
        if not events:
            ctrl_pane = html.Pre('No control messages yet.',
                                 style={'color': TEXT_MUTED, 'fontSize': '12px', 'background': BG_DARK,
                                        'padding': '6px 10px', 'borderRadius': '8px', 'margin': '0',
                                        'maxHeight': '55px', 'overflowY': 'auto'})
        else:
            lines = []
            for ev in events[-30:]:
                ts       = ev.get('ts', 'n/a')
                msg_type = ev.get('message_type', 'UNKNOWN')
                sst      = ev.get('sst')
                reason   = ev.get('reason', '')
                details  = ev.get('details', '')
                pol      = ev.get('policy') or {}
                scope    = f' sst={sst}' if sst is not None else ''
                pol_str  = (f" min={pol.get('min','n/a')} max={pol.get('max','n/a')} ded={pol.get('ded','n/a')}"
                            if pol else '')
                det_str  = f' details={details}' if details else ''
                lines.append(f'[{ts}] {msg_type}{scope}{pol_str} reason={reason}{det_str}')
            ctrl_pane = html.Pre('\n'.join(lines),
                                 style={'color': ACCENT_GREEN, 'fontSize': '12px', 'background': BG_DARK,
                                        'padding': '6px 10px', 'borderRadius': '8px', 'margin': '0',
                                        'maxHeight': '55px', 'overflowY': 'auto', 'fontFamily': 'monospace'})

        return (caption, metrics_row, ue_pane, prb_pane,
                tp_fig, util_fig, alloc_fig2, policy_pane, ctrl_pane)

    return app


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description='test_xapp_3 Dash/Plotly dashboard')
    parser.add_argument('--state-file',        type=str,   default='/tmp/test_xapp_3_state.json')
    parser.add_argument('--refresh-seconds',   type=float, default=0.5)
    parser.add_argument('--slices',            type=str,   default='1,2,3')
    parser.add_argument('--cell-prbs',         type=int,   default=162,
                        help='Total available PRBs in the cell')
    parser.add_argument('--port',              type=int,   default=8504)
    parser.add_argument('--host',              type=str,   default='0.0.0.0')
    parser.add_argument('--debug',             action='store_true')
    return parser.parse_args()


def main():
    args = parse_args()
    app  = build_app(args)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
