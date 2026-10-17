#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XAPP_FILE="${SCRIPT_DIR}/latency_test_xapp.py"

CONTAINER="${CONTAINER:-python_xapp_runner}"
E2_NODE_ID="${E2_NODE_ID:-gnbd_001_001_00019b_0}"
RAN_FUNC_ID="${RAN_FUNC_ID:-2}"
REPORT_STYLE="${REPORT_STYLE:-4}"
UE_IDS="${UE_IDS:-0,1}"
REPORT_PERIOD_MS="${REPORT_PERIOD_MS:-1000}"
GRANUL_PERIOD_MS="${GRANUL_PERIOD_MS:-1000}"
HTTP_PORT="${HTTP_PORT:-8098}"
RMR_PORT="${RMR_PORT:-4564}"

print_info() {
  echo "[INFO] $1"
}

print_error() {
  echo "[ERROR] $1"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Runs latency_test_xapp.py inside a running xApp container.

Options:
  --container <name>         Container name (default: ${CONTAINER})
  --e2-node-id <id>          E2 Node ID (default: ${E2_NODE_ID})
  --ran-func-id <id>         KPM RAN function ID (default: ${RAN_FUNC_ID})
  --report-style <4|5>       KPM report style (default: ${REPORT_STYLE})
  --ue-ids <id1,id2,...>     UE IDs for style 5 (default: ${UE_IDS})
  --report-period-ms <ms>    Report period in ms (default: ${REPORT_PERIOD_MS})
  --granul-period-ms <ms>    Granularity period in ms (default: ${GRANUL_PERIOD_MS})
  --http-port <port>         xApp HTTP port (default: ${HTTP_PORT})
  --rmr-port <port>          xApp RMR port (default: ${RMR_PORT})
  -h, --help                 Show this help

Environment variable overrides are also supported:
  CONTAINER, E2_NODE_ID, RAN_FUNC_ID, REPORT_STYLE, UE_IDS,
  REPORT_PERIOD_MS, GRANUL_PERIOD_MS, HTTP_PORT, RMR_PORT

Example:
  ./run_latency_test_xapp.sh --e2-node-id gnbd_001_001_00019b_0 --report-style 4
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container)
      CONTAINER="$2"
      shift 2
      ;;
    --e2-node-id|--e2_node_id)
      E2_NODE_ID="$2"
      shift 2
      ;;
    --ran-func-id|--ran_func_id)
      RAN_FUNC_ID="$2"
      shift 2
      ;;
    --report-style|--report_style)
      REPORT_STYLE="$2"
      shift 2
      ;;
    --ue-ids|--ue_ids)
      UE_IDS="$2"
      shift 2
      ;;
    --report-period-ms|--report_period_ms)
      REPORT_PERIOD_MS="$2"
      shift 2
      ;;
    --granul-period-ms|--granul_period_ms)
      GRANUL_PERIOD_MS="$2"
      shift 2
      ;;
    --http-port|--http_port)
      HTTP_PORT="$2"
      shift 2
      ;;
    --rmr-port|--rmr_port)
      RMR_PORT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      print_error "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  print_error "docker command not found"
  exit 1
fi

if [[ ! -f "${XAPP_FILE}" ]]; then
  print_error "Missing xApp file: ${XAPP_FILE}"
  exit 1
fi

if [[ ! -d "${SCRIPT_DIR}/lib" ]]; then
  print_error "Missing lib directory: ${SCRIPT_DIR}/lib"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  print_error "Container '${CONTAINER}' is not running"
  exit 1
fi

if [[ "${REPORT_STYLE}" == "5" ]]; then
  IFS=',' read -r -a parsed_ue_ids <<< "${UE_IDS}"
  if [[ ${#parsed_ue_ids[@]} -lt 2 ]]; then
    print_error "Report style 5 requires at least two UE IDs in --ue-ids"
    exit 1
  fi
fi

print_info "Copying xApp files into container ${CONTAINER}"
docker cp "${XAPP_FILE}" "${CONTAINER}:/root/latency_test_xapp.py"
docker cp "${SCRIPT_DIR}/lib" "${CONTAINER}:/root/lib"

print_info "Starting latency_test_xapp.py in ${CONTAINER}"
print_info "Metrics: DRB.AirIfDelayUl, DRB.RlcPacketDropRateDl, DRB.RlcDelayUl"

exec docker exec -it "${CONTAINER}" bash -lc "
  cd /root && \
  python3 -u ./latency_test_xapp.py \
    --e2_node_id '${E2_NODE_ID}' \
    --ran_func_id '${RAN_FUNC_ID}' \
    --report_style '${REPORT_STYLE}' \
    --ue_ids '${UE_IDS}' \
    --report_period_ms '${REPORT_PERIOD_MS}' \
    --granul_period_ms '${GRANUL_PERIOD_MS}' \
    --http_server_port '${HTTP_PORT}' \
    --rmr_port '${RMR_PORT}'
"
