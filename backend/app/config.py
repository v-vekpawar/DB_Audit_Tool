"""Tunables for the DB Normalization Checker.

All defaults are starting values from docs/README.md.
Thresholds, ETA_* constants and the score formula are tuned
against the test databases (docs/TEST_PLAN.md, build step 8).
"""

from pathlib import Path

SERVER_PORT = 8770

# FD search
# FD discovery. TANE is the production search; brute force is the reference
# engine used for small tables and parity tests.
MAX_LHS_SIZE = 6
MAX_LHS_SIZE_LARGE = 8
BRUTE_FORCE_MAX_COLUMNS = 10
RUN_BRUTE_FORCE_PARITY = True

LARGE_TABLE_ROWS = 1000

# Loading
MAX_ROWS_PER_TABLE = 50000
MIN_ROWS_FOR_DATA_FDS = 10
MIN_ROWS_FOR_INFERRED_KEYS = 10
MAX_INFERRED_KEY_COMBINATIONS = 200000

# Scoring tiers
MIN_REPEATED_ROWS = 5
STRONG_CONFIDENCE = 75
STRONG_CONFIDENCE_CROSS_TABLE = 60
SUSPECT_CONFIDENCE = 50
STRONG_MIN_REPEATED_ROWS = 20
STRONG_MIN_REPEATED_COVERAGE = 0.01

# Near-FDs (dirty data)
NEAR_FD_MAX_ERROR = 0.03
NEAR_FD_MIN_GROUP = 3
NEAR_FD_SCORE_CAP = 90

# Sampling
SAMPLED_SCORE_CAP = 85

# Cross-table
CROSS_TABLE_MATCH_MIN = 0.97

# Estimator (validated in build step 8: first estimate 15.1s rough vs
# 13.4s/12.0s actual on the planted suites — within factor ~1.3)
ETA_DEFAULT_SECONDS_PER_UNIT = 2e-07
ETA_PRIOR_UNITS = 100000
ETA_TICK_SECONDS = 5

# SSE stream
SSE_HEARTBEAT_SECONDS = 15
STREAM_TIMEOUT_SECONDS = 45

# Queries
QUERY_TIMEOUT_SECONDS = 30

# Logging (absolute path: independent of the working directory)
LOG_LEVEL = "INFO"
LOG_FILE = str(Path(__file__).resolve().parent.parent / "logs" / "dbnorm.log")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# Default ports / schema (convenience only)
DEFAULT_PORTS = {"mysql": 3306, "postgresql": 5432, "oracle": 1521, "mssql": 1433}
DEFAULT_POSTGRES_SCHEMA = "public"

# Last-scan store (absolute path, next to the backend)
LAST_SCAN_DB = str(Path(__file__).resolve().parent.parent / "last_scan.sqlite")
