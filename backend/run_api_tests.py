#!/usr/bin/env python3
"""
Real API Test Runner
Runs comprehensive API tests against real backend with real authentication
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


def check_environment():
    """Check that required environment variables are set"""
    required_vars = [
        "TEST_API_BASE_URL",
        "TEST_KEYCLOAK_URL",
        "TEST_KEYCLOAK_REALM",
        "TEST_USER_USERNAME",
        "TEST_USER_PASSWORD",
        "TEST_ADMIN_USERNAME",
        "TEST_ADMIN_PASSWORD"
    ]

    # Load test.env if it exists
    test_env_path = Path(__file__).parent / "tests" / "test.env"
    if test_env_path.exists():
        print(f"Loading environment from: {test_env_path}")
        from dotenv import load_dotenv
        load_dotenv(test_env_path)

    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nPlease set these variables or update tests/test.env")
        return False

    print("✅ Environment configuration OK")
    print(f"   API URL: {os.getenv('TEST_API_BASE_URL')}")
    print(f"   Keycloak: {os.getenv('TEST_KEYCLOAK_URL')}")
    print(f"   Realm: {os.getenv('TEST_KEYCLOAK_REALM')}")
    return True


def run_tests(test_filter=None, verbose=False, stop_on_first_failure=False):
    """Run the API tests"""
    if not check_environment():
        return 1

    # Build pytest command
    cmd = ["python", "-m", "pytest", "tests/"]

    if verbose:
        cmd.append("-v")

    if stop_on_first_failure:
        cmd.append("-x")

    if test_filter:
        cmd.extend(["-k", test_filter])

    # Add markers for specific test types
    cmd.extend(["--tb=short"])

    print(f"\n🚀 Running API tests...")
    print(f"Command: {' '.join(cmd)}")
    print("-" * 60)

    try:
        result = subprocess.run(cmd, cwd=Path(__file__).parent)
        return result.returncode
    except KeyboardInterrupt:
        print("\n⚠️ Tests interrupted by user")
        return 1
    except Exception as e:
        print(f"❌ Error running tests: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Run real API tests")
    parser.add_argument(
        "--filter", "-k",
        help="Filter tests by name/marker (e.g., 'auth' or 'redirect')"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--stop-on-failure", "-x",
        action="store_true",
        help="Stop on first failure"
    )
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Run only authentication tests"
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Run only public API tests"
    )
    parser.add_argument(
        "--redirect-only",
        action="store_true",
        help="Run only redirect detection tests"
    )

    args = parser.parse_args()

    # Set filter based on shortcut arguments
    test_filter = args.filter
    if args.auth_only:
        test_filter = "auth"
    elif args.public_only:
        test_filter = "public"
    elif args.redirect_only:
        test_filter = "redirect"

    exit_code = run_tests(
        test_filter=test_filter,
        verbose=args.verbose,
        stop_on_first_failure=args.stop_on_failure
    )

    if exit_code == 0:
        print("\n✅ All tests passed!")
    else:
        print(f"\n❌ Tests failed with exit code {exit_code}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()