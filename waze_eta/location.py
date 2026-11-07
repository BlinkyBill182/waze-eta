"""Where the owner is now: the latest report from each device, kept in a small JSON file."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path


class Locations:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.by_source: dict[str, dict] = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            self.by_source = {}

    def report(self, source: str, lat: float, lon: float, at: dt.datetime, accuracy: float | None = None) -> bool:
        """Save a device's position. A report older than the one already kept for that device is ignored."""
        cur = self.by_source.get(source)
        if cur and dt.datetime.fromisoformat(cur["at"]) >= at:
            return False
        self.by_source[source] = {"lat": round(lat, 6), "lon": round(lon, 6), "accuracy": accuracy,
                                  "source": source, "at": at.isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.by_source))
        os.replace(tmp, self.path)
        return True

    def latest(self, now: dt.datetime, max_age: dt.timedelta) -> dict | None:
        """The freshest report from any device, if it is newer than max_age; with age_min added."""
        fresh = [x for x in self.by_source.values() if now - dt.datetime.fromisoformat(x["at"]) <= max_age]
        if not fresh:
            return None
        best = max(fresh, key=lambda x: dt.datetime.fromisoformat(x["at"]))
        return {**best, "age_min": int((now - dt.datetime.fromisoformat(best["at"])).total_seconds() // 60)}
