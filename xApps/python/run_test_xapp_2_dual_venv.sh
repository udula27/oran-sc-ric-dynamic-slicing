#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

RIC_VENV="${RIC_VENV:-${REPO_ROOT}/ric_env}"
STREAMLIT_VENV="${STREAMLIT_VENV:-${REPO_ROOT}/streamlit_env}"

RIC_PYTHON="${RIC_PYTHON:-${RIC_VENV}/bin/python3}"
STREAMLIT_PYTHON="${STREAMLIT_PYTHON:-${STREAMLIT_VENV}/bin/python3}"

XAPP_FILE="${SCRIPT_DIR}/test_xapp_2.py"
DASHBOARD_FILE="${SCRIPT_DIR}/test_xapp_2_dashboard.py"

E2_NODE_ID="${E2_NODE_ID:-gnbd_001_001_00019b_0}"
KPM_RAN_FUNC_ID="${KPM_RAN_FUNC_ID:-2}"
RC_RAN_FUNC_ID="${RC_RAN_FUNC_ID:-3}"
SLICES="${SLICES:-1,2,3}"
SD="${SD:-0}"
THROUGHPUT_THRESHOLD="${THROUGHPUT_THRESHOLD:-30000}"
IDLE_PRB_THRESHOLD="${IDLE_PRB_THRESHOLD:-20}"
PRB_SHIFT_STEP="${PRB_SHIFT_STEP:-5}"
CONTROL_COOLDOWN_S="${CONTROL_COOLDOWN_S:-5}"
HTTP_PORT="${HTTP_PORT:-8097}"
RMR_PORT="${RMR_PORT:-4560}"

DASHBOARD_PORT="${DASHBOARD_PORT:-8502}"
DASHBOARD_STATE_FILE="${DASHBOARD_STATE_FILE:-/tmp/test_xapp_2_state.json}"
DASHBOARD_REFRESH_SECONDS="${DASHBOARD_REFRESH_SECONDS:-1.0}"

XAPP_PID=""
DASH_PID=""

print_info() {
    echo "[INFO] $1"
}

print_warn() {
    echo "[WARN] $1"
}

print_error() {
    echo "[ERROR] $1"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [--xapp-args "<extra test_xapp_2 args>"]

Runs test_xapp_2 using two virtual environments:
  - RIC/xApp env:      ${RIC_VENV}
  - Streamlit env:     ${STREAMLIT_VENV}

Environment overrides:
  RIC_VENV, STREAMLIT_VENV, RIC_PYTHON, STREAMLIT_PYTHON
  E2_NODE_ID, KPM_RAN_FUNC_ID, RC_RAN_FUNC_ID, SLICES, SD
  THROUGHPUT_THRESHOLD, IDLE_PRB_THRESHOLD, PRB_SHIFT_STEP, CONTROL_COOLDOWN_S
  HTTP_PORT, RMR_PORT, DASHBOARD_PORT, DASHBOARD_STATE_FILE, DASHBOARD_REFRESH_SECONDS

Example:
  E2_NODE_ID=gnbd_001_001_00019b_0 DASHBOARD_PORT=8503 ./run_test_xapp_2_dual_venv.sh
EOF
}

EXTRA_XAPP_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --xapp-args)
            shift
            if [[ $# -eq 0 ]]; then
                print_error "--xapp-args requires a value"
                exit 1
            fi
            read -r -a EXTRA_XAPP_ARGS <<< "$1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_error "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ ! -x "${RIC_PYTHON}" ]]; then
    print_error "RIC python not found/executable: ${RIC_PYTHON}"
    print_error "Create it with: python3 -m venv ${RIC_VENV}"
    exit 1
fi

if [[ ! -x "${STREAMLIT_PYTHON}" ]]; then
    print_error "Streamlit python not found/executable: ${STREAMLIT_PYTHON}"
    print_error "Create it with: python3 -m venv ${STREAMLIT_VENV}"
    exit 1
fi

if ! "${RIC_PYTHON}" -c "import ctypes; ctypes.CDLL('librmr_si.so')" >/dev/null 2>&1; then
    print_error "RIC host runtime missing native RMR library: librmr_si.so"
    print_warn "Host dual-venv mode requires RMR C library installed on host (LD_LIBRARY_PATH/librmr_si.so)."
    print_warn "Use Docker launcher instead for this environment:"
    print_warn "  ./oran-sc-ric/xApps/python/run_test_xapp_2_docker_dual_venv.sh"
    exit 1
fi

if [[ ! -f "${XAPP_FILE}" ]]; then
    print_error "xApp file not found: ${XAPP_FILE}"
    exit 1
fi

if [[ ! -f "${DASHBOARD_FILE}" ]]; then
    print_error "Dashboard file not found: ${DASHBOARD_FILE}"
    exit 1
fi

if ! "${STREAMLIT_PYTHON}" -c "import streamlit" >/dev/null 2>&1; then
    print_error "streamlit is not installed in ${STREAMLIT_VENV}"
    print_error "Install with: ${STREAMLIT_PYTHON} -m pip install streamlit"
    exit 1
fi

cleanup() {
    print_info "Stopping processes..."
    if [[ -n "${XAPP_PID}" ]] && kill -0 "${XAPP_PID}" >/dev/null 2>&1; then
        kill "${XAPP_PID}" >/dev/null 2>&1 || true
        wait "${XAPP_PID}" 2>/dev/null || true
    fi
    if [[ -n "${DASH_PID}" ]] && kill -0 "${DASH_PID}" >/dev/null 2>&1; then
        kill "${DASH_PID}" >/dev/null 2>&1 || true
        wait "${DASH_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

print_info "RIC python: ${RIC_PYTHON}"
print_info "Streamlit python: ${STREAMLIT_PYTHON}"
print_info "Dashboard URL: http://127.0.0.1:${DASHBOARD_PORT}"
print_info "State file: ${DASHBOARD_STATE_FILE}"

export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
export STREAMLIT_SERVER_HEADLESS=true

"${RIC_PYTHON}" "${XAPP_FILE}" \
    --e2_node_id "${E2_NODE_ID}" \
    --kpm_ran_func_id "${KPM_RAN_FUNC_ID}" \
    --rc_ran_func_id "${RC_RAN_FUNC_ID}" \
    --slices "${SLICES}" \
    --sd "${SD}" \
    --throughput_threshold "${THROUGHPUT_THRESHOLD}" \
    --idle_prb_threshold "${IDLE_PRB_THRESHOLD}" \
    --prb_shift_step "${PRB_SHIFT_STEP}" \
    --control_cooldown_s "${CONTROL_COOLDOWN_S}" \
    --http_server_port "${HTTP_PORT}" \
    --rmr_port "${RMR_PORT}" \
    --dashboard_enabled 0 \
    --dashboard_state_file "${DASHBOARD_STATE_FILE}" \
    --dashboard_refresh_seconds "${DASHBOARD_REFRESH_SECONDS}" \
    "${EXTRA_XAPP_ARGS[@]}" &
XAPP_PID=$!

"${STREAMLIT_PYTHON}" -m streamlit run "${DASHBOARD_FILE}" \
    --server.port "${DASHBOARD_PORT}" \
    --server.address 0.0.0.0 \
    --browser.gatherUsageStats false \
    --server.headless true \
    -- --state-file "${DASHBOARD_STATE_FILE}" \
       --refresh-seconds "${DASHBOARD_REFRESH_SECONDS}" \
       --slices "${SLICES}" </dev/null &
DASH_PID=$!

print_info "xApp PID=${XAPP_PID}, dashboard PID=${DASH_PID}"
print_info "Press Ctrl+C to stop both"

wait "${XAPP_PID}"