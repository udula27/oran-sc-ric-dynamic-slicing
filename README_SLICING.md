# oran-sc-ric with a Dynamic Slice-Level PRB Allocation xApp

This is a modified fork of [srsran/oran-sc-ric](https://github.com/srsran/oran-sc-ric) at base commit `e44a7ce`, accompanying our work on an open-source 5G testbed with dynamic network slicing.

- Branch: `dynamic-slicing-xapp`
- Tag: `xapp-dynamic-slicing-final`

## Changes

- A Python xApp implementing graph-based inter-slice PRB allocation.
- Slice-level PRB telemetry through E2SM-KPM.
- Slice quota control through E2SM-RC.
- A monitoring dashboard.
- Testbed scripts and configurations.

## Running with the gNB fork

The matching gNB fork is [udula27/srsRAN_Project_Dynamic_Slicing](https://github.com/udula27/srsRAN_Project_Dynamic_Slicing), using branch `slicing-e2sm-rc` and tag `srsRAN-dynamic-slicing-final`.

1. Start the Near-RT RIC from this checkout:

   ```bash
   docker compose up -d
   ```

2. Check out the gNB fork on the matching branch (or use the fixed tag):

   ```bash
   git clone https://github.com/udula27/srsRAN_Project_Dynamic_Slicing.git
   cd srsRAN_Project_Dynamic_Slicing
   git checkout slicing-e2sm-rc
   # For the tagged snapshot instead:
   git checkout srsRAN-dynamic-slicing-final
   ```

   Build and launch the gNB with E2 enabled, configured to connect to this RIC. Follow the gNB fork's instructions for its build and radio/testbed configuration.

3. Get the registered E2 node ID from the RIC, then run the xApp and dashboard using the included Docker runner:

   ```bash
   docker compose logs rtmgr_sim | grep ranNamelist
   E2_NODE_ID=<registered-e2-node-id> xApps/python/run_test_xapp_3_docker_dual_venv.sh
   ```

   The runner copies `test_xapp_3.py` and its library into `python_xapp_runner`, starts the controller, and launches the dashboard. Its default dashboard port is `8503`; the runner has environment-variable overrides for runtime options.

## Allocation parameters

The graph-based policy is implemented by `xApps/python/test_xapp_3.py`. Slice order for the quota and throughput arrays is eMBB, Emergency, IoT.

| Parameter | Value | Meaning |
|---|---:|---|
| `min_quota` | `[20, 30, 10]` PRBs (%) | Minimum quota for eMBB, Emergency, and IoT |
| `thresh_bps` | `[50e6, 5e6, 1e6]` bps | Per-slice throughput targets |
| `alpha` | `0.7` | Emergency borrows from eMBB |
| `gamma` | `0.3` | Emergency borrows from IoT |
| eMBB sharing weight | `1.0` | eMBB borrows from IoT |
| IoT sharing edges | None | IoT has no borrowing rights |
| Donor utilization threshold | `0.5` | Donor must be below this utilization to lend |
| `beta` | `0.5` | Quota smoothing factor |
| EMA old/new weights | `0.7` / `0.3` | Previous and proposed quota weights |
| `delta_max` | TODO | No value found |
| `eta` | TODO | No value found |

## Data availability

TODO: Add a dataset link. Experiment CSVs are not included in this repository.

## Diff from upstream

```bash
git diff e44a7ce..xapp-dynamic-slicing-final --stat
```

## License

This fork keeps the upstream AGPL-3.0 license.
