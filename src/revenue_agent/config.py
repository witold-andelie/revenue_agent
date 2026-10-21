from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE entries without adding a dotenv dependency."""
    file_path = Path(path)
    if not file_path.exists():
        return
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def opencode_go_config() -> dict[str, str | float]:
    load_dotenv()
    api_key = os.getenv("OPENCODE_GO_API_KEY", "").strip()
    base_url = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1/chat/completions").strip()
    model = os.getenv("OPENCODE_GO_MODEL", "deepseek-v4-flash").strip()
    if not api_key:
        raise RuntimeError("OPENCODE_GO_API_KEY is not configured")
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        # The shared gateway routinely needs 1-4 minutes for long research
        # generations; 60s produced spurious read timeouts in live cycles.
        "timeout": float(os.getenv("OPENCODE_GO_TIMEOUT_SECONDS", "240")),
    }
