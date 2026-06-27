"""
Mock fixture data for CI Diagnostics Agent.

Three builds, three distinct failure stories:
  build-1042 : stripe-sdk version bump broke payment gateway tests
  build-1057 : environment variable missing in production config
  build-1073 : database migration conflict causing service crash

Each tool has a healthy response and one or more failure variants.
Failure injection is controlled in tools.py — not here.
"""

# ---------------------------------------------------------------------------
# BUILD-1042 : Dependency bump broke tests
# ---------------------------------------------------------------------------

BUILD_1042_LOGS_HEALTHY = """
[2024-01-15 14:23:01] Step 1/6: Pulling base image python:3.11-slim ... ✓
[2024-01-15 14:23:08] Step 2/6: Installing dependencies ... ✓
[2024-01-15 14:23:45] Step 3/6: Running lint checks ... ✓
[2024-01-15 14:24:01] Step 4/6: Running test suite ... FAILED
[2024-01-15 14:24:01]   × tests/test_checkout_flow.py::test_payment_gateway_timeout
[2024-01-15 14:24:01]   × tests/test_checkout_flow.py::test_retry_on_503
[2024-01-15 14:24:01]   × tests/test_checkout_flow.py::test_idempotency_key_conflict
[2024-01-15 14:24:01] Step 5/6: Build artifact ... SKIPPED
[2024-01-15 14:24:01] Step 6/6: Push to registry ... SKIPPED
[2024-01-15 14:24:02] Exit code: 1. Duration: 61s
""".strip()

BUILD_1042_LOGS_TRUNCATED = """
[2024-01-15 14:23:01] Step 1/6: Pulling base image python:3.11-slim ... ✓
[2024-01-15 14:23:08] Step 2/6: Installing dep
ERROR: log stream disconnected at byte 2891. Partial data only.
""".strip()

BUILD_1042_TEST_RESULTS_HEALTHY = {
    "build_id": "build-1042",
    "timestamp": "2024-01-15T14:24:01Z",
    "summary": {"total": 47, "passed": 44, "failed": 3, "skipped": 0},
    "failures": [
        {
            "test": "test_payment_gateway_timeout",
            "file": "tests/test_checkout_flow.py",
            "error": (
                "AssertionError: expected HTTPError after 5s, got response 200 — "
                "stripe-sdk retry intercepted the timeout"
            ),
        },
        {
            "test": "test_retry_on_503",
            "file": "tests/test_checkout_flow.py",
            "error": (
                "AssertionError: expected 2 retry attempts, got 0 — "
                "stripe-sdk 4.2.0 changed default retry behaviour"
            ),
        },
        {
            "test": "test_idempotency_key_conflict",
            "file": "tests/test_checkout_flow.py",
            "error": (
                "stripe.error.IdempotencyError: stripe-sdk 4.2.0 enforces "
                "stricter idempotency key validation"
            ),
        },
    ],
}

BUILD_1042_TEST_RESULTS_TIMEOUT = "connection timeout after 30s — test reporting service unavailable"

BUILD_1042_TEST_RESULTS_EMPTY = {}

BUILD_1042_COMMITS = [
    {
        "sha": "a3f9c12",
        "message": "bump: stripe-sdk 4.1.2 → 4.2.0",
        "author": "jane.doe",
        "timestamp": "2024-01-15T12:01:33Z",
        "files_changed": ["requirements.txt", "requirements-lock.txt"],
    },
    {
        "sha": "b71de44",
        "message": "fix: update retry config for webhook handler",
        "author": "jane.doe",
        "timestamp": "2024-01-15T11:45:12Z",
        "files_changed": ["src/payments/webhook.py"],
    },
    {
        "sha": "c90fa21",
        "message": "chore: update CI base image to python:3.11-slim",
        "author": "dev.ops",
        "timestamp": "2024-01-14T09:30:00Z",
        "files_changed": [".github/workflows/ci.yml"],
    },
]

# ---------------------------------------------------------------------------
# BUILD-1057 : Missing environment variable in production config
# ---------------------------------------------------------------------------

BUILD_1057_LOGS_HEALTHY = """
[2024-01-18 09:11:02] Step 1/6: Pulling base image python:3.11-slim ... ✓
[2024-01-18 09:11:09] Step 2/6: Installing dependencies ... ✓
[2024-01-18 09:11:52] Step 3/6: Running lint checks ... ✓
[2024-01-18 09:12:10] Step 4/6: Running test suite ... ✓ (47/47 passed)
[2024-01-18 09:13:01] Step 5/6: Build artifact ... ✓
[2024-01-18 09:13:45] Step 6/6: Deploying to production ... FAILED
[2024-01-18 09:13:45]   RuntimeError: Missing required environment variable: PAYMENT_ENCRYPTION_KEY
[2024-01-18 09:13:45]   Service failed health check after 60s. Rolling back.
[2024-01-18 09:14:50] Exit code: 1. Duration: 228s
""".strip()

BUILD_1057_LOGS_TRUNCATED = """
[2024-01-18 09:11:02] Step 1/6: Pulling base image python:3.11-slim ... ✓
[2024-01-18 09:11:09] Step 2/6: Installing dep
ERROR: log stream disconnected at byte 1204. Partial data only.
""".strip()

BUILD_1057_TEST_RESULTS_HEALTHY = {
    "build_id": "build-1057",
    "timestamp": "2024-01-18T09:12:10Z",
    "summary": {"total": 47, "passed": 47, "failed": 0, "skipped": 0},
    "failures": [],
}

BUILD_1057_TEST_RESULTS_TIMEOUT = "connection timeout after 30s — test reporting service unavailable"

BUILD_1057_COMMITS = [
    {
        "sha": "d84bc31",
        "message": "feat: add PAYMENT_ENCRYPTION_KEY to local .env.example",
        "author": "sara.smith",
        "timestamp": "2024-01-18T07:30:00Z",
        "files_changed": [".env.example", "src/payments/encryption.py"],
    },
    {
        "sha": "e92ac44",
        "message": "refactor: move encryption key loading to startup config",
        "author": "sara.smith",
        "timestamp": "2024-01-18T07:15:00Z",
        "files_changed": ["src/config.py"],
    },
    {
        "sha": "f01cd55",
        "message": "chore: rotate staging API keys",
        "author": "dev.ops",
        "timestamp": "2024-01-17T16:00:00Z",
        "files_changed": [".github/secrets.yml"],
    },
]

# ---------------------------------------------------------------------------
# BUILD-1073 : Database migration conflict causing service crash on startup
# ---------------------------------------------------------------------------

BUILD_1073_LOGS_HEALTHY = """
[2024-01-22 16:45:00] Step 1/6: Pulling base image python:3.11-slim ... ✓
[2024-01-22 16:45:07] Step 2/6: Installing dependencies ... ✓
[2024-01-22 16:45:50] Step 3/6: Running lint checks ... ✓
[2024-01-22 16:46:05] Step 4/6: Running test suite ... ✓ (47/47 passed)
[2024-01-22 16:47:00] Step 5/6: Build artifact ... ✓
[2024-01-22 16:47:45] Step 6/6: Deploying to production ... FAILED
[2024-01-22 16:47:45]   alembic.util.CommandError: Can't locate revision 'a1b2c3d4'
[2024-01-22 16:47:45]   Multiple heads detected in migration chain: [a1b2c3d4, x9y8z7w6]
[2024-01-22 16:47:46]   Service crashed on startup. DB state unknown. Rolling back.
[2024-01-22 16:48:55] Exit code: 1. Duration: 235s
""".strip()

BUILD_1073_LOGS_TRUNCATED = """
[2024-01-22 16:45:00] Step 1/6: Pulling base image python:3.11-slim ... ✓
[2024-01-22 16:45:07] Step 2/6: Installing dep
ERROR: log stream disconnected at byte 3344. Partial data only.
""".strip()

BUILD_1073_TEST_RESULTS_HEALTHY = {
    "build_id": "build-1073",
    "timestamp": "2024-01-22T16:46:05Z",
    "summary": {"total": 47, "passed": 47, "failed": 0, "skipped": 0},
    "failures": [],
}

BUILD_1073_TEST_RESULTS_EMPTY = {}

BUILD_1073_COMMITS = [
    {
        "sha": "g12ef67",
        "message": "feat: add migration for new orders_v2 table (branch: feature/orders-redesign)",
        "author": "mike.jones",
        "timestamp": "2024-01-22T14:20:00Z",
        "files_changed": ["migrations/versions/a1b2c3d4_orders_v2.py"],
    },
    {
        "sha": "h23fg78",
        "message": "feat: add migration for payments audit log (branch: feature/audit-log)",
        "author": "sara.smith",
        "timestamp": "2024-01-22T14:05:00Z",
        "files_changed": ["migrations/versions/x9y8z7w6_payments_audit.py"],
    },
    {
        "sha": "i34gh89",
        "message": "chore: merge feature/orders-redesign into main",
        "author": "tech.lead",
        "timestamp": "2024-01-22T15:00:00Z",
        "files_changed": ["migrations/versions/a1b2c3d4_orders_v2.py", "src/models/order.py"],
    },
]

# ---------------------------------------------------------------------------
# Registry: maps build_id → scenario data
# ---------------------------------------------------------------------------

BUILDS = {
    "build-1042": {
        "repo": "payments-service",
        "description": "stripe-sdk version bump broke payment gateway tests",
        "logs_healthy": BUILD_1042_LOGS_HEALTHY,
        "logs_truncated": BUILD_1042_LOGS_TRUNCATED,
        "test_results_healthy": BUILD_1042_TEST_RESULTS_HEALTHY,
        "test_results_failures": [
            BUILD_1042_TEST_RESULTS_TIMEOUT,
            BUILD_1042_TEST_RESULTS_EMPTY,
        ],
        "commits": BUILD_1042_COMMITS,
        # Which tool is flaky for this build and at what probability
        "flaky_tool": "get_test_results",
        "flaky_probability": 0.6,
    },
    "build-1057": {
        "repo": "payments-service",
        "description": "missing environment variable caused production deployment failure",
        "logs_healthy": BUILD_1057_LOGS_HEALTHY,
        "logs_truncated": BUILD_1057_LOGS_TRUNCATED,
        "test_results_healthy": BUILD_1057_TEST_RESULTS_HEALTHY,
        "test_results_failures": [BUILD_1057_TEST_RESULTS_TIMEOUT],
        "commits": BUILD_1057_COMMITS,
        # Build logs are flaky for this build
        "flaky_tool": "get_build_logs",
        "flaky_probability": 0.6,
    },
    "build-1073": {
        "repo": "payments-service",
        "description": "database migration conflict caused service crash on startup",
        "logs_healthy": BUILD_1073_LOGS_HEALTHY,
        "logs_truncated": BUILD_1073_LOGS_TRUNCATED,
        "test_results_healthy": BUILD_1073_TEST_RESULTS_HEALTHY,
        "test_results_failures": [BUILD_1073_TEST_RESULTS_EMPTY],
        "commits": BUILD_1073_COMMITS,
        # Test results are flaky for this build
        "flaky_tool": "get_test_results",
        "flaky_probability": 0.6,
    },
}

QUESTIONS = {
    "build-1042": "Build #1042 failed in the payments-service pipeline. What went wrong and what should the team do next?",
    "build-1057": "Build #1057 passed all tests but failed to deploy. What is the root cause?",
    "build-1073": "Build #1073 failed during deployment despite all tests passing. What happened and is the database at risk?",
}
