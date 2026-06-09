# Testing Guide

The testbed includes a comprehensive test suite for validation and continuous integration.

## Test Suite Overview

| Suite | Description | Duration |
|-------|-------------|----------|
| **e2e** | End-to-end integration tests | ~5 min |
| **protocols** | 5G protocol validation (PFCP, NGAP, GTP-U) | ~3 min |
| **iam** | Keycloak realm: clients, roles, token claims (phase 08) | ~2 min |
| **performance** | Throughput, latency, stress tests | ~10 min |
| **resilience** | Failure recovery tests | ~8 min |
| **ran** | Physical RAN integration tests | ~2 min |

## Quick Start

```bash
cd tests

# Interactive picker
../testbed-config tests

# Run all enabled suites
make test

# Run a specific suite
make e2e
make protocols
make iam
make ran

# List available tests
make list
```

## Prerequisites

- Vagrant VMs running (`vagrant up`)
- 5G Core deployed (at minimum)
- Python 3.8+

The test runner automatically:
- Checks VM status
- Creates virtual environment
- Installs dependencies
- Fetches kubeconfig from master

## Test Suites

### E2E Tests

Validates complete system integration:

```bash
make e2e
```

**Tests**:
- Infrastructure connectivity
- Kubernetes cluster health
- KubeEdge integration
- Overlay network setup
- 5G Core deployment
- Network interfaces (N1-N6)
- Protocol connectivity
- UERANSIM deployment
- RAN mode primitives (dashboard-compatible gNB/UE labels)
- RAN overlay labeling (baseline Ansible vs runtime dashboard)
- Edge placement semantics (gNB/UE scheduled on edge)
- End-to-end connectivity

### Protocol Tests

Validates 5G protocol implementations:

```bash
make protocols
```

**Tests**:
- PFCP (N4): SMF-UPF control plane
- NGAP (N2): gNB-AMF signaling
- GTP-U (N3): User plane tunnels
- Port listening verification
- Message exchange validation

### IAM Tests

Validates the Keycloak realm provisioned in phase 08:

```bash
make iam
```

**Tests**:
- Keycloak and PostgreSQL pods Running in the `iam` namespace
- OIDC discovery document reachable and well-formed
- Master-realm admin login with the configured password
- Expected OIDC clients present in the realm
- Expected realm roles present
- CAMARA gateway service account token carries `camara-location-read`

### Performance Tests

Measures system performance:

```bash
make performance
```

**Tests**:
- VXLAN throughput (iperf3)
- Latency measurements
- Packet loss under load
- Concurrent connections
- Resource usage (CPU, memory)

### Resilience Tests

Validates failure recovery:

```bash
make resilience
```

**Tests**:
- Pod restart recovery
- Network interface recovery
- Node failure simulation
- OVS bridge recreation
- KubeEdge recovery

### Physical RAN Tests

Validates physical RAN integration:

```bash
make ran
```

**Tests**:
- OVS bridge configuration
- RAN interface detection
- br-ran bridge existence
- Patch port configuration
- AMF/UPF overlay reachability
- gNB connection status

## Running Tests

### Command Line

```bash
# Specific suite
python3 run_tests.py -s e2e

# With verbose output
python3 run_tests.py -s e2e -v

# Specific phases
python3 run_tests.py -p infrastructure,5g-core
```

### Makefile Targets

```bash
make test         # Run all enabled suites
make e2e          # End-to-end tests
make protocols    # Protocol tests
make iam          # Keycloak realm / token tests (phase 08)
make performance  # Performance tests
make resilience   # Resilience tests
make ran          # Physical RAN tests
make verbose      # All tests with verbose output
make list         # List available tests
make clean        # Remove caches
make clean-all    # Remove venv too
```

## Configuration

### test_config.yaml

```yaml
global:
  verbose: false
  timeout: 300
  retry_attempts: 3

suites:
  e2e:
    enabled: true
    timeout: 1800
  
  protocols:
    enabled: true
    timeout: 600

performance:
  throughput:
    min_mbps: 10
    target_mbps: 100
  latency:
    max_ms: 50
```

### Environment Variables

```bash
export VERBOSE=true
export TEST_TIMEOUT=600
export TEST_DURATION=120
```

## Writing New Tests

### Test Structure

```python
# tests/my_suite/test_feature.py
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.k8s_client import K8sClient
from utils.test_helpers import TestConfig, TestLogger

class MyTestSuite:
    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        self.kubectl = K8sClient(self.config.get("cluster.kubeconfig_path"))
    
    def test_my_feature(self) -> bool:
        """Test description"""
        self.logger.info("Testing feature...")
        
        try:
            # Test implementation
            result = self.kubectl.get_pods("5g")
            
            if len(result) > 0:
                self.logger.success("Feature test passed")
                return True
            else:
                self.logger.error("Feature test failed")
                return False
        except Exception as e:
            self.logger.error(f"Test failed: {e}")
            return False
```

### Register New Suite

1. Add to `run_tests.py`:
```python
suite_modules = {
    "my_suite": "my_suite.test_feature",
    # ...
}
```

2. Add to `Makefile`:
```makefile
my_suite:
	@python3 run_tests.py -s my_suite
```

## CI/CD Integration

### GitHub Actions

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup testbed
        run: vagrant up
      
      - name: Run tests
        run: |
          cd tests
          make e2e
```

## Troubleshooting Tests

### SSL Certificate Errors

```bash
# Refresh kubeconfig
vagrant ssh master -c "cat /home/vagrant/kubeconfig" > tests/kubeconfig
```

### Test Timeout

```bash
# Increase timeout
export TEST_TIMEOUT=1200
python3 run_tests.py -s performance
```

### Missing Dependencies

```bash
# Reinstall venv
make clean-all
make
```

## Related Documentation

- [Getting Started](../getting-started.md) - Deployment guide
- [Troubleshooting](../operations/troubleshooting.md) - Debug procedures
- [Contributing](contributing.md) - Development guidelines
