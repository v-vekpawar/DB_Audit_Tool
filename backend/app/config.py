"""Tunables for the DB Normalization Checker.

All defaults are starting values from docs/README.md.
Thresholds, ETA_* constants and the score formula are tuned
against the test databases (docs/TEST_PLAN.md, build step 8).
"""

SERVER_PORT = 8770

# FD search
MAX_LHS_SIZE = 2
MAX_LHS_SIZE_LARGE = 3
LARGE_TABLE_ROWS = 1000

# Loading
MAX_ROWS_PER_TABLE = 50000
MIN_ROWS_FOR_DATA_FDS = 10

# Scoring tiers
MIN_REPEATED_ROWS = 5
STRONG_CONFIDENCE = 75
STRONG_CONFIDENCE_CROSS_TABLE = 60
SUSPECT_CONFIDENCE = 50

# Near-FDs (dirty data)
NEAR_FD_MAX_ERROR = 0.03
NEAR_FD_MIN_GROUP = 3
NEAR_FD_SCORE_CAP = 90

# Sampling
SAMPLED_SCORE_CAP = 85

# Cross-table
CROSS_TABLE_MATCH_MIN = 0.97

# Estimator (TODO: tune in build step 8 against measured runs)
ETA_DEFAULT_SECONDS_PER_UNIT = 5e-06
ETA_PRIOR_UNITS = 100000
ETA_TICK_SECONDS = 5

# SSE stream
SSE_HEARTBEAT_SECONDS = 15
STREAM_TIMEOUT_SECONDS = 45

# Queries
QUERY_TIMEOUT_SECONDS = 30

# Logging
LOG_LEVEL = "INFO"
LOG_FILE = "backend/logs/dbnorm.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# Default ports / schema (convenience only)
DEFAULT_PORTS = {"mysql": 3306, "postgresql": 5432, "oracle": 1521}
DEFAULT_POSTGRES_SCHEMA = "public"
