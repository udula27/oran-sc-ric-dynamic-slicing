#!/usr/bin/env python3

import argparse
import signal
from datetime import datetime

from lib.xAppBase import xAppBase


REQUESTED_METRICS = [
    'DRB.AirIfDelayUl',
    'DRB.RlcPacketDropRateDl',
    'DRB.RlcDelayUl'
]


class LatencyTestXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port):
        super(LatencyTestXapp, self).__init__(config, http_server_port, rmr_port)

    @staticmethod
    def _fmt_value(value):
        if value is None:
            return 'N/A'
        if isinstance(value, float):
            return f'{value:.3f}'
        return str(value)

    @staticmethod
    def _first_or_none(values):
        if not values:
            return None
        return values[0]

    def _print_ue_metrics(self, ue_metrics):
        if not ue_metrics:
            print('[INFO] No UE-level metrics present in this indication.')
            return

        print('UE Metrics:')
        for ue_id in sorted(ue_metrics.keys(), key=lambda x: str(x)):
            per_ue = ue_metrics[ue_id].get('measData', {})

            air_if_delay_ul = self._first_or_none(per_ue.get('DRB.AirIfDelayUl', []))
            rlc_drop_rate_dl = self._first_or_none(per_ue.get('DRB.RlcPacketDropRateDl', []))
            rlc_delay_ul = self._first_or_none(per_ue.get('DRB.RlcDelayUl', []))

            rlc_drop_rate_pct = None if rlc_drop_rate_dl is None else (float(rlc_drop_rate_dl) / 100.0)

            print(
                f'  UE={ue_id} | '
                f'DRB.AirIfDelayUl={self._fmt_value(air_if_delay_ul)} (0.1ms) | '
                f'DRB.RlcPacketDropRateDl={self._fmt_value(rlc_drop_rate_dl)} ({self._fmt_value(rlc_drop_rate_pct)}%) | '
                f'DRB.RlcDelayUl={self._fmt_value(rlc_delay_ul)} (0.1ms)'
            )

    def _print_non_ue_metrics(self, meas_data):
        flat_metrics = meas_data.get('measData', {})
        if not flat_metrics:
            return

        air_if_delay_ul = self._first_or_none(flat_metrics.get('DRB.AirIfDelayUl', []))
        rlc_drop_rate_dl = self._first_or_none(flat_metrics.get('DRB.RlcPacketDropRateDl', []))
        rlc_delay_ul = self._first_or_none(flat_metrics.get('DRB.RlcDelayUl', []))
        rlc_drop_rate_pct = None if rlc_drop_rate_dl is None else (float(rlc_drop_rate_dl) / 100.0)

        print('Aggregate Metrics:')
        print(
            f'  DRB.AirIfDelayUl={self._fmt_value(air_if_delay_ul)} (0.1ms) | '
            f'DRB.RlcPacketDropRateDl={self._fmt_value(rlc_drop_rate_dl)} ({self._fmt_value(rlc_drop_rate_pct)}%) | '
            f'DRB.RlcDelayUl={self._fmt_value(rlc_delay_ul)} (0.1ms)'
        )

    def kpm_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print('\n' + '=' * 100)
        print(f'[{now}] RIC Indication from {e2_agent_id} (subscription_id={subscription_id})')

        try:
            hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
        except Exception as ex:
            print(f'[ERROR] Failed to decode KPM indication: {ex}')
            return

        print(f"Collection Start Time: {hdr.get('colletStartTime', 'N/A')}")
        if 'granulPeriod' in meas_data:
            print(f"Granularity Period: {meas_data['granulPeriod']} ms")

        ue_meas_data = meas_data.get('ueMeasData', {})
        self._print_ue_metrics(ue_meas_data)
        self._print_non_ue_metrics(meas_data)

    @xAppBase.start_function
    def start(self, e2_node_id, report_period_ms, granul_period_ms, report_style, ue_ids):
        print('=' * 100)
        print('latency_test_xapp - KPM UE Latency/Drop-Rate Monitor')
        print('=' * 100)
        print(f'E2 Node ID: {e2_node_id}')
        print(f'KPM RAN Function ID: {self.e2sm_kpm.ran_func_id}')
        print(f'Report Style: {report_style}')
        print(f'Report Period: {report_period_ms} ms')
        print(f'Granularity Period: {granul_period_ms} ms')
        print(f'Metrics: {REQUESTED_METRICS}')

        if report_style == 4:
            matching_ue_conds = [
                {
                    'testCondInfo': {
                        'testType': ('ul-rSRP', 'true'),
                        'testExpr': 'lessthan',
                        'testValue': ('valueInt', 1000)
                    }
                }
            ]
            self.e2sm_kpm.subscribe_report_service_style_4(
                e2_node_id,
                report_period_ms,
                matching_ue_conds,
                REQUESTED_METRICS,
                granul_period_ms,
                self.kpm_callback
            )
            print('[SUBSCRIBE] Sent E2SM-KPM report style 4 subscription for all matching UEs.')

        elif report_style == 5:
            if len(ue_ids) < 2:
                raise ValueError('Report style 5 requires at least 2 UE IDs (provide --ue_ids)')
            self.e2sm_kpm.subscribe_report_service_style_5(
                e2_node_id,
                report_period_ms,
                ue_ids,
                REQUESTED_METRICS,
                granul_period_ms,
                self.kpm_callback
            )
            print(f'[SUBSCRIBE] Sent E2SM-KPM report style 5 subscription for UE IDs: {ue_ids}')

        else:
            raise ValueError('Only report styles 4 and 5 are supported by this xApp')

        print('[INFO] Waiting for UE metric indications... Press Ctrl+C to stop.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='latency_test_xapp - monitor UE DRB latency/drop metrics via E2SM-KPM')
    parser.add_argument('--config', type=str, default='', help='xApp config file path')
    parser.add_argument('--http_server_port', type=int, default=8098, help='HTTP server listen port')
    parser.add_argument('--rmr_port', type=int, default=4564, help='RMR port')
    parser.add_argument('--e2_node_id', type=str, default='gnbd_001_001_00019b_0', help='E2 Node ID')
    parser.add_argument('--ran_func_id', type=int, default=2, help='KPM RAN Function ID')
    parser.add_argument('--report_period_ms', type=int, default=1000, help='KPM report period (ms)')
    parser.add_argument('--granul_period_ms', type=int, default=1000, help='KPM granularity period (ms)')
    parser.add_argument('--report_style', type=int, default=4, choices=[4, 5], help='KPM report style to use (4 or 5)')
    parser.add_argument('--ue_ids', type=str, default='0,1', help='Comma-separated UE IDs for report style 5')

    args = parser.parse_args()

    ue_ids = [int(x.strip()) for x in args.ue_ids.split(',') if x.strip()]

    xapp = LatencyTestXapp(args.config, args.http_server_port, args.rmr_port)
    xapp.e2sm_kpm.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)
    signal.signal(signal.SIGINT, xapp.signal_handler)

    xapp.start(
        e2_node_id=args.e2_node_id,
        report_period_ms=args.report_period_ms,
        granul_period_ms=args.granul_period_ms,
        report_style=args.report_style,
        ue_ids=ue_ids
    )
