# 5G K3s KubeEdge Testbed - Test Suite
# Thin wrapper around run_tests.py. The interactive picker lives in the single
# project CLI: ./testbed-config tests
.DEFAULT_GOAL := help
.PHONY: help test e2e protocols iam camara performance resilience ran verbose list clean clean-all

VENVDIR := venv

# Pass extra args to the runner. `make iam V=1` or `make iam ARGS="-v"` for
# verbose (make consumes a bare `-v`, so it cannot be forwarded directly).
ARGS ?=
ifdef V
ARGS += -v
endif

test:
	@python3 run_tests.py $(ARGS)

e2e:
	@python3 run_tests.py -s e2e $(ARGS)

protocols:
	@python3 run_tests.py -s protocols $(ARGS)

iam:
	@python3 run_tests.py -s iam $(ARGS)

camara:
	@python3 run_tests.py -s camara $(ARGS)

performance:
	@python3 run_tests.py -s performance $(ARGS)

resilience:
	@python3 run_tests.py -s resilience $(ARGS)

ran:
	@python3 run_tests.py -s ran $(ARGS)

verbose:
	@python3 run_tests.py -v

list:
	@python3 run_tests.py --list

clean:
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.log" -delete 2>/dev/null || true
	@find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true

clean-all: clean
	@rm -rf $(VENVDIR)/

help:
	@echo "5G K3s KubeEdge Testbed - Test Suite"
	@echo "====================================="
	@echo ""
	@echo "Interactive picker (single project CLI):"
	@echo "  ../testbed-config tests   - Pick and run suites via the menu"
	@echo ""
	@echo "Quick start:"
	@echo "  make test         - Run all enabled suites"
	@echo ""
	@echo "Individual suites:"
	@echo "  make e2e          - End-to-end deployment tests"
	@echo "  make protocols    - 5G protocol tests (NGAP, PFCP, GTP-U)"
	@echo "  make iam          - Keycloak realm + token validation (phase 08)"
	@echo "  make camara       - CAMARA gateway + positioning + org isolation (phase 10)"
	@echo "  make performance  - Performance benchmarks"
	@echo "  make resilience   - Failure recovery tests"
	@echo "  make ran          - Physical RAN integration tests"
	@echo ""
	@echo "Other:"
	@echo "  make <suite> V=1  - Run a suite with verbose output (e.g. make iam V=1)"
	@echo "  make verbose      - Run all tests with verbose output"
	@echo "  make list         - List available tests"
	@echo "  make clean        - Remove caches/logs"
	@echo "  make clean-all    - Remove venv too"
