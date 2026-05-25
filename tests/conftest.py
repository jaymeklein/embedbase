import sys
import os

# Add the repo root to sys.path so `api` and `worker` are importable in tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars for tests that import settings
os.environ.setdefault("MASTER_API_KEY", "test-master-key-for-testing-only")
os.environ.setdefault("DATABASE_PATH", ":memory:")
