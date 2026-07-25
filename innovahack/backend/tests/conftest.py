from __future__ import annotations

import os

os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_PATH", ":memory:")
