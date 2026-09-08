"""Comprehensive tests for Flipkart Seller Phone Number Extraction and Enrichment pipeline."""

import pytest
from scraper.validator import (
    validate_phone,
    is_generic_seller_name,
)
from scraper.web_research import (
    WebResearchEngine,
    generate_targeted_phone_queries,
)


def test_validate_phone_strict():
    """Verify phone validation accepts valid Indian numbers and rejects invalid / marketplace numbers."""
    assert validate_phone("9876543210") == "9876543210"
    assert validate_phone("+91-9876543210") == "9876543210"
    assert validate_phone("09876543210") == "9876543210"
    assert validate_phone("+91 87654 32109") == "8765432109"
    assert validate_phone("7123456789") == "7123456789"
    assert validate_phone("6123456789") == "6123456789"

    # Reject non-mobile / invalid prefixes / wrong length
    assert validate_phone("18002089898") is None  # Toll free
    assert validate_phone("1800-111-222") is None
    assert validate_phone("1234567890") is None   # Starts with 1
    assert validate_phone("5123456789") is None   # Starts with 5
    assert validate_phone("98765") is None        # Too short
    assert validate_phone("987654321012") is None # Too long


def test_is_generic_seller_name():
    """Verify generic seller name detection for strict location-anchored phone matching."""
    assert is_generic_seller_name("ABC Enterprises") is True
    assert is_generic_seller_name("Fashion Store") is True
    assert is_generic_seller_name("Metro Traders") is True
    assert is_generic_seller_name("Gupta Trading Co") is True
    assert is_generic_seller_name("Royal Collections") is True
    assert is_generic_seller_name("National Industries") is True
    assert is_generic_seller_name("Retail Store") is True

    # Unique brand names are not generic
    assert is_generic_seller_name("REEPREECREATION") is False
    assert is_generic_seller_name("CHARLIEINTERNATIONAL") is False
    assert is_generic_seller_name("OVIDASECRET") is False


def test_generate_targeted_phone_queries():
    """Verify targeted phone query generation with location, GST, and domain identifiers."""
    queries = generate_targeted_phone_queries(
        seller_name="Alpha Traders",
        city="Surat",
        state="Gujarat",
        location="Ring Road, Surat",
        gst_number="24AAACA1234F1Z5",
        website_url="https://alphatraders.in",
    )

    # Must contain location-anchored queries
    assert any('"Alpha Traders" "Surat" phone' in q for q in queries)
    assert any('"Alpha Traders" "Surat" mobile' in q for q in queries)
    assert any('"Alpha Traders" "Surat" contact' in q for q in queries)
    assert any('"Alpha Traders" "Ring Road, Surat" phone' in q for q in queries)
    assert any('"Alpha Traders" "Gujarat" phone' in q for q in queries)

    # Must contain GST-anchored queries
    assert any('"24AAACA1234F1Z5" phone' in q for q in queries)
    assert any('"Alpha Traders" "24AAACA1234F1Z5" phone' in q for q in queries)

    # Must contain domain-anchored queries
    assert any('"Alpha Traders" "alphatraders.in" phone' in q for q in queries)


@pytest.mark.asyncio
async def test_first_source_flipkart_skips_web_search(monkeypatch):
    """If valid seller phone is already available from Flipkart, use it and do NOT search the web."""
    engine = WebResearchEngine()

    search_called = []

    async def mock_google(query: str):
        search_called.append(("Google", query))
        return [], 200

    async def mock_bing(query: str):
        search_called.append(("Bing", query))
        return [], 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)

    phone, source = await engine.enrich_seller_phone(
        seller_name="OmniRetail",
        existing_phone="9876543210",
    )
    await engine.close()

    assert phone == "9876543210"
    assert "Flipkart" in source
    assert len(search_called) == 0  # No external queries were made


@pytest.mark.asyncio
async def test_multi_engine_waterfall_google_to_bing(monkeypatch):
    """Google 429 / error gracefully falls back to Bing and extracts valid phone."""
    engine = WebResearchEngine()

    google_called = []
    bing_called = []

    async def mock_google(query: str):
        google_called.append(query)
        return [], 429  # Google rate limited

    async def mock_bing(query: str):
        bing_called.append(query)
        return [
            {
                "title": "Alpha Creations Surat - Contact Us",
                "url": "https://alphacreations.in/contact",
                "snippet": "Alpha Creations, Ring Road, Surat. Call us at +91 9123456780 or email us.",
            }
        ], 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)

    phone, source = await engine.enrich_seller_phone(
        seller_name="Alpha Creations",
        city="Surat",
        state="Gujarat",
    )
    await engine.close()

    assert len(google_called) > 0
    assert len(bing_called) > 0
    assert phone == "9123456780"
    assert "alphacreations.in" in source


@pytest.mark.asyncio
async def test_generic_seller_name_location_matching(monkeypatch):
    """Generic seller name requires location match; rejects wrong location and accepts matching location."""
    engine = WebResearchEngine()

    async def mock_google(query: str):
        return [], 500

    # Wrong location result (Delhi instead of Ahmedabad)
    wrong_loc_results = [
        {
            "title": "Royal Collections Chandni Chowk Delhi",
            "url": "https://delhitraders.example.com/royal-collections",
            "snippet": "Royal Collections Delhi shop phone: 9811111111.",
        }
    ]

    # Correct location result (Ahmedabad)
    correct_loc_results = [
        {
            "title": "Royal Collections Ahmedabad Gujarat",
            "url": "https://gujaratbiz.example.com/royal-collections",
            "snippet": "Royal Collections, Relief Road, Ahmedabad, Gujarat 380001. Mobile: 9822222222.",
        }
    ]

    # Scenario A: Searching for Ahmedabad seller with only wrong location result -> Rejected
    async def mock_bing_wrong(query: str):
        return wrong_loc_results, 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing_wrong)

    phone_rejected, _ = await engine.enrich_seller_phone(
        seller_name="Royal Collections",
        city="Ahmedabad",
        state="Gujarat",
    )
    assert phone_rejected is None

    # Scenario B: Searching with correct location result -> Accepted
    async def mock_bing_correct(query: str):
        return correct_loc_results, 200

    monkeypatch.setattr(engine, "_query_bing", mock_bing_correct)

    phone_accepted, source = await engine.enrich_seller_phone(
        seller_name="Royal Collections",
        city="Ahmedabad",
        state="Gujarat",
    )
    await engine.close()

    assert phone_accepted == "9822222222"
    assert "gujaratbiz.example.com" in source


@pytest.mark.asyncio
async def test_full_enrich_seller_phone_field_in_output(monkeypatch):
    """Verify that enrich_seller populates 'Phone Number' in output record."""
    engine = WebResearchEngine()

    async def mock_search(query: str):
        return [], 200

    monkeypatch.setattr(engine, "_query_google", mock_search)
    monkeypatch.setattr(engine, "_query_bing", mock_search)
    monkeypatch.setattr(engine, "_query_brave", mock_search)
    monkeypatch.setattr(engine, "_query_ddg", lambda q: [])

    seller_record = {
        "marketplace": "flipkart",
        "seller_name": "TESTPHONEENTERPRISES",
        "contact_number": "+91-9988776655",  # Phone provided by Flipkart
        "email": "test@testphoneenterprises.co.in",
        "city": "Bengaluru",
        "state": "Karnataka",
    }

    enriched = await engine.enrich_seller(seller_record)
    await engine.close()

    assert enriched["Phone Number"] == "9988776655"
    assert enriched["contact_number"] == "9988776655"
