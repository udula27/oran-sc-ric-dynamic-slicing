#!/usr/bin/env bash
# run_test_xapp_3_docker_dual_venv.sh
#
# Runs test_xapp_3 inside a Docker container (xApp process) while the
# Streamlit dashboard runs on the host in a separate Python venv.
#
# Policy: asymmetric sharing graph
#   Emergency ← eMBB (α=0.7), Emergency ← IoT (γ=0.3), eMBB ← IoT (1.0)
#   min_quota=[20,30,10] thresh_bps=[50M,5M,1M]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

CONTAINER="${CONTAINER:-python_xapp_runner}"

RIC_VENV="${RIC_VENV:-${REPO_ROOT}/ric_env}"
RIC_PYTHON="${RIC_PYTHON:-${RIC_VENV}/bin/python3}"

STREAMLIT_VENV="${STREAMLIT_VENV:-${REPO_ROOT}/streamlit_env}"
STREAMLIT_PYTHON="${STREAMLIT_PYTHON:-${STREAMLIT_VENV}/bin/python3}"

XAPP_NAME="test_xapp_3.py"
DASHBOARD_NAME="test_xapp_3_dashboard.py"

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

DASHBOARD_PORT="${DASHBOARD_PORT:-8503}"
DASHBOARD_STATE_FILE="${DASHBOARD_STATE_FILE:-/tmp/test_xapp_3_state.json}"
DASHBOARD_REFRESH_SECONDS="${DASHBOARD_REFRESH_SECONDS:-1.0}"
CONTAINER_STATE_FILE="${CONTAINER_STATE_FILE:-/tmp/test_xapp_3_state.json}"

XAPP_EXEC_PID=""
DASH_PID=""
STATE_SYNC_PID=""

print_info() {
    echo "[INFO] $1"
}

print_warn() {
    echo "[WARN] $1"
}

print_error() {
    echo "[ERROR] $1"
}

ensure_venv() {
    local venv_path="$1"
    local py_path="$2"
    if [[ -x "${py_path}" ]]; then
        return
    fi

    print_info "Creating virtual environment: ${venv_path}"
    python3 -m venv "${venv_path}"
}

ensure_ric_env() {
    ensure_venv "${RIC_VENV}" "${RIC_PYTHON}"
    print_info "Ensuring RIC dependencies in ${RIC_VENV}"
    "${RIC_PYTHON}" -m pip install -q --upgrade pip
    "${RIC_PYTHON}" -m pip install -q "ricxappframe" "protobuf<3.21"
}

ensure_streamlit_env() {
    ensure_venv "${STREAMLIT_VENV}" "${STREAMLIT_PYTHON}"
    print_info "Ensuring Streamlit dependencies in ${STREAMLIT_VENV}"
    "${STREAMLIT_PYTHON}" -m pip install -q --upgrade pip
    "${STREAMLIT_PYTHON}" -m pip install -q streamlit
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [--xapp-args "<extra test_xapp_3 args>"]

Runs test_xapp_3 in Docker + Streamlit dashboard on host:
  - xApp location: container ${CONTAINER}
  - dashboard env: ${STREAMLIT_VENV}

Policy: Emergency←eMBB(α=0.7), Emergency←IoT(γ=0.3), eMBB←IoT
        min_quota=[20,30,10]  thresh_bps=[50M,5M,1M]

Environment overrides:
    CONTAINER, RIC_VENV, RIC_PYTHON, STREAMLIT_VENV, STREAMLIT_PYTHON
    E2_NODE_ID, KPM_RAN_FUNC_ID, RC_RAN_FUNC_ID, SLICES, SD
    THROUGHPUT_THRESHOLD, IDLE_PRB_THRESHOLD, PRB_SHIFT_STEP, CONTROL_COOLDOWN_S
    HTTP_PORT, RMR_PORT, DASHBOARD_PORT, DASHBOARD_STATE_FILE, DASHBOARD_REFRESH_SECONDS
    CONTAINER_STATE_FILE

Example:
  CONTAINER=python_xapp_runner DASHBOARD_PORT=8503 ./run_test_xapp_3_docker_dual_venv.sh
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

if ! command -v docker >/dev/null 2>&1; then
    print_error "docker command not found"
    exit 1
fi

ensure_ric_env
ensure_streamlit_env

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    print_error "Container '${CONTAINER}' is not running"
    exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/${XAPP_NAME}" ]]; then
    print_error "Missing file: ${SCRIPT_DIR}/${XAPP_NAME}"
    exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/${DASHBOARD_NAME}" ]]; then
    print_error "Missing file: ${SCRIPT_DIR}/${DASHBOARD_NAME}"
    exit 1
fi

if [[ ! -d "${SCRIPT_DIR}/lib" ]]; then
    print_error "Missing directory: ${SCRIPT_DIR}/lib"
    exit 1
fi

cleanup() {
    print_info "Stopping processes..."

    if [[ -n "${XAPP_EXEC_PID}" ]] && kill -0 "${XAPP_EXEC_PID}" >/dev/null 2>&1; then
        kill "${XAPP_EXEC_PID}" >/dev/null 2>&1 || true
        wait "${XAPP_EXEC_PID}" 2>/dev/null || true
    fi

    if [[ -n "${STATE_SYNC_PID}" ]] && kill -0 "${STATE_SYNC_PID}" >/dev/null 2>&1; then
        kill "${STATE_SYNC_PID}" >/dev/null 2>&1 || true
        wait "${STATE_SYNC_PID}" 2>/dev/null || true
    fi

    docker exec "${CONTAINER}" bash -lc "pkill -f 'python3 -u ./test_xapp_3.py' || true" >/dev/null 2>&1 || true

    if [[ -n "${DASH_PID}" ]] && kill -0 "${DASH_PID}" >/dev/null 2>&1; then
        kill "${DASH_PID}" >/dev/null 2>&1 || true
        wait "${DASH_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

print_info "Copying xApp files to container ${CONTAINER}..."
docker cp "${SCRIPT_DIR}/${XAPP_NAME}" "${CONTAINER}:/root/${XAPP_NAME}" 2>/dev/null || true
docker cp "${SCRIPT_DIR}/test_xapp_1.py" "${CONTAINER}:/root/test_xapp_1.py" 2>/dev/null || true
docker cp "${SCRIPT_DIR}/lib" "${CONTAINER}:/root/lib" 2>/dev/null || true

echo '{}' > "${DASHBOARD_STATE_FILE}"

print_info "Starting xApp in container ${CONTAINER}"
docker exec -i "${CONTAINER}" bash -lc "
    cd /root && \
    python3 -u ./${XAPP_NAME} \
      --e2_node_id '${E2_NODE_ID}' \
      --kpm_ran_func_id '${KPM_RAN_FUNC_ID}' \
      --rc_ran_func_id '${RC_RAN_FUNC_ID}' \
      --slices '${SLICES}' \
      --sd '${SD}' \
      --throughput_threshold '${THROUGHPUT_THRESHOLD}' \
      --idle_prb_threshold '${IDLE_PRB_THRESHOLD}' \
      --prb_shift_step '${PRB_SHIFT_STEP}' \
      --control_cooldown_s '${CONTROL_COOLDOWN_S}' \
      --http_server_port '${HTTP_PORT}' \
      --rmr_port '${RMR_PORT}' \
      --dashboard_enabled 0 \
      --dashboard_state_file '${CONTAINER_STATE_FILE}' \
      --dashboard_refresh_seconds '${DASHBOARD_REFRESH_SECONDS}' \
      ${EXTRA_XAPP_ARGS[*]}
" &
XAPP_EXEC_PID=$!

print_info "Starting state sync: ${CONTAINER}:${CONTAINER_STATE_FILE} -> ${DASHBOARD_STATE_FILE}"
(
    while true; do
        docker cp "${CONTAINER}:${CONTAINER_STATE_FILE}" "${DASHBOARD_STATE_FILE}" >/dev/null 2>&1 || true
        sleep 1
    done
) &
STATE_SYNC_PID=$!

print_info "Starting Streamlit dashboard on host"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
export STREAMLIT_SERVER_HEADLESS=true
"${STREAMLIT_PYTHON}" -m streamlit run "${SCRIPT_DIR}/${DASHBOARD_NAME}" \
    --server.port "${DASHBOARD_PORT}" \
    --server.address 0.0.0.0 \
    --browser.gatherUsageStats false \
    --server.headless true \
    -- --state-file "${DASHBOARD_STATE_FILE}" \
       --refresh-seconds "${DASHBOARD_REFRESH_SECONDS}" \
       --slices "${SLICES}" </dev/null &
DASH_PID=$!

print_info "xApp launcher PID=${XAPP_EXEC_PID}, dashboard PID=${DASH_PID}"
print_info "Dashboard URL: http://127.0.0.1:${DASHBOARD_PORT}"
print_info "Press Ctrl+C to stop both"

wait "${XAPP_EXEC_PID}"
