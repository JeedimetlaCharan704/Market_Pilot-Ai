"""Business discovery. One interface, three interchangeable backends.

  osm             Nominatim/Photon geocode + Overpass query. Keyless, default.
  google_places   Google Places Text Search (New). Needs GOOGLE_MAPS_API_KEY.
  scraper_docker  Talks to the official Google Maps Scraper Kit engine over its
                  own REST contract (POST /api/v1/jobs -> poll -> /download).
                  Needs Docker running locally.

`fallback` is not a backend: it is loaded only when every live backend fails AND
ALLOW_FALLBACK=1, and every row it produces is tagged source=fallback so the UI
can never present fixture data as a live result.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import time
import urllib.parse
from datetime import UTC, datetime

from . import net
from .settings import ROOT, runtime_dir, settings

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
NOMINATIM = "https://nominatim.openstreetmap.org/search"
PHOTON = "https://photon.komoot.io/api/"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# Free-text business category -> (OSM tag key, tag value).
OSM_TAGS: dict[str, tuple[str, str]] = {
    "cafe": ("amenity", "cafe"), "coffee shop": ("amenity", "cafe"),
    "coffee": ("amenity", "cafe"), "coffeehouse": ("amenity", "cafe"),
    "tea shop": ("amenity", "cafe"), "bakery": ("shop", "bakery"),
    "bar": ("amenity", "bar"), "pub": ("amenity", "pub"),
    "restaurant": ("amenity", "restaurant"), "pizzeria": ("amenity", "restaurant"),
    "fast food": ("amenity", "fast_food"),
    "salon": ("shop", "hairdresser"), "hair salon": ("shop", "hairdresser"),
    "hairdresser": ("shop", "hairdresser"), "barber": ("shop", "barber"),
    "spa": ("amenity", "spa"), "nail salon": ("shop", "beauty"),
    "gym": ("leisure", "fitness_centre"), "yoga studio": ("amenity", "yoga_studio"),
    "florist": ("shop", "florist"), "bookstore": ("shop", "books"),
    "bookshop": ("shop", "books"), "pharmacy": ("amenity", "pharmacy"),
    "dentist": ("amenity", "dentist"), "clinic": ("amenity", "clinic"),
    "veterinary": ("amenity", "veterinary"), "car repair": ("shop", "car_repair"),
    "mechanic": ("shop", "car_repair"), "hotel": ("tourism", "hotel"),
    "hostel": ("tourism", "hostel"), "pet grooming": ("shop", "pet"),
    "tuition": ("amenity", "language_school"), "school": ("amenity", "school"),
    "clothes shop": ("shop", "clothes"), "boutique": ("shop", "clothes"),
    "real estate": ("office", "estate_agent"), "realtor": ("office", "estate_agent"),
    "property": ("office", "estate_agent"), "real estate agency": ("office", "estate_agent"),
    "architect": ("office", "architect"), "interior design": ("office", "interior_designer"),
    "lawyer": ("office", "lawyer"), "accountant": ("office", "accountant"),
}


class DiscoveryError(RuntimeError):
    pass


# --- query planning ---------------------------------------------------------


def plan_query(business_type: str, city: str) -> dict:
    """Deterministic: no model call, so discovery never depends on an LLM being up."""
    category = " ".join(business_type.split()).lower()
    match = OSM_TAGS.get(category)
    if match is None:
        for key, value in OSM_TAGS.items():
            if f" {key} " in f" {category} " or f" {key}s " in f" {category} ":
                match = value
                break
    return {
        "category": category,
        "location": city.strip(),
        "tag_key": match[0] if match else "",
        "tag_value": match[1] if match else "",
    }


# --- OpenStreetMap (default) ------------------------------------------------


def _cache_file():
    return runtime_dir() / "geocode_cache.json"


def _bundled_cache():
    """The committed cache. Read-only on a serverless host, so it seeds reads."""
    return ROOT / "data" / "geocode_cache.json"


def _cache_read() -> dict:
    for path in (_cache_file(), _bundled_cache()):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def _cache_write(key: str, lat: float, lon: float, source: str) -> None:
    """Coordinates are stable, so they are stored once and reused."""
    data = _cache_read()
    data[key] = {
        "lat": lat,
        "lon": lon,
        "source": source,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = _cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _photon(place: str) -> tuple[float, float]:
    query = urllib.parse.urlencode({"q": place, "limit": 1})
    response = net.get(f"{PHOTON}?{query}", check_robots=False, timeout=20)
    if response.status_code != 200:
        raise DiscoveryError(f"HTTP {response.status_code}")
    features = response.json().get("features") or []
    if not features:
        raise DiscoveryError("no match")
    lon, lat = features[0]["geometry"]["coordinates"][:2]
    return float(lat), float(lon)


def _nominatim(place: str) -> tuple[float, float]:
    query = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    response = net.get(f"{NOMINATIM}?{query}", check_robots=False, timeout=20)
    if response.status_code != 200:
        raise DiscoveryError(f"HTTP {response.status_code}")
    rows = response.json()
    if not rows:
        raise DiscoveryError("no match")
    return float(rows[0]["lat"]), float(rows[0]["lon"])


def geocode(place: str) -> tuple[float, float]:
    """Resolve a place name to coordinates.

    Photon (keyless, komoot-hosted) is tried first because Nominatim may rate-limit
    or 403 certain IPs. Both are real geocoders; results are cached locally.
    """
    key = " ".join(place.split()).lower()
    cached = _cache_read().get(key)
    if cached:
        return float(cached["lat"]), float(cached["lon"])

    errors: list[str] = []
    for name, fetch in (("photon", _photon), ("nominatim", _nominatim)):
        try:
            lat, lon = fetch(place)
        except (DiscoveryError, net.FetchError, ValueError, KeyError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        _cache_write(key, lat, lon, name)
        return lat, lon
    raise DiscoveryError(f"could not geocode {place!r} -> " + "; ".join(errors))


def _bbox(lat: float, lon: float, radius_m: int = 15000) -> str:
    d_lat = radius_m / 111_320.0
    d_lon = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return f"({lat - d_lat},{lon - d_lon},{lat + d_lat},{lon + d_lon})"


def _why(response) -> str:
    """Pull the human-readable error out of Overpass's HTML error page."""
    match = re.search(r"Error</strong>:\s*([^<]+)", response.text)
    return match.group(1).strip() if match else ""


def _overpass(query: str) -> dict:
    url_query = urllib.parse.urlencode({"data": query})
    errors: list[str] = []
    for attempt in range(2):
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                response = net.get(f"{endpoint}?{url_query}", check_robots=False, timeout=45)
            except net.FetchError as exc:
                errors.append(f"{endpoint.split('/')[2]}: {exc}")
                continue
            if response.status_code != 200:
                errors.append(f"{endpoint.split('/')[2]}: HTTP {response.status_code} {_why(response)}")
                continue
            try:
                payload = response.json()
            except ValueError:
                errors.append(f"{endpoint.split('/')[2]}: non-JSON body {_why(response)}")
                continue
            if isinstance(payload, dict) and "elements" in payload:
                return payload
            errors.append(f"{endpoint.split('/')[2]}: no elements key")
        if attempt == 0:
            time.sleep(3)
    raise DiscoveryError("Overpass failed on every mirror -> " + "; ".join(errors[-4:]))


def _selectors(plan: dict, box: str, *, require_website: bool) -> list[str]:
    extra = '["website"]' if require_website else ""
    selectors: list[str] = []
    if plan["tag_value"]:
        tag = f'["{plan["tag_key"]}"="{plan["tag_value"]}"]{extra}'
        selectors += [f"node{tag}{box}", f"way{tag}{box}"]
    escaped = re.escape(plan["category"]).replace("\\ ", ".*")
    selectors += [
        f'node["name"~"{escaped}",i]{extra}{box}',
        f'way["name"~"{escaped}",i]{extra}{box}',
    ]
    return selectors


def _overpass_elements(selectors: list[str], cap: int) -> list[dict]:
    body = "".join(f"{selector};" for selector in selectors)
    query = f"[out:json][timeout:30];({body});out center tags {cap};"
    return _overpass(query).get("elements", [])


def osm_search(business_type: str, city: str, limit: int) -> list[dict]:
    plan = plan_query(business_type, city)
    lat, lon = geocode(plan["location"])
    box = _bbox(lat, lon)
    cap = limit * 4

    errors: list[str] = []
    found: dict[tuple, dict] = {}
    for require_website in (True, False):
        try:
            batch = _overpass_elements(
                _selectors(plan, box, require_website=require_website), cap
            )
        except DiscoveryError as exc:
            errors.append(str(exc))
            batch = []
        for element in batch:
            found[(element.get("type"), element.get("id"))] = element
        if len(found) >= max(4, limit):
            break
    elements = list(found.values())

    if not elements:
        raise DiscoveryError("; ".join(errors) or f"OpenStreetMap found nothing for {plan['category']!r}")

    out, seen = [], set()
    for element in elements:
        tags = element.get("tags") or {}
        name = (tags.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "source": "OSM",
                "external_id": f"{element.get('type', 'node')}/{element.get('id', 0)}",
                "name": name,
                "website": _first(tags, "website", "contact:website", "url"),
                "phone": _first(tags, "phone", "contact:phone", "contact:mobile"),
                "email": _first(tags, "email", "contact:email"),
                "address": _osm_address(tags) or plan["location"],
                "city": tags.get("addr:city") or plan["location"],
                "rating": None,
                "review_count": None,
            }
        )
        if len(out) >= limit:
            break

    out.sort(key=lambda row: (row["website"] is None, row["name"].lower()))
    return out


def _first(tags: dict, *keys: str) -> str | None:
    for key in keys:
        if tags.get(key):
            return tags[key]
    return None


def _osm_address(tags: dict) -> str | None:
    parts = [tags.get(k, "") for k in
             ("addr:housenumber", "addr:street", "addr:suburb", "addr:city", "addr:postcode")]
    joined = " ".join(p for p in parts if p).strip()
    return joined or tags.get("addr:full")


# --- Google Places ----------------------------------------------------------


def places_search(business_type: str, city: str, limit: int) -> list[dict]:
    cfg = settings()
    if not cfg["google_maps_api_key"]:
        raise DiscoveryError("GOOGLE_MAPS_API_KEY is not set")
    response = net.post(
        PLACES_URL,
        json={
            "textQuery": f"{business_type} in {city}",
            "maxResultCount": min(limit, 20),
            "languageCode": "en",
        },
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": cfg["google_maps_api_key"],
            "X-Goog-FieldMask": ",".join(
                [
                    "places.id", "places.displayName", "places.formattedAddress",
                    "places.location", "places.websiteUri", "places.nationalPhoneNumber",
                    "places.internationalPhoneNumber", "places.rating",
                    "places.userRatingCount", "places.addressComponents",
                ]
            ),
        },
        check_robots=False,
    )
    if response.status_code != 200:
        raise DiscoveryError(f"Google Places returned HTTP {response.status_code}: {response.text[:200]}")

    out = []
    for place in response.json().get("places", []):
        components = place.get("addressComponents") or []
        city_name = next(
            (c.get("longText") for c in components if "locality" in (c.get("types") or [])), city
        )
        out.append(
            {
                "source": "GOOGLE_PLACES",
                "external_id": place.get("id", ""),
                "name": (place.get("displayName") or {}).get("text", "").strip() or "Unnamed",
                "website": place.get("websiteUri"),
                "phone": place.get("internationalPhoneNumber")
                or place.get("nationalPhoneNumber"),
                "email": None,
                "address": place.get("formattedAddress"),
                "city": city_name,
                "rating": place.get("rating"),
                "review_count": place.get("userRatingCount"),
            }
        )
    return [r for r in out if r["external_id"]]


# --- Google Maps Scraper Kit engine -----------------------------------------


def scraper_search(business_type: str, city: str, limit: int) -> list[dict]:
    cfg = settings()
    base = cfg["scraper_base_url"]
    headers = {"Content-Type": "application/json"}
    if cfg["scraper_api_key"]:
        headers["X-API-Key"] = cfg["scraper_api_key"]

    health_resp = net.get(f"{base}/api/v1/jobs", check_robots=False, timeout=5)
    if health_resp.status_code >= 400:
        raise DiscoveryError(f"scraper engine not reachable at {base}")

    lat, lon = geocode(city)
    created = net.post(
        f"{base}/api/v1/jobs",
        json={
            "name": "marketpilot",
            "keywords": [f"{business_type} in {city}"],
            "lang": "en",
            "zoom": 15,
            "lat": str(lat),
            "lon": str(lon),
            "fast_mode": False,
            "radius": 8000,
            "depth": 5,
            "email": True,
            "max_time": 240,
        },
        headers=headers,
        check_robots=False,
        timeout=30,
    )
    if created.status_code >= 400:
        raise DiscoveryError(f"job create failed: HTTP {created.status_code}")
    job_id = created.json().get("id")
    if not job_id:
        raise DiscoveryError("scraper engine returned no job id")

    for _ in range(30):
        status = net.get(f"{base}/api/v1/jobs/{job_id}", check_robots=False, timeout=15).json().get("Status")
        if status == "ok":
            break
        if status == "failed":
            raise DiscoveryError("scraper job failed (rate limited?)")
        time.sleep(5)
    else:
        raise DiscoveryError("scraper job timed out")

    csv_text = net.get(f"{base}/api/v1/jobs/{job_id}/download", check_robots=False, timeout=30).text
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    return [
        {
            "source": "SCRAPER_KIT",
            "external_id": f"job-{str(job_id)[:8]}-{index}",
            "name": (row.get("title") or "").strip() or "Unnamed",
            "website": row.get("website") or None,
            "phone": row.get("phone") or None,
            "email": (row.get("emails") or "").split(",")[0].strip() or None,
            "address": row.get("address") or None,
            "city": city,
            "rating": float(row["review_rating"]) if row.get("review_rating") else None,
            "review_count": int(row["review_count"]) if row.get("review_count") else None,
        }
        for index, row in enumerate(rows[:limit])
    ]


# --- fallback fixtures ------------------------------------------------------


def fallback_search(limit: int) -> list[dict]:
    path = ROOT / "data" / "fallback_businesses.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["businesses"]
    for row in rows:
        row["source"] = "FALLBACK"
    return rows[:limit]


# --- entry point ------------------------------------------------------------

BACKENDS: dict[str, object] = {
    "osm": osm_search,
    "google_places": places_search,
    "scraper_docker": scraper_search,
}


def _enabled(name: str, cfg: dict) -> bool:
    if name == "google_places":
        return bool(cfg["google_maps_api_key"])
    if name == "scraper_docker":
        return bool(cfg["scraper_base_url"])
    return True


def health() -> dict:
    cfg = settings()
    out: dict[str, dict] = {}
    for name in BACKENDS:
        entry: dict = {"enabled": _enabled(name, cfg), "configured": True, "reachable": None}
        if name == "google_places":
            entry["configured"] = bool(cfg["google_maps_api_key"])
            entry["hint"] = "set GOOGLE_MAPS_API_KEY in .env"
        elif name == "scraper_docker":
            try:
                response = net.get(
                    f"{cfg['scraper_base_url']}/api/v1/jobs",
                    check_robots=False, timeout=3,
                )
                entry["reachable"] = response.status_code < 400
                entry["hint"] = (
                    f"engine at {cfg['scraper_base_url']} returned HTTP {response.status_code}"
                    if response.status_code >= 400 else "engine is up"
                )
            except net.FetchError as exc:
                entry["reachable"] = False
                entry["hint"] = f"start the Docker kit: {exc}"
        else:
            entry["hint"] = "keyless - Overpass + Photon"
        out[name] = entry
    out["_active"] = cfg["discovery_backend"]
    return out


def search(business_type: str, city: str) -> dict:
    """Configured backend first, then any other enabled backend, then (optionally) fixtures."""
    cfg = settings()
    limit = int(cfg["discovery_limit"])
    errors: list[str] = []

    order = [cfg["discovery_backend"], *sorted(BACKENDS)]
    for name in dict.fromkeys(order):
        fn = BACKENDS.get(name)
        if fn is None or not _enabled(name, cfg):
            continue
        try:
            rows = fn(business_type, city, limit)  # type: ignore[operator]
            return {"backend": name, "rows": rows, "errors": errors}
        except (DiscoveryError, net.FetchError) as exc:
            errors.append(f"{name}: {exc}")

    if cfg["allow_fallback"] == "1":
        try:
            rows = fallback_search(limit)
            errors.append("all live backends failed; showing FIXTURE data")
            return {"backend": "fallback", "rows": rows, "errors": errors}
        except FileNotFoundError as exc:
            errors.append(f"fallback: {exc}")
    raise DiscoveryError("; ".join(errors) or "no discovery backend available")
