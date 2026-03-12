# RAN Mode Switching

This guide explains how to switch between Physical RAN, UERANSIM, and coexistence mode using the dashboard.

> **Dashboard setup**: see [Dashboard Overview](../dashboard/overview.md) for how to access and configure the dashboard.
> **Full API reference**: see [Dashboard API Reference](../dashboard/api-reference.md).
> **Physical RAN hardware setup**: see [Physical RAN Integration](physical-ran.md).

---

## Concepts

The testbed supports three RAN configurations, selectable at runtime without redeploying the 5G Core:

| Mode | What is active | Use case |
|------|----------------|----------|
| **Simulated (UERANSIM)** | gNB + UE pods on edge node | Development, protocol testing, no hardware needed |
| **Physical RAN** | Real femtocell connected via OVS bridge | Integration testing, real UE experiments |
| **Coexistence** | Both modes active simultaneously | Mixed scenarios |

Switching modes is **non-destructive**: the 5G Core (AMF, SMF, UPF) keeps running. Only the RAN side changes.

---

## Current Status

```
GET /api/v1/ran/status
```

Returns the current state:
```json
{
  "physical": {"enabled": false, "bridge_mode": "none"},
  "simulated": {"enabled": true, "gnbs": 1, "ues": 2},
  "coexistence": false
}
```

In the dashboard UI: open **RAN** from the left sidebar. The status cards show the active configuration at a glance.

---

## Switching to UERANSIM

### Enable

```
POST /api/v1/ran/enable
{"mode": "simulated"}
```

The backend scales the gNB Deployment and UE StatefulSet up to the last-known replica count (persisted in ConfigMap `ueransim-dashboard-state`).

### Disable

```
POST /api/v1/ran/disable
{"mode": "simulated"}
```

Scales gNB and UE pods to 0. The 5G Core continues running.

### Manage gNB and UE resources

| Operation | Endpoint | Notes |
|-----------|----------|-------|
| Create gNB (guided form) | `POST /api/v1/ran/ueransim/gnbs/form` | Creates Deployment + ConfigMap |
| Create UE (guided form) | `POST /api/v1/ran/ueransim/ues/form` | Creates StatefulSet + ConfigMap |
| Scale gNB | `PATCH /api/v1/ran/ueransim/gnbs/{name}/scale` | Body: `{"replicas": N}` |
| Scale UE | `PATCH /api/v1/ran/ueransim/ues/{name}/scale` | Body: `{"replicas": N}` |
| Delete gNB | `DELETE /api/v1/ran/ueransim/gnbs/{name}` | Removes Deployment + ConfigMap |
| Delete UE | `DELETE /api/v1/ran/ueransim/ues/{name}` | Removes StatefulSet + ConfigMap |

---

## Switching to Physical RAN

### Prerequisites

1. Physical RAN hardware connected to the host NIC
2. Worker VM bridged to that NIC (via `PHYSICAL_RAN_BRIDGE=<nic> vagrant reload worker`)
3. See [Physical RAN Integration](physical-ran.md) for hardware setup

### Enable

```
POST /api/v1/ran/enable
{"mode": "physical"}
```

The backend applies the prerequisites idempotently:
- Creates `br-ran` OVS bridge and patch ports (if not already present)
- Patches AMF Multus annotation to add the `n2phy` interface on the RAN subnet
- Ensures UPF-Cloud has the return route for the physical RAN subnet

Enable is **idempotent**: re-running it does not restart OVS or AMF if the configuration is already correct.

### Disable

```
POST /api/v1/ran/disable
{"mode": "physical"}
```

Removes `br-ran` patch ports and the AMF `n2phy` annotation.

---

## Coexistence Mode

Both UERANSIM and Physical RAN can run simultaneously. The 5G Core handles both RAN connections normally — each gNB (simulated or physical) registers with AMF independently.

To enable coexistence, simply enable both modes:

```
POST /api/v1/ran/enable  {"mode": "physical"}
POST /api/v1/ran/enable  {"mode": "simulated"}
```

To return to a single mode, disable the one you no longer need.

---

## Scheduling

Default placement (recommended):

| Workload | Node | Reason |
|----------|------|--------|
| gNB, UE pods | edge | KubeEdge edge node; mirrors a cell site deployment |
| 5GC control/user plane | worker | Full Kubernetes agent; OVS bridges; MongoDB |

The Topology Map module shows actual pod placement via `pod.spec.nodeName`.

---

## Operational Notes

- **Replica state persistence**: UERANSIM replica counts are persisted in ConfigMap `ueransim-dashboard-state`. Disabling UERANSIM (scale to 0) and re-enabling it restores the previous count.
- **Runtime extensions**: gNBs/UEs created from dashboard forms are persisted in ConfigMap and re-applied on status refresh, on top of the Ansible baseline.
- **Bridge mode source of truth**: the dashboard reads `RAN_BRIDGE_MODE` from the running OVS DaemonSet environment — this is the live state, not the Ansible configuration.

---

## Related Documentation

- [Dashboard Overview](../dashboard/overview.md) — dashboard architecture and access
- [Dashboard API Reference](../dashboard/api-reference.md) — full API endpoint listing
- [Physical RAN Integration](physical-ran.md) — hardware setup for femtocell connection
- [Dashboard Module: Physical RAN Config](../dashboard/modules.md#module-6-physical-ran-config) — UI walkthrough
