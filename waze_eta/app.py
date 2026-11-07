"""HTTP API: GET /eta, POST/GET /location, GET /health. Contract: README.md."""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
from zoneinfo import ZoneInfo

from aiohttp import web

from .location import Locations
from .waze import NotFound, Waze, WazeError

log = logging.getLogger("waze_eta")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Jerusalem"))
MAX_AHEAD = dt.timedelta(days=7)
MAX_BEHIND = dt.timedelta(hours=1)
# A phone sharing live location reports only when it moves, so a quiet hour is not a stale position.
LOCATION_MAX_AGE = dt.timedelta(hours=float(os.environ.get("LOCATION_MAX_AGE_H", 24)))
ME = "me"

WAZE = web.AppKey("waze", Waze)
KEY = web.AppKey("key", str)
LOCS = web.AppKey("locations", Locations)


class NoLocation(Exception):
    pass


def error(status: int, msg: str) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def parse_depart(v: str | None, now: dt.datetime) -> dt.datetime:
    """ISO date-time; without an offset it is local time (TZ_NAME). Missing → now."""
    if not v:
        return now
    t = dt.datetime.fromisoformat(v.strip().replace(" ", "T"))
    return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)


@web.middleware
async def auth(request: web.Request, handler):
    if request.path != "/health":
        got = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(got.encode(), request.app[KEY].encode()):
            return error(401, "missing or wrong X-API-Key")
    return await handler(request)


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def resolve_me(request: web.Request, place: str) -> tuple[str, dict | None]:
    """"me" → the freshest device position as "lat,lon"; anything else passes through."""
    if place.lower() != ME:
        return place, None
    loc = request.app[LOCS].latest(dt.datetime.now(TZ), LOCATION_MAX_AGE)
    if loc is None:
        raise NoLocation()
    return f"{loc['lat']},{loc['lon']}", {k: loc[k] for k in ("source", "at", "age_min", "accuracy")}


async def post_location(request: web.Request) -> web.Response:
    try:
        b = await request.json()
        lat, lon = float(b["lat"]), float(b["lon"])
        source = str(b.get("source") or "").strip()[:32]
        acc = float(b["accuracy"]) if b.get("accuracy") is not None else None
        at = parse_depart(b.get("at"), dt.datetime.now(TZ))
    except (ValueError, KeyError, TypeError):
        return error(400, 'body must be JSON {"lat", "lon", "source", "accuracy"?, "at"?}')
    if not source or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return error(400, "source is required and lat/lon must be valid")
    if at > dt.datetime.now(TZ) + dt.timedelta(minutes=5):
        return error(400, "at is in the future")
    saved = request.app[LOCS].report(source, lat, lon, at, acc)
    return web.json_response({"saved": saved})


async def get_location(request: web.Request) -> web.Response:
    loc = request.app[LOCS].latest(dt.datetime.now(TZ), LOCATION_MAX_AGE)
    return web.json_response(loc) if loc else error(404, "no recent location from any device")


async def eta(request: web.Request) -> web.Response:
    q = request.query
    origin, dest = (q.get("from") or "").strip(), (q.get("to") or "").strip()
    if not origin or not dest:
        return error(400, "from and to are required")
    now = dt.datetime.now(TZ).replace(second=0, microsecond=0)
    try:
        (origin, from_loc), (dest, to_loc) = resolve_me(request, origin), resolve_me(request, dest)
    except NoLocation:
        return error(409, "from/to is \"me\" but no device reported a location recently")
    try:
        depart = parse_depart(q.get("depart_at"), now)
    except ValueError:
        return error(400, "depart_at must be ISO, e.g. 2026-10-26T08:30 (local) or with an offset")
    if not now - MAX_BEHIND <= depart <= now + MAX_AHEAD:
        return error(400, "depart_at must be between an hour ago and 7 days ahead")
    depart_in = round((depart - now).total_seconds() / 60)
    try:
        r, cached = await request.app[WAZE].route(origin, dest, depart_in)
    except NotFound as e:
        return error(422, f"Waze found no place for: {e}")
    except WazeError as e:
        log.warning("waze failed: %s", e)
        return error(502, f"Waze failed: {e}")
    log.info("eta %d min, %.1f km, in %d min%s", round(r.minutes), r.km, depart_in, " (cached)" if cached else "")
    out = {
        "minutes": round(r.minutes), "km": round(r.km, 1), "route": r.name,
        "depart_at": depart.isoformat(), "arrive_at": (depart + dt.timedelta(minutes=round(r.minutes))).isoformat(),
        "from": r.start, "to": r.end, "cached": cached,
    }
    if from_loc:
        out["from_location"] = from_loc
    if to_loc:
        out["to_location"] = to_loc
    return web.json_response(out)


def make_app(waze: Waze, key: str, locations: Locations) -> web.Application:
    if not key:
        raise SystemExit("ETA_KEY is not set")
    app = web.Application(middlewares=[auth])
    app[WAZE], app[KEY], app[LOCS] = waze, key, locations
    app.router.add_get("/health", health)
    app.router.add_get("/eta", eta)
    app.router.add_post("/location", post_location)
    app.router.add_get("/location", get_location)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    waze = Waze(region=os.environ.get("WAZE_REGION", "IL"))
    locations = Locations(os.environ.get("LOCATION_PATH", "data/location.json"))
    web.run_app(make_app(waze, os.environ.get("ETA_KEY", ""), locations), host="0.0.0.0", port=int(os.environ.get("PORT", 8080)),
                access_log=None)
