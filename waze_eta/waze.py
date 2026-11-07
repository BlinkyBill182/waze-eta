"""The one place that talks to Waze (through pywaze), with caches so callers can't get the IP blocked."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from pywaze.route_calculator import WazeRouteCalculator, WRCError

GEO_TTL = 7 * 24 * 3600  # addresses don't move
ROUTE_TTL = 10 * 60      # traffic does
BUCKET = 15              # departures within the same 15 minutes share a cached route


class NotFound(Exception):
    """Waze found no place for an address."""


class WazeError(Exception):
    """Waze didn't answer, or answered with something unusable."""


@dataclass
class Route:
    minutes: float
    km: float
    name: str
    start: dict
    end: dict


class Waze:
    def __init__(self, region: str = "IL", concurrency: int = 2, timeout: int = 20):
        self.calc = WazeRouteCalculator(region=region, timeout=timeout)
        self.gate = asyncio.Semaphore(concurrency)
        self.geo: dict[str, tuple[float, dict]] = {}
        self.routes: dict[tuple, tuple[float, Route]] = {}

    async def coords(self, place: str) -> dict:
        """{lat, lon} for an address or a "lat,lon" string."""
        place = " ".join(place.split())
        if self.calc.already_coords(place):
            c = self.calc.coords_string_parser(place)
            return {"lat": c["lat"], "lon": c["lon"]}
        hit = self.geo.get(place.lower())
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            async with self.gate:
                c = await self.calc.address_to_coords(place)
        except WRCError as e:
            if str(e).startswith("Cannot get coords"):
                raise NotFound(place) from e
            raise WazeError(str(e)) from e
        out = {"lat": round(c["lat"], 6), "lon": round(c["lon"], 6)}
        self.geo[place.lower()] = (time.time() + GEO_TTL, out)
        return out

    async def route(self, origin: str, dest: str, depart_in: int = 0) -> tuple[Route, bool]:
        """Fastest drivable route leaving depart_in minutes from now. Returns (route, came_from_cache)."""
        start, end = await self.coords(origin), await self.coords(dest)
        key = (start["lat"], start["lon"], end["lat"], end["lon"], (int(time.time() // 60) + depart_in) // BUCKET)
        hit = self.routes.get(key)
        if hit and hit[0] > time.time():
            return hit[1], True
        try:
            async with self.gate:
                found = await self.calc.calc_routes(f"{start['lat']},{start['lon']}", f"{end['lat']},{end['lon']}",
                                                    time_delta=depart_in)
        except WRCError as e:
            raise WazeError(str(e) or type(e).__name__) from e
        if not found:
            raise WazeError("no route")
        best = min(found, key=lambda r: r.duration)
        r = Route(best.duration, best.distance, best.name, start, end)
        self.routes = {k: v for k, v in self.routes.items() if v[0] > time.time()}
        self.routes[key] = (time.time() + ROUTE_TTL, r)
        return r, False
