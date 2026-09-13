"""Unit tests verifying all 13 core requirements for seller enrichment pipeline fixes:
1. Valid website detection for exact seller name match
2. Rejection of weak candidate websites
3. Rejection of excluded domains for website discovery
4. Valid email extraction from verified official website
5. Valid email extraction matching seller domain
6. Rejection of disallowed email domains (figma.com, spanishdict.com, ahima.org, wbztv.com, cloudflare.com)
7. Rejection of generic email without seller name match
8. Valid phone extraction from official website
9. Rejection of generic marketplace numbers
10. Valid GST extraction and validation
11. PAN extraction derived strictly from GSTIN
12. No standalone PAN search when GSTIN is missing
13. Overwriting mismatched PAN when valid GSTIN is present
"""

import pytest
from unittest.mock import AsyncMock, patch

from scraper.validator import (
    DISALLOWED_EMAIL_DOMAINS,
    extract_pan_from_gstin,
    validate_email,
    validate_gst,
    validate_pan,
    validate_phone,
)
from scraper.web_research import (
    EXCLUDED_WEBSITE_DOMAINS,
    WebResearchEngine,
    generate_bing_queries_for_field,
    generate_brave_queries_for_field,
    generate_targeted_queries_for_field,
    is_bad_enrichment,
)
from scraper.website_parser import WebsiteParser


# 1. Valid website detection for exact seller name match
def test_01_valid_website_detection_for_exact_seller():
    engine = WebResearchEngine()
    search_results = [
        {
            "title": "Sidh India Plastics - Official Online Store",
            "url": "https://www.sidhindia.com/about-us",
            "snippet": "Sidh India Plastics is a leading plastic manufacturer in Delhi, India.",
        }
    ]
    candidates = engine._identify_candidate_websites("Sidh India Plastics", search_results)
    assert len(candidates) >= 1
    assert candidates[0] == "https://www.sidhindia.com"


# 2. Rejection of weak candidate websites
def test_02_rejection_of_weak_candidate_websites():
    engine = WebResearchEngine()
    search_results = [
        {
            "title": "SpanishDict | English to Spanish Translation",
            "url": "https://www.spanishdict.com/translate/sidh",
            "snippet": "Translate sidh into Spanish. Words and dictionary meanings.",
        },
        {
            "title": "Game8 Wiki Guides",
            "url": "https://game8.co/games/zelda",
            "snippet": "Walkthrough and quest guides.",
        }
    ]
    candidates = engine._identify_candidate_websites("Sidh India Plastics", search_results)
    assert len(candidates) == 0


# 3. Rejection of excluded domains for website discovery
def test_03_rejection_of_excluded_domains():
    engine = WebResearchEngine()
    search_results = [
        {
            "title": "Sidh India Plastics on Flipkart",
            "url": "https://www.flipkart.com/seller/sidh-india",
            "snippet": "Shop products from Sidh India Plastics on Flipkart.",
        },
        {
            "title": "Sidh India on Figma Community",
            "url": "https://www.figma.com/@sidhindia",
            "snippet": "Design files and UI components by Sidh India.",
        },
        {
            "title": "Sidh India on Amazon India",
            "url": "https://www.amazon.in/sp?seller=A123456",
            "snippet": "Seller profile on Amazon.in.",
        }
    ]
    candidates = engine._identify_candidate_websites("Sidh India Plastics", search_results)
    assert len(candidates) == 0


# 4. Valid email extraction from verified official website
def test_04_valid_email_from_verified_official_website():
    parser = WebsiteParser()
    html = """
    <html>
      <body>
        <h1>Contact Sidh India Plastics</h1>
        <p>Email us at: <a href="mailto:info@sidhindia.com">info@sidhindia.com</a></p>
      </body>
    </html>
    """
    extracted = parser.extract_from_html(html, "https://www.sidhindia.com/contact")
    assert extracted.get("email") == "info@sidhindia.com"


# 5. Valid email extraction matching seller domain
def test_05_valid_email_matching_seller_domain():
    assert validate_email("support@sidhindia.com") == "support@sidhindia.com"
    assert validate_email("sales@redtape.com") == "sales@redtape.com"


# 6. Rejection of disallowed email domains
def test_06_rejection_of_disallowed_email_domains():
    disallowed_examples = [
        "marcia@wbztv.com",
        "membership@ahima.org",
        "designer@figma.com",
        "help@spanishdict.com",
        "support@cloudflare.com",
    ]
    for em in disallowed_examples:
        assert validate_email(em) is None


# 7. Rejection of generic email without seller name match
@pytest.mark.asyncio
async def test_07_rejection_of_generic_email_without_seller_match():
    engine = WebResearchEngine()
    mock_results = [
        {
            "title": "Random User Page",
            "url": "https://example.com/user",
            "snippet": "Contact me at john.doe12345@gmail.com for inquiries.",
        }
    ]
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 503))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=(mock_results, 200))):
        email, _ = await engine.enrich_seller_email(seller_name="Sidh India Plastics", city="Delhi")
        assert email is None
    await engine.close()


# 8. Valid phone extraction from official website
@pytest.mark.asyncio
async def test_08_valid_phone_from_official_website():
    engine = WebResearchEngine()
    mock_inspect = {
        "contact_number": "+91 98112 34567",
        "email": "info@sidhindia.com",
        "gst_number": None,
    }
    with patch.object(engine.website_parser, "inspect_website", new=AsyncMock(return_value=mock_inspect)):
        phone, src = await engine.enrich_seller_phone(
            seller_name="Sidh India Plastics",
            website_url="https://www.sidhindia.com"
        )
        assert phone == "9811234567"
        assert src == "https://www.sidhindia.com"
    await engine.close()


# 9. Rejection of generic marketplace numbers
def test_09_rejection_of_generic_marketplace_numbers():
    generic_numbers = [
        "18002089898",  # Flipkart customer care
        "180030009009", # Amazon customer care
        "1800123456",
        "18000000000",
        "9999999999",
        "0000000000",
    ]
    for num in generic_numbers:
        assert validate_phone(num) is None


# 10. Valid GST extraction and validation
def test_10_valid_gst_extraction_and_validation():
    valid_gst = "24AAIHD1204K1ZQ"
    assert validate_gst(valid_gst) == "24AAIHD1204K1ZQ"
    assert validate_gst("24aaihd1204k1zq") == "24AAIHD1204K1ZQ"
    assert validate_gst("invalid_gst_format") is None
    # Reject Flipkart/marketplace corporate boilerplates in address/location
    assert validate_gst("99AAIHD1204K1ZQ") is None  # Invalid state code 99


# 11. PAN extraction derived strictly from GSTIN
def test_11_pan_derived_strictly_from_gstin():
    valid_gst = "24AAIHD1204K1ZQ"
    pan = extract_pan_from_gstin(valid_gst)
    assert pan == "AAIHD1204K"
    assert len(pan) == 10
    assert validate_pan(pan, gst_str=valid_gst) == "AAIHD1204K"


# 12. No standalone PAN search when GSTIN is missing
@pytest.mark.asyncio
async def test_12_no_standalone_pan_search_when_gstin_missing():
    # Verify standalone PAN search queries are strictly empty
    assert generate_targeted_queries_for_field("Sidh India Plastics", "pan") == []
    assert generate_bing_queries_for_field("Sidh India Plastics", "pan") == []
    assert generate_brave_queries_for_field("Sidh India Plastics", "pan") == []

    engine = WebResearchEngine()
    record_without_gst = {
        "marketplace": "flipkart",
        "seller_name": "Sidh India Plastics",
        "city": "Delhi",
        "state": "Delhi",
    }
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 503))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_brave", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_ddg", new=AsyncMock(return_value=[])):
        enriched = await engine.enrich_seller(record_without_gst)
        # When GSTIN is missing, PAN must be None
        assert enriched.get("pan_number") is None
        assert enriched.get("PAN Number") is None
    await engine.close()


# 13. Overwriting mismatched PAN when valid GSTIN is present
@pytest.mark.asyncio
async def test_13_overwriting_mismatched_pan_when_valid_gstin_present():
    engine = WebResearchEngine()
    # Provided seller record with a conflicting/wrong PAN
    record_with_mismatched_pan = {
        "marketplace": "flipkart",
        "seller_name": "Sidh India Plastics",
        "gst_number": "24AAIHD1204K1ZQ",  # Derived PAN is AAIHD1204K
        "pan_number": "WRONG1234P",       # Conflicting PAN
        "city": "Surat",
        "state": "Gujarat",
    }
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 503))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_brave", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_ddg", new=AsyncMock(return_value=[])):
        enriched = await engine.enrich_seller(record_with_mismatched_pan)
        # PAN must match GSTIN[2:12], overwriting the conflicting PAN
        assert enriched["pan_number"] == "AAIHD1204K"
        assert enriched["PAN Number"] == "AAIHD1204K"
    await engine.close()
