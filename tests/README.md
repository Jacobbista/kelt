# 5G K3s KubeEdge Testbed - Test Suite

Automated test suite for validating the 5G testbed deployment.

## Quick Start

```bash
cd tests

# Run all tests (kubeconfig is fetched automatically)
python3 run_tests.py

# Run specific suite
python3 run_tests.py -s e2e
python3 run_tests.py -s protocols

# Verbose output
python3 run_tests.py -v
```

## Prerequisites

- Vagrant VMs running (`vagrant up` from project root)
- Python 3.8+

The test runner automatically:
1. Checks if VMs are running
2. Fetches fresh kubeconfig from master VM
3. Sets up virtual environment
4. Installs dependencies

## Test Suites

| Suite | Description | Status | Command |
|-------|-------------|--------|---------|
| `e2e` | End-to-end deployment validation | ✅ Enabled | `make e2e` |
| `protocols` | 5G protocol tests (NGAP, PFCP, GTP-U) | ✅ Enabled | `make protocols` |
| `iam` | Keycloak realm + token validation (phase 08) | ✅ Enabled | `make iam` |
| `performance` | Throughput and latency benchmarks | ⏸️ Disabled* | `make performance` |
| `resilience` | Failure recovery tests | ⏸️ Disabled* | `make resilience` |
| `ran` | Physical RAN integration tests | ✅ Enabled | `make ran` |

Recent E2E additions:
- RAN mode primitives validation (resource labels for dashboard control)
- RAN overlay labeling validation (`managed-by: ansible|dashboard`)
- Edge placement semantics validation (gNB/UE on edge nodes)

\* See [Disabled Suites](#disabled-suites) for requirements

## Directory Structure

```
tests/
├── run_tests.py        # Main test runner (start here)
├── kubeconfig          # Auto-fetched from master VM
├── test_config.yaml    # Test configuration
├── requirements.txt    # Python dependencies
│
├── core/               # Core E2E tests
│   └── test_e2e.py
├── protocols/          # 5G protocol tests
│   └── test_5g_protocols.py
├── iam/                # Keycloak realm + token tests
│   └── test_iam.py
├── performance/        # Performance benchmarks
│   └── test_performance.py
├── resilience/         # Failure recovery tests
│   └── test_resilience.py
├── ran/                # Physical RAN tests
│   └── test_physical_ran.py
│
└── utils/              # Shared utilities
    ├── k8s_client.py       # Kubernetes API client
    ├── kubectl_client.py   # Backward compat alias
    └── test_helpers.py     # Test utilities
```

## IAM Suite

Validates the Keycloak realm deployed by phase 08: pods Running, the OIDC
discovery document, the master admin credential, the imported clients and
realm roles, and a CAMARA gateway service-account token. It is an
integration suite (it talks to the live NodePort), not a unit test, so it
requires the testbed to be up and phase 08 deployed.

Run it after deploying phase 08:

```bash
make iam

# Verbose: prints discovery endpoints, full client/role lists, and token
# claims (azp, expires_in, roles). Never prints secrets or the bearer token.
# (make consumes a bare -v, so verbose is passed as V=1.)
make iam V=1
```

Token tests need secrets. They are read from the environment first
(`KEYCLOAK_ADMIN_PASSWORD`, `CAMARA_CLIENT_SECRET`), then from the
project-root `.testbed.secrets` file as a convenience. When a secret is
absent, the dependent test is **skipped, not failed**, so the suite still
runs in a secret-less checkout. The `iam` block in `test_config.yaml` must
mirror the phase 08 role defaults (NodePort, path prefix, realm, expected
clients and roles).

Application-level auth logic (JWT validation, 401 on bad token) belongs in
each downstream service's own repository, where it runs in cloud CI without
a cluster. This suite only validates the deployed identity provider.

## Network probes (netshoot)

The per-NF images are minimal and ship no `ip`/`ss`/`ping`, so the e2e and
protocols suites do not exec network tools inside NF pods. Instead:

- Interface IPs are read from the Multus `k8s.v1.cni.cncf.io/network-status`
  annotation (no exec).
- Reachability and port checks run from the `netshoot` pod in the `5g`
  namespace (ping, nc, and nmap for SCTP/UDP), which is attached to the
  overlays for testing.

A running `netshoot` pod in `5g` is therefore required for the reachability
and port-listening checks; without it those checks report an error.

## Disabled Suites

### Performance Suite
**Requires:**
- `iperf3` installed in Open5GS containers
- Adequate cluster resources for load testing

**To enable:** Edit `test_config.yaml`:
```yaml
suites:
  performance:
    enabled: true
```

### Resilience Suite
**Requires:**
- Stable cluster with adequate resources
- E2E and Protocols tests passing consistently
- May need increased timeouts for KubeEdge edge nodes

**To enable:** Edit `test_config.yaml`:
```yaml
suites:
  resilience:
    enabled: true
```

## Configuration

Edit `test_config.yaml` to customize test behavior:

```yaml
suites:
  e2e:
    enabled: true     # Enable/disable suite
  protocols:
    enabled: true
  performance:
    enabled: false    # Disabled by default
  resilience:
    enabled: false    # Disabled by default
```

## Using with Makefile

```bash
# Run all tests
make test

# Run specific suite
make e2e
make protocols
make performance
make resilience
make ran

# Clean test artifacts
make clean
```

## Troubleshooting

### SSL Certificate Error
The kubeconfig is auto-updated on each run. If you still see SSL errors:
```bash
# Force kubeconfig refresh
rm tests/kubeconfig
python3 tests/run_tests.py
```

### Module Not Found
Ensure you're running from the tests directory or using the main runner:
```bash
cd tests
python3 run_tests.py
```

### VMs Not Running
```bash
cd /path/to/project
vagrant up
```

## Writing New Tests

1. Create test file in appropriate directory
2. Import utilities:
   ```python
   from utils.k8s_client import K8sClient
   from utils.test_helpers import TestConfig, TestLogger
   ```
3. Add suite to `run_tests.py` if needed
