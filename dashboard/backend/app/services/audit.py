import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def write_audit(event: str, details: dict[str, Any]) -> None:
    settings.ensure_audit_dir()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "details": details,
    }
    path = Path(settings.audit_log_path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
