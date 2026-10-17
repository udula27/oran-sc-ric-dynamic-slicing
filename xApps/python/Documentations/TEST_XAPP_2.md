# test_xapp_2.py - Run Guide (xApp + Streamlit)

This document shows the two supported one-command launch options for `test_xapp_2.py`.

## Prerequisites

- `ric_env` exists with xApp deps installed (`ricxappframe`, `protobuf<3.21`)
- `streamlit_env` exists with `streamlit` installed
- RIC platform and gNB are up
- E2 node is registered (example: `gnbd_001_001_00019b_0`)

---

## Option A: Host dual-venv launcher (xApp + dashboard on host)

Use this when your host Python can reach RIC networking directly.

From repo root:

```bash
./oran-sc-ric/xApps/python/run_test_xapp_2_dual_venv.sh
```

Example with overrides:

```bash
E2_NODE_ID=gnbd_001_001_00019b_0 \
RMR_PORT=4563 \
HTTP_PORT=8097 \
DASHBOARD_PORT=8502 \
./oran-sc-ric/xApps/python/run_test_xapp_2_dual_venv.sh
```

---

## Option B: Docker xApp + host Streamlit launcher

Use this when xApp must run in `python_xapp_runner` for RMR/route compatibility.

From repo root:

```bash
./oran-sc-ric/xApps/python/run_test_xapp_2_docker_dual_venv.sh
```

Example with overrides:

```bash
CONTAINER=python_xapp_runner \
E2_NODE_ID=gnbd_001_001_00019b_0 \
RMR_PORT=4563 \
HTTP_PORT=8097 \
DASHBOARD_PORT=8502 \
./oran-sc-ric/xApps/python/run_test_xapp_2_docker_dual_venv.sh
```

---

## Dashboard

- URL: `http://127.0.0.1:8502` (or your `DASHBOARD_PORT` override)
- Shows:
  - PRB allocation bars (RC policy)
  - PRB utilization bars (KPM style 1 DL+UL)
  - UE list by slice (RC report style 4)
  - UE throughput by slice (KPM report style 4)

---

## Stop behavior

- Press `Ctrl+C` in launcher terminal.
- Both xApp and dashboard processes are terminated by the launcher trap handler.

---

## Useful environment variables

- Common:
  - `E2_NODE_ID`, `SLICES`, `SD`
  - `KPM_RAN_FUNC_ID`, `RC_RAN_FUNC_ID`
  - `THROUGHPUT_THRESHOLD`, `IDLE_PRB_THRESHOLD`, `PRB_SHIFT_STEP`, `CONTROL_COOLDOWN_S`
  - `HTTP_PORT`, `RMR_PORT`, `DASHBOARD_PORT`, `DASHBOARD_STATE_FILE`, `DASHBOARD_REFRESH_SECONDS`
- Host dual-venv launcher:
  - `RIC_VENV`, `RIC_PYTHON`, `STREAMLIT_VENV`, `STREAMLIT_PYTHON`
- Docker launcher:
  - `CONTAINER`, `STREAMLIT_VENV`, `STREAMLIT_PYTHON`
