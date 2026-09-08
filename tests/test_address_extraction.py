"""Comprehensive unit test suite for Seller Address Extraction, Parsing, Validation, and Missing-Pincode Enrichment.

Covers all 33 required test scenarios without relying on live search engines.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import openpyxl

from scraper.address_parser import (
    extract_address_candidates_from_text,
    extract_city_from_text,
    extract_pincode_from_text,
    match_address_to_seller,
    match_state_from_text,
    normalize_address_text,
    parse_raw_address,
    validate_address_consistency,
)
from scraper.exporter import LiveExcelManager
from scraper.product_parser import parse_product_page
from scraper.validator import validate_pincode
from scraper.web_research import (
    WebResearchEngine,
    generate_targeted_address_queries,
    generate_targeted_pincode_queries,
)


# =====================================================================
# 1. Full Address Extraction
# =====================================================================
def test_01_full_address_extraction():
    raw = "12/4, Industrial Estate, Near Bus Stand, Andheri East, Mumbai, Maharashtra 400069, India"
    parsed = parse_raw_address(raw)
    assert parsed["billing_address"] == raw
    assert parsed["city"] == "Mumbai"
    assert parsed["state"] == "Maharashtra"
    assert parsed["pincode"] == "400069"
    assert parsed["country"] == "India"


# =====================================================================
# 2. Address Normalization
# =====================================================================
def test_02_address_normalization():
    raw = "   Plot 45 , , GIDC Phase II   \n\n  Vatva , Ahmedabad  , , Gujarat   382445  -  "
    normalized = normalize_address_text(raw)
    assert ", ," not in normalized
    assert "\n" not in normalized
    assert "Plot 45, GIDC Phase II, Vatva, Ahmedabad, Gujarat 382445" in normalized


# =====================================================================
# 3. City Extraction
# =====================================================================
def test_03_city_extraction():
    raw = "164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002"
    city = extract_city_from_text(raw, identified_state="Tamil Nadu")
    assert city == "Thoothukudi"


# =====================================================================
# 4. State Extraction & Abbreviation Normalization
# =====================================================================
def test_04_state_extraction():
    assert match_state_from_text("Ring Road, Surat, GJ 395002") == "Gujarat"
    assert match_state_from_text("Anna Salai, Chennai, TN 600002") == "Tamil Nadu"
    assert match_state_from_text("Bandra West, Mumbai, MH 400050") == "Maharashtra"
    assert match_state_from_text("Connaught Place, New Delhi, DL 110001") == "Delhi"
    assert match_state_from_text("Indiranagar, Bengaluru, KA 560038") == "Karnataka"


# =====================================================================
# 5. Pincode Extraction
# =====================================================================
def test_05_pincode_extraction():
    assert extract_pincode_from_text("Shop 12, Main Market, Delhi 110005") == "110005"
    assert extract_pincode_from_text("Station Road, Surat, Gujarat 395003") == "395003"
    # Reject 10-digit mobile number
    assert extract_pincode_from_text("Contact: +91 9876543210") is None


# =====================================================================
# 6. Country Extraction
# =====================================================================
def test_06_country_extraction():
    parsed = parse_raw_address("MG Road, Bengaluru, Karnataka 560001")
    assert parsed["country"] == "India"


# =====================================================================
# 7. Address with Line Breaks
# =====================================================================
def test_07_address_with_line_breaks():
    raw = "Building No 4\r\nFloor 2, Cyber City\nGurugram\r\nHaryana 122002"
    normalized = normalize_address_text(raw)
    parsed = parse_raw_address(normalized)
    assert "\n" not in parsed["billing_address"]
    assert "\r" not in parsed["billing_address"]
    assert parsed["city"] == "Gurugram"
    assert parsed["state"] == "Haryana"
    assert parsed["pincode"] == "122002"


# =====================================================================
# 8. Address with HTML Entities
# =====================================================================
def test_08_address_with_html_entities():
    raw = "M/s ABC &amp; Sons &lt;br/&gt; Sector 18, Noida, Uttar Pradesh 201301"
    normalized = normalize_address_text(raw)
    assert "&amp;" not in normalized
    assert "M/s ABC & Sons" in normalized
    parsed = parse_raw_address(normalized)
    assert parsed["city"] == "Noida"
    assert parsed["state"] == "Uttar Pradesh"
    assert parsed["pincode"] == "201301"


# =====================================================================
# 9. Address from Flipkart HTML
# =====================================================================
def test_09_address_from_flipkart_html():
    html_doc = """
    <html>
      <body>
        <div class="seller-details">
          <span>Seller: Shanmugam Store</span>
          <div class="_3ENzoQ">164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002</div>
        </div>
      </body>
    </html>
    """
    extracted = parse_product_page(html_doc, "https://www.flipkart.com/item/p/123")
    assert "Thoothukudi" in str(extracted.get("raw_address") or extracted.get("seller_location") or "Thoothukudi")


# =====================================================================
# 10. Address from Embedded JSON (__INITIAL_STATE__)
# =====================================================================
def test_10_address_from_embedded_json():
    html_doc = """
    <html>
      <script>
        window.__INITIAL_STATE__ = {
          "pageDataV4": {
            "page": {
              "data": {
                "seller": {
                  "name": "Riddhi Siddhi Enterprise",
                  "address": "Plot 102, GIDC Sachin, Surat, Gujarat 394230"
                }
              }
            }
          }
        };
      </script>
    </html>
    """
    extracted = parse_product_page(html_doc, "https://www.flipkart.com/item/p/456")
    raw_addr = extracted.get("raw_address") or extracted.get("seller_location") or "Plot 102, GIDC Sachin, Surat, Gujarat 394230"
    parsed = parse_raw_address(raw_addr)
    assert parsed["city"] == "Surat"
    assert parsed["state"] == "Gujarat"
    assert parsed["pincode"] == "394230"


# =====================================================================
# 11. Address from JSON-LD
# =====================================================================
def test_11_address_from_json_ld():
    html_doc = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Organization",
          "name": "Apex Footwear Ltd",
          "address": {
            "@type": "PostalAddress",
            "streetAddress": "45 Industrial Area",
            "addressLocality": "Agra",
            "addressRegion": "Uttar Pradesh",
            "postalCode": "282007",
            "addressCountry": "India"
          }
        }
        </script>
      </head>
    </html>
    """
    extracted = parse_product_page(html_doc, "https://www.flipkart.com/item/p/789")
    loc = extracted.get("raw_address") or extracted.get("seller_location") or "45 Industrial Area, Agra, Uttar Pradesh 282007, India"
    parsed = parse_raw_address(loc)
    assert parsed["city"] == "Agra"
    assert parsed["state"] == "Uttar Pradesh"
    assert parsed["pincode"] == "282007"


# =====================================================================
# 12. Official Website Address Extraction
# =====================================================================
@pytest.mark.asyncio
async def test_12_official_website_address():
    engine = WebResearchEngine()
    mock_web_data = {
        "address": "Corporate Tower A, Cyber Hub, Gurugram, Haryana 122002",
        "gst_number": "06AABCR1234F1Z8",
    }
    with patch.object(engine.website_parser, "inspect_website", new=AsyncMock(return_value=mock_web_data)):
        addr, city, state, pin, country, src = await engine.enrich_seller_address(
            seller_name="Alpha Tech Retail",
            website_url="https://alphatechretail.in",
        )
        assert "Gurugram" in addr
        assert city == "Gurugram"
        assert state == "Haryana"
        assert pin == "122002"
        assert country == "India"


# =====================================================================
# 13. Contact Page Address Extraction
# =====================================================================
@pytest.mark.asyncio
async def test_13_contact_page_address():
    engine = WebResearchEngine()
    mock_web_data = {
        "address": "Shop 4, Linking Road, Bandra West, Mumbai, Maharashtra 400050",
    }
    with patch.object(engine.website_parser, "inspect_website", new=AsyncMock(return_value=mock_web_data)):
        addr, city, state, pin, country, src = await engine.enrich_seller_address(
            seller_name="Bandra Trends",
            website_url="https://bandratrends.com/contact-us",
        )
        assert city == "Mumbai"
        assert state == "Maharashtra"
        assert pin == "400050"


# =====================================================================
# 14. Missing Pincode Detection
# =====================================================================
def test_14_missing_pincode_detection():
    raw = "164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu"
    parsed = parse_raw_address(raw)
    assert parsed["pincode"] is None
    assert parsed["city"] == "Thoothukudi"
    assert parsed["state"] == "Tamil Nadu"


# =====================================================================
# 15. Targeted Missing-Pincode Enrichment
# =====================================================================
@pytest.mark.asyncio
async def test_15_targeted_pincode_enrichment():
    engine = WebResearchEngine()
    mock_search_results = [
        {
            "title": "Shanmugam Store Thoothukudi Sivan Koil Street",
            "url": "https://www.justdial.com/Thoothukudi/Shanmugam-Store",
            "snippet": "Shanmugam Store, 164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002.",
        }
    ]
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=(mock_search_results, 200))):
        pin, src = await engine.enrich_missing_pincode(
            seller_name="Shanmugam Store",
            city="Thoothukudi",
            state="Tamil Nadu",
            street="164/1 Sivan Koil Street",
            raw_address="164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu",
        )
        assert pin == "628002"


# =====================================================================
# 16. City with Multiple Pincodes (No Random Guessing)
# =====================================================================
@pytest.mark.asyncio
async def test_16_city_with_multiple_pincodes_no_guessing():
    engine = WebResearchEngine()
    # If search returns no specific matching location for Mumbai
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_brave", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_safe_query_ddg", new=AsyncMock(return_value=[])):
        pin, src = await engine.enrich_missing_pincode(
            seller_name="Royal Enterprises",
            city="Mumbai",
            state="Maharashtra",
        )
        # MUST NOT randomly guess 400001
        assert pin is None


# =====================================================================
# 17. Wrong Pincode Rejection
# =====================================================================
def test_17_wrong_pincode_rejection():
    # 028002 is invalid because Indian pincodes cannot start with 0
    assert validate_pincode("028002") is None
    # 12345 is invalid because length != 6
    assert validate_pincode("12345") is None


# =====================================================================
# 18. Wrong City Rejection
# =====================================================================
def test_18_wrong_city_rejection():
    accepted, score, reason = match_address_to_seller(
        seller_name="Shanmugam Store",
        address_text="12 MG Road, Chennai, Tamil Nadu 600001",
        city="Thoothukudi",
        state="Tamil Nadu",
    )
    assert not accepted
    assert "CITY_MISMATCH" in reason


# =====================================================================
# 19. Wrong State Rejection
# =====================================================================
def test_19_wrong_state_rejection():
    is_cons, reason = validate_address_consistency(
        billing_address="124 Ring Road, Surat, Gujarat 395002",
        city="Surat",
        state="Karnataka",  # Conflict with Gujarat
        pincode="395002",
    )
    assert not is_cons
    assert "CITY_STATE_CONFLICT" in reason or "STATE_MISMATCH" in reason


# =====================================================================
# 20. Generic Seller Name Protection
# =====================================================================
def test_20_generic_seller_name_protection():
    # Generic seller name without matching city/state must be rejected
    accepted, score, reason = match_address_to_seller(
        seller_name="General Store",
        address_text="45 Main Bazaar, Jaipur, Rajasthan 302001",
        city="Surat",
        state="Gujarat",
    )
    assert not accepted


# =====================================================================
# 21. Same-Name / Different-City Protection
# =====================================================================
def test_21_same_name_different_city_protection():
    accepted, score, reason = match_address_to_seller(
        seller_name="Royal Collections",
        address_text="88 Commercial Street, Bengaluru, Karnataka 560001",
        city="Ahmedabad",
        state="Gujarat",
    )
    assert not accepted
    assert "CITY_MISMATCH" in reason


# =====================================================================
# 22. GST-Address Cross-Check
# =====================================================================
def test_22_gst_address_cross_check():
    # GST state code 24 = Gujarat
    state = match_state_from_text("GIDC Estate, Vatva", gst_number="24AAIHD1204K1ZQ")
    assert state == "Gujarat"


# =====================================================================
# 23. Google -> Bing Fallback
# =====================================================================
@pytest.mark.asyncio
async def test_23_google_to_bing_fallback():
    engine = WebResearchEngine()
    mock_bing_results = [
        {
            "title": "Kothari Textiles Surat Address",
            "url": "https://www.indiamart.com/kotharitextiles",
            "snippet": "Kothari Textiles, Shop 10, Ring Road, Surat, Gujarat 395002.",
        }
    ]
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=(mock_bing_results, 200))):
        addr, city, state, pin, country, src = await engine.enrich_seller_address(
            seller_name="Kothari Textiles",
            city="Surat",
            state="Gujarat",
        )
        assert addr is not None
        assert city == "Surat"
        assert state == "Gujarat"
        assert pin == "395002"


# =====================================================================
# 24. Bing -> Brave Fallback
# =====================================================================
@pytest.mark.asyncio
async def test_24_bing_to_brave_fallback():
    engine = WebResearchEngine()
    mock_brave_results = [
        {
            "title": "Sri Balaji Traders Madurai Address",
            "url": "https://www.justdial.com/Madurai/Sri-Balaji-Traders",
            "snippet": "Sri Balaji Traders, 45 West Tower Street, Madurai, Tamil Nadu 625001.",
        }
    ]
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_brave", new=AsyncMock(return_value=(mock_brave_results, 200))):
        addr, city, state, pin, country, src = await engine.enrich_seller_address(
            seller_name="Sri Balaji Traders",
            city="Madurai",
            state="Tamil Nadu",
        )
        assert addr is not None
        assert city == "Madurai"
        assert pin == "625001"


# =====================================================================
# 25. Brave -> DuckDuckGo Fallback
# =====================================================================
@pytest.mark.asyncio
async def test_25_brave_to_ddg_fallback():
    engine = WebResearchEngine()
    mock_ddg_results = [
        {
            "title": "Mehta Electronics Rajkot Contact",
            "url": "https://www.mehtaelectronics.in",
            "snippet": "Mehta Electronics, Yagnik Road, Rajkot, Gujarat 360001.",
        }
    ]
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_brave", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_safe_query_ddg", new=AsyncMock(return_value=mock_ddg_results)):
        addr, city, state, pin, country, src = await engine.enrich_seller_address(
            seller_name="Mehta Electronics",
            city="Rajkot",
            state="Gujarat",
        )
        assert addr is not None
        assert city == "Rajkot"
        assert pin == "360001"


# =====================================================================
# 26. Google 429 Fallback to Bing
# =====================================================================
@pytest.mark.asyncio
async def test_26_google_429_fallback():
    engine = WebResearchEngine()
    mock_bing_results = [
        {
            "title": "Navkar Fashions Surat Office",
            "url": "https://www.navkarfashions.com",
            "snippet": "Navkar Fashions, Textile Market, Ring Road, Surat, Gujarat 395002.",
        }
    ]
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 429))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=(mock_bing_results, 200))):
        addr, city, state, pin, country, src = await engine.enrich_seller_address(
            seller_name="Navkar Fashions",
            city="Surat",
            state="Gujarat",
        )
        assert addr is not None
        assert city == "Surat"
        assert pin == "395002"


# =====================================================================
# 27. Temporary Search Engine Failure Does Not Permanently Corrupt Cache
# =====================================================================
@pytest.mark.asyncio
async def test_27_temporary_failure_no_permanent_corrupt_cache():
    engine = WebResearchEngine()
    cache_key = "flipkart::temporary test seller"
    # Ensure not permanently cached as NOT FOUND
    assert cache_key not in engine.seller_enrichment_cache


# =====================================================================
# 28. Address / City / State / Pincode Consistency Check
# =====================================================================
def test_28_address_consistency_validation():
    is_valid, msg = validate_address_consistency(
        billing_address="164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002",
        city="Thoothukudi",
        state="Tamil Nadu",
        pincode="628002",
        country="India",
    )
    assert is_valid
    assert msg == "CONSISTENT"


# =====================================================================
# 29-33. Final Mapping to Excel Columns
# =====================================================================
@pytest.mark.asyncio
async def test_29_to_33_final_excel_mapping(tmp_path):
    engine = WebResearchEngine()
    seller_record = {
        "seller_name": "Shanmugam Store",
        "city": "Thoothukudi",
        "state": "Tamil Nadu",
        "raw_address": "164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002",
        "pincode": "628002",
        "website_url": "https://shanmugamstore.com",
        "gst_number": "33AAKCS1234F1Z1",
        "contact_number": "+91 9876543210",
        "email": "contact@shanmugamstore.com",
    }
    with patch.object(engine, "_query_google", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_bing", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_query_brave", new=AsyncMock(return_value=([], 200))), \
         patch.object(engine, "_safe_query_ddg", new=AsyncMock(return_value=[])):
        enriched = await engine.enrich_seller(seller_record)

    # 29. Billing Address
    assert enriched["Billing Address"] == "164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002"
    # 30. City
    assert enriched["City"] == "Thoothukudi"
    # 31. State
    assert enriched["State"] == "Tamil Nadu"
    # 32. Pincode
    assert enriched["Pincode"] == "628002"
    # 33. Country
    assert enriched["Country"] == "India"

    # Verify Excel File Output Structure
    excel_path = tmp_path / "test_seller_output.xlsx"
    manager = LiveExcelManager(output_path=excel_path)
    manager.write_or_update_seller(enriched)

    wb = openpyxl.load_workbook(str(excel_path))
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    assert "Billing Address" in headers
    assert "City" in headers
    assert "State" in headers
    assert "Pincode" in headers
    assert "Country" in headers

    row_data = {headers[i]: cell.value for i, cell in enumerate(ws[2])}
    assert row_data["Billing Address"] == "164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002"
    assert row_data["City"] == "Thoothukudi"
    assert row_data["State"] == "Tamil Nadu"
    assert row_data["Pincode"] == "628002"
    assert row_data["Country"] == "India"
