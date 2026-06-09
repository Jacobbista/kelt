#!/usr/bin/env python3
"""
Main test runner for 5G K3s KubeEdge Testbed

This script automatically:
1. Sets up the virtual environment
2. Updates kubeconfig from master VM
3. Runs the requested test suites
"""
import sys
import os
import argparse
import subprocess
from pathlib import Path


# Script directory
SCRIPT_DIR = Path(__file__).parent.resolve()
VENV_DIR = SCRIPT_DIR / "venv"
KUBECONFIG_PATH = SCRIPT_DIR / "kubeconfig"
REQUIREMENTS_PATH = SCRIPT_DIR / "requirements.txt"


def is_in_venv():
    """Check if we're running inside the virtual environment"""
    return sys.prefix == str(VENV_DIR) or sys.executable.startswith(str(VENV_DIR))


def get_venv_python():
    """Get path to venv's python"""
    if os.name == 'nt':
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv():
    """Ensure virtual environment exists and has dependencies"""
    venv_python = get_venv_python()
    
    if not VENV_DIR.exists():
        print("🔧 Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        print("✅ Virtual environment created")
    
    # Install dependencies if needed
    if REQUIREMENTS_PATH.exists():
        print("🔍 Checking dependencies...")
        try:
            result = subprocess.run(
                [str(venv_python), "-c", "import kubernetes, yaml, requests"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                raise Exception("Dependencies not installed")
            print("✅ Dependencies ready")
        except Exception:
            print("📦 Installing dependencies...")
            pip_path = VENV_DIR / ("Scripts" if os.name == 'nt' else "bin") / "pip"
            subprocess.run([str(pip_path), "install", "-q", "-r", str(REQUIREMENTS_PATH)], check=True)
            print("✅ Dependencies installed")
    
    return str(venv_python)


def check_vagrant_vms():
    """Check if Vagrant VMs are running"""
    try:
        # Need to be in project root for vagrant status
        project_root = SCRIPT_DIR.parent
        result = subprocess.run(
            ["vagrant", "status"], 
            capture_output=True, text=True, timeout=30,
            cwd=str(project_root)
        )
        if result.returncode != 0:
            print("❌ Vagrant not available")
            return False
        
        status = result.stdout.lower()
        for vm in ["master", "worker", "edge"]:
            if f"{vm}" not in status or "running" not in status:
                print(f"❌ VM '{vm}' is not running")
                return False
        
        print("✅ All required Vagrant VMs are running")
        return True
        
    except Exception as e:
        print(f"❌ Failed to check Vagrant VMs: {e}")
        return False


def update_kubeconfig():
    """Fetch kubeconfig from master VM"""
    print("🔄 Updating kubeconfig from master VM...")
    try:
        project_root = SCRIPT_DIR.parent
        result = subprocess.run(
            ["vagrant", "ssh", "master", "-c", "sudo cat /etc/rancher/k3s/k3s.yaml"],
            capture_output=True, text=True, timeout=30,
            cwd=str(project_root)
        )
        
        if result.returncode != 0:
            print(f"⚠️  Could not fetch kubeconfig")
            return KUBECONFIG_PATH.exists()
        
        # `vagrant ssh` can prepend a banner to stdout; drop anything before
        # the YAML document so the kubeconfig parses.
        content = result.stdout
        idx = content.find("apiVersion")
        if idx > 0:
            content = content[idx:]
        # Replace localhost with master IP
        content = content.replace("127.0.0.1", "192.168.56.10")

        with open(KUBECONFIG_PATH, "w") as f:
            f.write(content)
        
        os.chmod(KUBECONFIG_PATH, 0o600)
        print("✅ Kubeconfig updated")
        return True
        
    except Exception as e:
        print(f"⚠️  Could not update kubeconfig: {e}")
        return KUBECONFIG_PATH.exists()


def run_in_venv():
    """Re-execute this script inside the venv"""
    venv_python = ensure_venv()
    
    # Re-run this script with venv python
    env = os.environ.copy()
    env["KUBECONFIG"] = str(KUBECONFIG_PATH)
    env["_IN_VENV"] = "1"  # Flag to prevent infinite recursion
    
    result = subprocess.run(
        [venv_python] + sys.argv,
        env=env
    )
    sys.exit(result.returncode)


def main():
    """Main entry point"""
    # If not in venv and not flagged as re-entry, bootstrap
    if not is_in_venv() and os.environ.get("_IN_VENV") != "1":
        print("🚀 Starting 5G K3s KubeEdge Testbed Test Suite")
        print("=" * 50)
        
        # Check VMs
        if not check_vagrant_vms():
            print("\n💡 Please start the testbed with: vagrant up")
            sys.exit(1)
        
        # Update kubeconfig
        if not update_kubeconfig():
            if not KUBECONFIG_PATH.exists():
                print("❌ No kubeconfig available")
                sys.exit(1)
        
        # Re-run in venv
        run_in_venv()
        return
    
    # === Running inside venv now ===
    
    # Set kubeconfig env
    os.environ["KUBECONFIG"] = str(KUBECONFIG_PATH)
    
    # Now safe to import heavy modules
    sys.path.insert(0, str(SCRIPT_DIR))
    from utils.test_helpers import TestConfig, TestLogger
    
    parser = argparse.ArgumentParser(description="5G K3s KubeEdge Testbed Test Runner")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-s", "--suite",
                       choices=["e2e", "protocols", "performance", "resilience", "ran", "iam"],
                       help="Run specific test suite")
    parser.add_argument("-p", "--phases", nargs="+",
                       choices=["infrastructure", "5g-core", "ueransim", "e2e", "performance", "resilience"],
                       help="Run specific test phases")
    parser.add_argument("--list", action="store_true", help="List available tests")
    
    args = parser.parse_args()
    
    print("✅ Environment ready, starting tests...")
    print("=" * 50)
    
    logger = TestLogger(args.verbose)
    config = TestConfig()
    
    if args.list:
        print("\nAvailable test suites:")
        print("  e2e         - End-to-end integration tests")
        print("  protocols   - 5G protocol tests (PFCP, NGAP, GTP-U)")
        print("  performance - Performance and stress tests")
        print("  resilience  - Failure recovery tests")
        print("  ran         - Physical RAN integration tests")
        print("  iam         - Keycloak realm + token validation (phase 08)")
        print("\nRun with: make <suite>  or  python run_tests.py -s <suite>")
        return
    
    # Suite mapping
    suite_modules = {
        "e2e": "core.test_e2e",
        "protocols": "protocols.test_5g_protocols",
        "performance": "performance.test_performance",
        "resilience": "resilience.test_resilience",
        "ran": "ran.test_physical_ran",
        "iam": "iam.test_iam"
    }
    
    def run_suite(suite_name: str, force: bool = False) -> bool:
        """Run a single test suite"""
        if suite_name not in suite_modules:
            logger.error(f"Unknown suite: {suite_name}")
            return False
        
        # Check if suite is enabled (skip check if forced via -s flag)
        if not force and not config.get(f"suites.{suite_name}.enabled", True):
            logger.warning(f"Skipping {suite_name} (disabled in config)")
            return True  # Don't count as failure
        
        module_path = suite_modules[suite_name]
        script_path = SCRIPT_DIR / (module_path.replace(".", "/") + ".py")
        
        if not script_path.exists():
            logger.error(f"Test script not found: {script_path}")
            return False
        
        cmd = [sys.executable, str(script_path)]
        if args.verbose:
            cmd.append("-v")
        
        result = subprocess.run(cmd)
        return result.returncode == 0
    
    # Determine what to run
    force_run = False  # Force run ignores enabled flag
    if args.suite:
        suites = [args.suite]
        force_run = True  # Explicit -s flag forces the suite to run
    elif args.phases:
        phase_map = {
            "infrastructure": ["e2e"],
            "5g-core": ["e2e", "protocols"],
            "ueransim": ["e2e"],
            "e2e": ["e2e", "protocols"],
            "performance": ["performance"],
            "resilience": ["resilience"]
        }
        suites = []
        for phase in args.phases:
            suites.extend(phase_map.get(phase, []))
        suites = list(dict.fromkeys(suites))  # Unique, preserve order
    else:
        # Default: only run enabled suites
        suites = ["e2e", "protocols", "iam", "performance", "resilience"]
    
    # Run suites
    results = {}  # suite -> (success, skipped)
    for suite in suites:
        # Check if enabled (unless forced)
        if not force_run and not config.get(f"suites.{suite}.enabled", True):
            logger.warning(f"⏭️  Skipping {suite.upper()} (disabled in test_config.yaml)")
            results[suite] = (True, True)  # skipped
            continue
        
        success = run_suite(suite, force=force_run)
        results[suite] = (success, False)  # not skipped
        
        if success:
            logger.success(f"{suite.upper()} tests passed")
        else:
            logger.error(f"{suite.upper()} tests failed")
    
    # Summary
    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)
    
    passed = sum(1 for (success, skipped) in results.values() if success and not skipped)
    skipped = sum(1 for (_, skip) in results.values() if skip)
    failed = sum(1 for (success, skip) in results.values() if not success and not skip)
    total = len(results) - skipped
    
    for suite, (success, skip) in results.items():
        if skip:
            status = "⏭️  SKIPPED"
        elif success:
            status = "✅ PASSED"
        else:
            status = "❌ FAILED"
        print(f"  {suite.upper():12} {status}")
    
    if skipped:
        print(f"\nSkipped: {skipped} (disabled in config)")
    print(f"Results: {passed}/{total} test suites passed")
    
    if failed == 0:
        print("🎉 All enabled test suites passed!")
        sys.exit(0)
    else:
        print("💥 Some test suites failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
