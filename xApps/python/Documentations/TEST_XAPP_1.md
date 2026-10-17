# test_xapp_1.py - Integrated Closed-Loop Dynamic Slice Control xApp

## Overview
`test_xapp_1.py` combines your implemented components into a single runtime loop:

1. **RC Style 4 UE context tracking**
   - Maintains UE context cache from RC Style 4 indications.
   - Detects and logs UE connection/disconnection events.

2. **KPM monitoring (UE throughput + slice PRB usage)**
   - Subscribes to **UE throughput** metrics (`DRB.UEThpDl`, `DRB.UEThpUl`) using KPM report style 4.
   - Subscribes to **slice PRB utilization** (`RRU.PrbUsedDl`, `RRU.PrbUsedUl`) per configured slice SST using `SLICE_ID_LABEL`.

3. **Closed-loop RC control**
   - If a UE throughput in a slice is below threshold, xApp searches for an **idle slice**.
   - It shifts PRB ratio budget from donor slice to target slice by a fixed step.
   - Sends E2SM-RC control action 2.6 (`control_slice_level_prb_quota`) for both donor and target slices.

---

## Logging Behavior
The xApp logs only key events (as requested):

- **UE events**
  - `[UE_EVENT][CONNECT] ...`
  - `[UE_EVENT][DISCONNECT] ...`

- **Threshold breach events**
  - `[THRESHOLD_BREACH] ue_id=... sst=... throughput=... < threshold`

- **RC control events**
  - `[RC_CONTROL_EVENT] donor_sst=... min=... max=... ded=...`
  - `[RC_CONTROL_EVENT] target_sst=... min=... max=... ded=...`
  - `[RC_CONTROL_EVENT][ACK] ...`
  - `[RC_CONTROL_EVENT][FAILURE] ...`

KPM samples are processed continuously but not printed every interval.

---

## Control Policy (Simple)
### Inputs
- UE throughput threshold: `--throughput_threshold`
- Idle slice PRB threshold: `--idle_prb_threshold`
- PRB move step: `--prb_shift_step`
- Control cooldown: `--control_cooldown_s`
- Startup equal split: `--init_equal_split`, `--init_min_ratio`
- UE attach grace: `--ue_attach_grace_s`
- Traffic activation threshold: `--activation_throughput_kbps`
- Consecutive breach requirement: `--min_consecutive_breaches`

### Decision
0. (Optional at startup) Apply near-equal max PRB split across configured slices.
1. Find a UE whose throughput is below threshold.
2. Identify its slice (from RC UE context).
3. Find a donor slice that is idle:
   - no active UEs in that slice, **or**
   - low PRB load (`RRU.PrbUsedDl + RRU.PrbUsedUl <= idle_prb_threshold`).
4. Trigger only after `min_consecutive_breaches` below-threshold samples.
5. Move PRB budget by `prb_shift_step`:
   - donor `max -= step`
   - target `max += step`
6. Send RC control requests for donor and target slices.

### Anti-Transient Guards
To avoid false control reactions right after UE attach (e.g., temporary `0 kbps` before traffic starts), the xApp uses:

1. **UE attach grace window**
   - Skip threshold checks for first `ue_attach_grace_s` seconds after UE connect.

2. **Traffic activation gate**
   - Do not evaluate threshold until UE throughput reaches at least `activation_throughput_kbps` once.

3. **Consecutive breach gate**
   - Require `min_consecutive_breaches` consecutive below-threshold samples before RC control is sent.

---

## Run Command (Docker Exec)
From your RIC directory, run inside `python_xapp_runner` container:

```bash
docker exec python_xapp_runner python3 -u ./test_xapp_1.py \
  --e2_node_id gnbd_001_001_00019b_0 \
  --kpm_ran_func_id 2 \
  --rc_ran_func_id 3 \
  --slices 1,2,3 \
  --sd 0 \
   --throughput_threshold 30000 \
  --idle_prb_threshold 20 \
  --prb_shift_step 5 \
  --control_cooldown_s 5 \
   --init_equal_split 1 \
   --init_min_ratio 10 \
   --ue_attach_grace_s 30 \
   --activation_throughput_kbps 500 \
   --min_consecutive_breaches 3 \
  --http_server_port 8096 \
   --rmr_port 4563
```

---

## Arguments
- `--e2_node_id`: E2 node ID registered in RIC.
- `--kpm_ran_func_id`: KPM RAN function ID (default `2`).
- `--rc_ran_func_id`: RC RAN function ID (default `3`).
- `--slices`: comma-separated SST list for monitoring/control (default `1,2,3`).
- `--sd`: SD value used in labels/control (default `0`).
- `--report_period_ms`: KPM report period (default `1000`).
- `--throughput_threshold`: breach threshold for `DRB.UEThp*` (default `1000`).
- `--idle_prb_threshold`: idle threshold for `RRU.PrbUsedDl + RRU.PrbUsedUl` (default `20`).
- `--prb_shift_step`: PRB ratio shift step (default `5`).
- `--control_cooldown_s`: minimum interval between RC actions (default `5`).
- `--init_equal_split`: apply near-equal startup allocation (default `1` = enabled).
- `--init_min_ratio`: startup min ratio used with equal split (default `10`).
- `--ue_attach_grace_s`: grace period after UE connect before threshold checks (default `20`).
- `--activation_throughput_kbps`: throughput needed to mark UE traffic active (default `100`).
- `--min_consecutive_breaches`: number of consecutive breaches required to trigger RC control (default `3`).
- `--http_server_port`: xApp HTTP callback port (default `8096`).
- `--rmr_port`: xApp RMR port (default `4563`).

---

## Notes
- Designed for **closed-loop behavior testing** with simple policy logic.
- Keeps logic intentionally minimal and deterministic for easier observation.
- If UE ID encoding differs between RC and KPM indications, the xApp includes a single-UE fallback mapping path.
