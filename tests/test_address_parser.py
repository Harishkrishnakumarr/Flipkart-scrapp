"""Unit tests for address parser and normalization."""

from scraper.address_parser import (
    extract_city_from_text,
    match_state_from_text,
    parse_raw_address,
)


def test_match_state_from_text():
    assert match_state_from_text("Andheri East, Mumbai, Maharashtra 400069") == "Maharashtra"
    assert match_state_from_text("Whitefield, Bangalore, Karnataka - 560066") == "Karnataka"
    assert match_state_from_text("Connaught Place, New Delhi, DL - 110001") == "Delhi"


def test_extract_city_from_text():
    assert extract_city_from_text("Plot 42, HSR Layout, Bengaluru, Karnataka") == "Bengaluru"
    assert extract_city_from_text("Bandra Kurla Complex, Mumbai, MH") == "Mumbai"


def test_parse_raw_address_full():
    raw = "Plot No. 12B, Industrial Area, Phase 1, Chandigarh, 160002, India"
    parsed = parse_raw_address(raw)
    assert parsed["pincode"] == "160002"
    assert parsed["state"] == "Chandigarh"
    assert parsed["country"] == "India"
    assert "Plot No. 12B" in (parsed["billing_address"] or "")


def test_parse_raw_address_with_gst_fallback():
    # If text has no state name, use GSTIN state code (33 = Tamil Nadu)
    raw = "Shop No 5, Anna Salai - 600002"
    parsed = parse_raw_address(raw, gst_number="33ABCDE1234F1Z5")
    assert parsed["state"] == "Tamil Nadu"
    assert parsed["pincode"] == "600002"
