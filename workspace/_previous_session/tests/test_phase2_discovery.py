"""Phase 2 tests: query planning, lead upsert idempotency, geo helpers."""

from __future__ import annotations

import os

import pytest

from marketpilot.models import Lead
from marketpilot.services import discovery
from marketpilot.statuses import Status


# --- deterministic query parser (the fallback when the model is unreachable) ---


def test_parser_splits_on_in():
    q = discovery._parse_search_query("independent coffee shops in Bengaluru")
    assert q.category == "independent coffee shops"
    assert q.location == "Bengaluru"
    assert q.osm_tag == "cafe"


def test_parser_splits_on_last_comma():
    q = discovery._parse_search_query("boutique hotels, Goa")
    assert q.category == "boutique hotels"
    assert q.location == "Goa"


def test_parser_handles_text_without_location():
    q = discovery._parse_search_query("dry cleaners")
    assert "dry cleaners" in q.category
    assert q.location


def test_osm_tag_lookup_is_case_insensitive():
    assert discovery._parse_search_query("Coffee Shop in Pune").osm_tag == "cafe"
    assert discovery._parse_search_query("Nail salons in Pune").osm_tag == ""


def test_search_query_rejects_blank_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        discovery.SearchQuery(category="  ", location="Bengaluru")


# --- geo helpers -------------------------------------------------------------


def test_bbox_is_centred_and_respects_radius():
    box = discovery._bbox(12.9716, 77.5946, 8000)
    south, west, north, east = (float(v) for v in box.strip("()").split(","))
    assert south < 12.9716 < north
    assert west < 77.5946 < east
    # ~8km north/south is 2 * 0.0719 degrees
    assert 0.13 < (north - south) < 0.15
    # away from the equator a longitude degree is shorter, so it takes more of them
    assert (east - west) > (north - south)


def test_city_extracted_from_places_address():
    assert discovery._city_from_address("12 MG Road, Bengaluru, Karnataka 560001, India") == "Bengaluru"
    assert discovery._city_from_address("Bengaluru") == "Bengaluru"
    assert discovery._city_from_address(None) is None


def test_city_preferred_from_address_components():
    components = [
        {"types": ["street_number"], "longText": "12"},
        {"types": ["locality"], "longText": "Bengaluru"},
        {"types": ["administrative_area_level_1"], "longText": "Karnataka"},
    ]
    assert discovery._city_from_components(components) == "Bengaluru"
    assert discovery._city_from_components([{"types": ["country"], "longText": "India"}]) is None
    assert discovery._city_from_components(None) is None


def test_osm_address_joins_available_parts():
    assert (
        discovery._osm_address({"addr:housenumber": "5", "addr:street": "Church Street"})
        == "5 Church Street"
    )
    assert discovery._osm_address({}) is None


# --- persistence -------------------------------------------------------------


def test_upsert_creates_then_updates_without_duplicating(session, job):
    raw = {
        "source": "GOOGLE_PLACES",
        "external_id": "place-abc",
        "name": "Roast House",
        "address": "1 Church Street",
        "city": "Bengaluru",
        "latitude": 12.97,
        "longitude": 77.59,
        "website": None,
        "phone": "+91 80 1234 5678",
        "rating": 4.5,
        "review_count": 120,
    }
    lead, created = discovery.upsert_lead(session, job, raw)
    assert created and lead.status == Status.DISCOVERED.value

    raw["name"] = "Roast House (renamed)"
    raw["website"] = "https://roasthouse.example"
    same, created = discovery.upsert_lead(session, job, raw)

    assert not created and same.id == lead.id
    assert same.name == "Roast House (renamed)"
    assert same.website == "https://roasthouse.example"
    assert session.query(Lead).count() == 1


def test_same_external_id_under_a_different_source_is_a_new_lead(session, job):
    base = {
        "external_id": "shared-id",
        "name": "Corner Cafe",
        "address": None, "city": None, "latitude": None, "longitude": None,
        "website": None, "phone": None, "rating": None, "review_count": None,
    }
    discovery.upsert_lead(session, job, {**base, "source": "GOOGLE_PLACES"})
    discovery.upsert_lead(session, job, {**base, "source": "OSM"})
    assert session.query(Lead).count() == 2


# --- live integration (real HTTP, no mocks; opt-in) ---------------------------

@pytest.mark.skipif(
    os.getenv("MARKETPILOT_LIVE_TESTS") != "1",
    reason="set MARKETPILOT_LIVE_TESTS=1 to hit OpenStreetMap",
)
def test_osm_discovery_finds_real_businesses(session, job):
    from marketpilot.config import get_settings

    settings = get_settings()
    settings.discovery.provider = "osm"
    result = discovery.discover(session, job, settings)
    assert result["found"] > 0
    leads = session.query(Lead).all()
    assert leads and all(lead.status == Status.DISCOVERED.value for lead in leads)

    again = discovery.discover(session, job, settings)
    assert again.get("skipped") is True
    assert session.query(Lead).count() == len(leads)
