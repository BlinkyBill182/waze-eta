import datetime as dt
from types import SimpleNamespace

import pytest
from pywaze.route_calculator import WRCError

from waze_eta.app import TZ, make_app
from waze_eta.location import Locations
from waze_eta.waze import Waze

KEY = {"X-API-Key": "k"}
PLACES = {"haifa": {"lat": 32.794, "lon": 34.9896, "bounds": {}}, "tel aviv": {"lat": 32.0853, "lon": 34.7818, "bounds": {}}}


@pytest.fixture
def waze():
    w = Waze()
    w.calls = {"geo": 0, "route": [], "fail": None}

    async def geo(place, base_coords=None):
        w.calls["geo"] += 1
        if place.lower() not in PLACES:
            raise WRCError(f"Cannot get coords for {place}")
        return PLACES[place.lower()]

    async def routes(start, end, time_delta=0, **_):
        w.calls["route"].append(time_delta)
        if w.calls["fail"]:
            raise WRCError(w.calls["fail"])
        return [SimpleNamespace(duration=31.4, distance=11.25, name="slow"),
                SimpleNamespace(duration=28.6, distance=12.04, name="20 איילון צפון")]

    w.calc.address_to_coords, w.calc.calc_routes = geo, routes
    return w


@pytest.fixture
def locs(tmp_path):
    return Locations(tmp_path / "location.json")


@pytest.fixture
async def client(aiohttp_client, waze, locs):
    return await aiohttp_client(make_app(waze, "k", locs))


def ago(minutes):
    return (dt.datetime.now(TZ) - dt.timedelta(minutes=minutes)).isoformat()


async def test_health_needs_no_key(client):
    r = await client.get("/health")
    assert r.status == 200 and await r.json() == {"ok": True}


async def test_key_required(client):
    assert (await client.get("/eta", params={"from": "Haifa", "to": "Tel Aviv"})).status == 401
    r = await client.get("/eta", params={"from": "Haifa", "to": "Tel Aviv"}, headers={"X-API-Key": "wrong"})
    assert r.status == 401


async def test_eta_shape_and_fastest_route(client):
    r = await client.get("/eta", params={"from": "Haifa", "to": "Tel Aviv"}, headers=KEY)
    assert r.status == 200
    b = await r.json()
    assert b["minutes"] == 29 and b["km"] == 12.0 and b["route"] == "20 איילון צפון" and b["cached"] is False
    assert b["from"] == {"lat": 32.794, "lon": 34.9896}
    d, a = dt.datetime.fromisoformat(b["depart_at"]), dt.datetime.fromisoformat(b["arrive_at"])
    assert a - d == dt.timedelta(minutes=29)


async def test_repeat_is_cached(client, waze):
    for _ in range(3):
        b = await (await client.get("/eta", params={"from": "Haifa", "to": "Tel Aviv"}, headers=KEY)).json()
    assert b["cached"] is True and len(waze.calls["route"]) == 1 and waze.calls["geo"] == 2


async def test_coords_skip_geocoding(client, waze):
    r = await client.get("/eta", params={"from": "32.794,34.9896", "to": "32.0853, 34.7818"}, headers=KEY)
    assert r.status == 200 and waze.calls["geo"] == 0


async def test_depart_at_local_time_becomes_minutes_ahead(client, waze):
    t = (dt.datetime.now(TZ) + dt.timedelta(hours=2)).replace(second=0, microsecond=0)
    r = await client.get("/eta", params={"from": "Haifa", "to": "Tel Aviv", "depart_at": t.strftime("%Y-%m-%dT%H:%M")}, headers=KEY)
    assert r.status == 200
    assert waze.calls["route"][0] in (119, 120)
    assert dt.datetime.fromisoformat((await r.json())["depart_at"]) == t


@pytest.mark.parametrize("v", ["tomorrow", "2020-01-01T08:00", (dt.datetime.now(TZ) + dt.timedelta(days=9)).isoformat()])
async def test_bad_depart_at(client, v):
    assert (await client.get("/eta", params={"from": "Haifa", "to": "Tel Aviv", "depart_at": v}, headers=KEY)).status == 400


async def test_missing_params(client):
    assert (await client.get("/eta", params={"from": "Haifa"}, headers=KEY)).status == 400


async def test_unknown_place(client):
    r = await client.get("/eta", params={"from": "nowhere-xyz", "to": "Tel Aviv"}, headers=KEY)
    assert r.status == 422 and "nowhere-xyz" in (await r.json())["error"]


async def test_waze_failure_is_502(client, waze):
    waze.calls["fail"] = "403 Forbidden"
    r = await client.get("/eta", params={"from": "Haifa", "to": "Tel Aviv"}, headers=KEY)
    assert r.status == 502 and "403" in (await r.json())["error"]


def test_refuses_to_start_without_key(waze, locs):
    with pytest.raises(SystemExit):
        make_app(waze, "", locs)


# ---------- location ----------

async def test_location_needs_key(client):
    assert (await client.post("/location", json={"lat": 32, "lon": 34.8, "source": "mac"})).status == 401


async def test_me_uses_the_freshest_device(client, waze):
    await client.post("/location", json={"lat": 32.1, "lon": 34.8, "source": "mac", "at": ago(30)}, headers=KEY)
    await client.post("/location", json={"lat": 32.794, "lon": 34.9896, "source": "telegram", "at": ago(2), "accuracy": 15}, headers=KEY)
    r = await client.get("/eta", params={"from": "me", "to": "Tel Aviv"}, headers=KEY)
    b = await r.json()
    assert r.status == 200 and b["from"] == {"lat": 32.794, "lon": 34.9896}
    assert b["from_location"]["source"] == "telegram" and b["from_location"]["age_min"] == 2
    assert waze.calls["geo"] == 1  # only Tel Aviv was geocoded
    loc = await (await client.get("/location", headers=KEY)).json()
    assert loc["source"] == "telegram" and loc["accuracy"] == 15


async def test_older_report_from_same_device_is_ignored(client):
    await client.post("/location", json={"lat": 32.0, "lon": 34.8, "source": "telegram", "at": ago(1)}, headers=KEY)
    r = await client.post("/location", json={"lat": 31.0, "lon": 34.8, "source": "telegram", "at": ago(5)}, headers=KEY)
    assert (await r.json())["saved"] is False
    assert (await (await client.get("/location", headers=KEY)).json())["lat"] == 32.0


async def test_location_survives_restart(client, tmp_path):
    await client.post("/location", json={"lat": 32.0, "lon": 34.8, "source": "mac"}, headers=KEY)
    assert Locations(tmp_path / "location.json").latest(dt.datetime.now(TZ), dt.timedelta(hours=1))["source"] == "mac"


async def test_me_without_recent_location_is_409(client):
    await client.post("/location", json={"lat": 32.0, "lon": 34.8, "source": "mac", "at": ago(25 * 60)}, headers=KEY)
    assert (await client.get("/eta", params={"from": "me", "to": "Tel Aviv"}, headers=KEY)).status == 409
    assert (await client.get("/location", headers=KEY)).status == 404


@pytest.mark.parametrize("body", [{"lat": 32, "lon": 34.8}, {"lat": 99, "lon": 34.8, "source": "mac"},
                                  {"lat": "x", "lon": 34.8, "source": "mac"},
                                  {"lat": 32, "lon": 34.8, "source": "mac", "at": (dt.datetime.now(TZ) + dt.timedelta(hours=1)).isoformat()}])
async def test_bad_location(client, body):
    assert (await client.post("/location", json=body, headers=KEY)).status == 400
