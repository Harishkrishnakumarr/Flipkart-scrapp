"""Comprehensive tests for Flipkart Seller Email Address Extraction and Validation pipeline."""

import pytest
from bs4 import BeautifulSoup
from scraper.validator import (
    validate_email,
    decode_obfuscated_email,
    is_generic_seller_name,
)
from scraper.product_parser import extract_flipkart_seller_metadata, parse_product_page
from scraper.website_parser import WebsiteParser
from scraper.web_research import (
    WebResearchEngine,
    generate_targeted_email_queries,
)


# ==============================================================================
# 1-3: Validation, Obfuscation & Normalization
# ==============================================================================

def test_1_valid_email_extraction():
    """1. Valid standard email formats are correctly validated."""
    assert validate_email("info@abcenterprises.co.in") == "info@abcenterprises.co.in"
    assert validate_email("sales.dept@company.com") == "sales.dept@company.com"
    assert validate_email("contact_us@domain.in") == "contact_us@domain.in"
    assert validate_email("mybusiness@gmail.com") == "mybusiness@gmail.com"
    assert validate_email("customercare@yahoo.co.in") == "customercare@yahoo.co.in"


def test_2_invalid_email_rejection():
    """2. Invalid and dummy formats are strictly rejected."""
    assert validate_email("not-an-email") is None
    assert validate_email("@domain.com") is None
    assert validate_email("user@") is None
    assert validate_email("user@domain") is None
    assert validate_email("icon@2x.png") is None
    assert validate_email("photo@3x.jpg") is None
    assert validate_email("noreply@abcenterprises.com") is None  # Generic noreply rejected


def test_3_uppercase_and_obfuscation_normalization():
    """3. Uppercase and human obfuscations [at], (at), [dot], (dot) are normalized to lowercase."""
    assert validate_email("SALES@MYCOMPANY.COM") == "sales@mycompany.com"
    assert validate_email("Info@Company.In") == "info@company.in"
    assert validate_email("sales [at] mycompany [dot] com") == "sales@mycompany.com"
    assert validate_email("contact(at)domain(dot)in") == "contact@domain.in"
    assert validate_email("support [AT] brand [DOT] com") == "support@brand.com"
    assert decode_obfuscated_email("info AT company DOT in") == "info@company.in"


# ==============================================================================
# 4-6: Extraction from HTML, Mailto, JSON-LD & Flipkart Source
# ==============================================================================

def test_4_mailto_extraction():
    """4. Mailto link extraction from HTML."""
    html = '<p>Contact us: <a href="mailto:sales@directexport.in?subject=Inquiry">Email Us</a></p>'
    parser = WebsiteParser()
    extracted = parser.extract_from_html(html, "https://directexport.in")
    assert extracted["email"] == "sales@directexport.in"


def test_5_json_ld_extraction():
    """5. JSON-LD structured data email extraction."""
    html = '''
    <html>
    <head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "SuperRetailers",
      "contactPoint": {
        "@type": "ContactPoint",
        "email": "care@superretailers.com",
        "contactType": "customer service"
      }
    }
    </script>
    </head>
    <body><h1>SuperRetailers</h1></body>
    </html>
    '''
    parser = WebsiteParser()
    extracted = parser.extract_from_html(html, "https://superretailers.com")
    assert extracted["email"] == "care@superretailers.com"


def test_6_flipkart_direct_email_extraction():
    """6. Flipkart page direct email extraction from State JSON / Contact DOM."""
    html = '''
    <html>
    <head>
    <script>window.__INITIAL_STATE__ = {"pageData":{"sellerInfo":{"sellerName":"AlphaRetail","sellerEmail":"seller-help@alpharetail.in","sellerPhone":"9876543210"}}};</script>
    </head>
    <body>
    <div id="sellerName"><span>AlphaRetail</span></div>
    </body>
    </html>
    '''
    soup = BeautifulSoup(html, "lxml")
    meta = extract_flipkart_seller_metadata(soup, html)
    assert meta["email"] == "seller-help@alpharetail.in"

    parsed = parse_product_page(html, "https://www.flipkart.com/p/item123")
    assert parsed["email"] == "seller-help@alpharetail.in"


# ==============================================================================
# 7-8: Official Website & Contact Sub-Page Extraction
# ==============================================================================

@pytest.mark.asyncio
async def test_7_official_website_email_extraction(monkeypatch):
    """7. Email extracted from official company homepage."""
    engine = WebResearchEngine()

    async def mock_inspect(url: str):
        return {
            "company_name": "Modern Textiles",
            "email": "contact@moderntextiles.in",
            "website_url": url,
        }

    monkeypatch.setattr(engine.website_parser, "inspect_website", mock_inspect)

    email, src = await engine.enrich_seller_email(
        seller_name="Modern Textiles",
        website_url="https://moderntextiles.in",
    )
    await engine.close()

    assert email == "contact@moderntextiles.in"
    assert "moderntextiles.in" in src


@pytest.mark.asyncio
async def test_8_contact_page_email_extraction(monkeypatch):
    """8. Email discovered on /contact-us sub-page of company website."""
    parser = WebsiteParser()

    async def mock_fetch(url: str):
        if "contact" in url:
            return '<html><body><a href="mailto:sales@primeexports.com">Write to Us</a></body></html>'
        return '<html><body><h1>Welcome to Prime Exports</h1><a href="/contact-us">Contact</a></body></html>'

    monkeypatch.setattr(parser, "fetch_html", mock_fetch)

    data = await parser.inspect_website("https://primeexports.com")
    await parser.close()

    assert data["email"] == "sales@primeexports.com"


# ==============================================================================
# 9-12: Multi-Engine Waterfall & HTTP 429 Fallback
# ==============================================================================

@pytest.mark.asyncio
async def test_9_google_to_bing_fallback(monkeypatch):
    """9. Google failure gracefully falls back to Bing."""
    engine = WebResearchEngine()
    google_called = []
    bing_called = []

    async def mock_google(query: str):
        google_called.append(query)
        return [], 500

    async def mock_bing(query: str):
        bing_called.append(query)
        return [
            {
                "title": "Alpha Creations Surat - Contact Details",
                "url": "https://alphacreations.in/contact",
                "snippet": "Contact Alpha Creations Surat at sales@alphacreations.in for inquiries.",
            }
        ], 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)

    email, src = await engine.enrich_seller_email(
        seller_name="Alpha Creations",
        city="Surat",
        state="Gujarat",
    )
    await engine.close()

    assert len(google_called) > 0
    assert len(bing_called) > 0
    assert email == "sales@alphacreations.in"


@pytest.mark.asyncio
async def test_10_bing_to_brave_fallback(monkeypatch):
    """10. Bing failure gracefully falls back to Brave."""
    engine = WebResearchEngine()
    bing_called = []
    brave_called = []

    async def mock_google(query: str):
        return [], 500

    async def mock_bing(query: str):
        bing_called.append(query)
        return [], 500

    async def mock_brave(query: str):
        brave_called.append(query)
        return [
            {
                "title": "Beta Electronics Noida Official",
                "url": "https://betaelectronics.in/about",
                "snippet": "Beta Electronics Noida Sector 62. Email: info@betaelectronics.in.",
            }
        ], 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)
    monkeypatch.setattr(engine, "_query_brave", mock_brave)

    email, src = await engine.enrich_seller_email(
        seller_name="Beta Electronics",
        city="Noida",
        state="Uttar Pradesh",
    )
    await engine.close()

    assert len(bing_called) > 0
    assert len(brave_called) > 0
    assert email == "info@betaelectronics.in"


@pytest.mark.asyncio
async def test_11_brave_to_duckduckgo_fallback(monkeypatch):
    """11. Brave failure gracefully falls back to DuckDuckGo."""
    engine = WebResearchEngine()
    ddg_called = []

    async def mock_google(query: str):
        return [], 500

    async def mock_bing(query: str):
        return [], 500

    async def mock_brave(query: str):
        return [], 429

    async def mock_ddg(query: str):
        ddg_called.append(query)
        return [
            {
                "title": "Gamma Mills Ludhiana Punjab",
                "url": "https://gammamills.in/contact",
                "snippet": "Email Gamma Mills customer support at support@gammamills.in.",
            }
        ]

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)
    monkeypatch.setattr(engine, "_query_brave", mock_brave)
    monkeypatch.setattr(engine, "_query_ddg", mock_ddg)

    email, src = await engine.enrich_seller_email(
        seller_name="Gamma Mills",
        city="Ludhiana",
        state="Punjab",
    )
    await engine.close()

    assert len(ddg_called) > 0
    assert email == "support@gammamills.in"


@pytest.mark.asyncio
async def test_12_google_http_429_fallback(monkeypatch):
    """12. Google HTTP 429 rate limit triggers Bing fallback immediately without failing."""
    engine = WebResearchEngine()

    async def mock_google(query: str):
        return [], 429  # Rate limited

    async def mock_bing(query: str):
        return [
            {
                "title": "Delta Apparels Tirupur",
                "url": "https://deltaapparels.in/contact",
                "snippet": "Delta Apparels Tirupur sales desk: sales@deltaapparels.in.",
            }
        ], 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)

    email, src = await engine.enrich_seller_email(
        seller_name="Delta Apparels",
        city="Tirupur",
        state="Tamil Nadu",
    )
    await engine.close()

    assert email == "sales@deltaapparels.in"


# ==============================================================================
# 13-14: Search Snippet & Opened Page Inspection
# ==============================================================================

@pytest.mark.asyncio
async def test_13_snippet_email_extraction(monkeypatch):
    """13. Email candidate directly present in search result snippet."""
    engine = WebResearchEngine()

    async def mock_google(query: str):
        return [], 500

    async def mock_bing(query: str):
        return [
            {
                "title": "Sigma Traders Jaipur Details",
                "url": "https://sigmatradersjaipur.com",
                "snippet": "Sigma Traders in Jaipur Rajasthan. Write to us at info@sigmatradersjaipur.com.",
            }
        ], 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)

    email, _ = await engine.enrich_seller_email(
        seller_name="Sigma Traders",
        city="Jaipur",
        state="Rajasthan",
    )
    await engine.close()

    assert email == "info@sigmatradersjaipur.com"


@pytest.mark.asyncio
async def test_14_opened_page_email_extraction(monkeypatch):
    """14. When search snippet lacks email, opening result webpage extracts candidate."""
    engine = WebResearchEngine()

    search_result_without_email = [
        {
            "title": "Omega Footwear Agra - Official Portal",
            "url": "https://omegafootwear.co.in",
            "snippet": "Manufacturers of genuine leather shoes and footwear in Agra.",
        }
    ]

    async def mock_google(query: str):
        return [], 500

    async def mock_bing(query: str):
        return search_result_without_email, 200

    async def mock_fetch(url: str):
        return '<html><body><p>For wholesale inquiries contact sales@omegafootwear.co.in</p></body></html>'

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)
    monkeypatch.setattr(engine.website_parser, "fetch_html", mock_fetch)

    email, src = await engine.enrich_seller_email(
        seller_name="Omega Footwear",
        city="Agra",
        state="Uttar Pradesh",
    )
    await engine.close()

    assert email == "sales@omegafootwear.co.in"
    assert "omegafootwear.co.in" in src


# ==============================================================================
# 15-19: Identity Anchors (Name, City, State, GSTIN, Domain)
# ==============================================================================

def test_15_targeted_queries_contain_seller_name():
    """15. All generated queries contain the target seller name."""
    queries = generate_targeted_email_queries(seller_name="REEPREECREATION")
    assert all('"REEPREECREATION"' in q or "REEPREE" in q for q in queries)


def test_16_17_targeted_queries_contain_city_and_state():
    """16 & 17. Generated queries contain city and state identifiers."""
    queries = generate_targeted_email_queries(
        seller_name="Kalyan Garments",
        city="Surat",
        state="Gujarat",
        location="Ring Road, Surat",
    )
    assert any('"Kalyan Garments" "Surat" email' in q for q in queries)
    assert any('"Kalyan Garments" "Surat" contact email' in q for q in queries)
    assert any('"Kalyan Garments" "Ring Road, Surat" email' in q for q in queries)
    assert any('"Kalyan Garments" "Gujarat" email' in q for q in queries)


def test_18_targeted_queries_contain_gstin():
    """18. GST-anchored email queries generated when GSTIN is provided."""
    queries = generate_targeted_email_queries(
        seller_name="ABC Enterprises",
        gst_number="27AAPFU0939F1ZV",
    )
    assert any('"ABC Enterprises" "27AAPFU0939F1ZV" email' in q for q in queries)
    assert any('"27AAPFU0939F1ZV" email' in q for q in queries)
    assert any('"27AAPFU0939F1ZV" "@"' in q for q in queries)


def test_19_targeted_queries_contain_domain():
    """19. Domain-anchored email queries generated when official website is known."""
    queries = generate_targeted_email_queries(
        seller_name="ABC Enterprises",
        website_url="https://abcenterprises.co.in",
    )
    assert any('"ABC Enterprises" "abcenterprises.co.in" email' in q for q in queries)
    assert any('"@abcenterprises.co.in"' in q for q in queries)


# ==============================================================================
# 20-23: Generic Name Protection, Wrong City/Company & Marketplace Rejection
# ==============================================================================

@pytest.mark.asyncio
async def test_20_21_generic_seller_name_and_wrong_city_rejection(monkeypatch):
    """20 & 21. Generic seller name requires location match; rejects wrong city."""
    engine = WebResearchEngine()

    # Wrong city (Delhi instead of Ahmedabad)
    wrong_city_result = [
        {
            "title": "Royal Collections Chandni Chowk Delhi",
            "url": "https://delhibiz.example.com/royal-collections",
            "snippet": "Royal Collections Delhi wholesale shop email: royal_delhi@gmail.com.",
        }
    ]

    # Correct city (Ahmedabad)
    correct_city_result = [
        {
            "title": "Royal Collections Relief Road Ahmedabad Gujarat",
            "url": "https://gujaratbiz.example.com/royal-collections",
            "snippet": "Royal Collections Ahmedabad, Gujarat. Reach us at contact@royalcollectionsahmedabad.com.",
        }
    ]

    async def mock_google(query: str):
        return [], 500

    async def mock_bing_wrong(query: str):
        return wrong_city_result, 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing_wrong)

    # Scenario A: Searching for Ahmedabad seller with only Delhi result -> Rejected
    email_rej, _ = await engine.enrich_seller_email(
        seller_name="Royal Collections",
        city="Ahmedabad",
        state="Gujarat",
    )
    assert email_rej is None

    # Scenario B: Searching with matching Ahmedabad result -> Accepted
    async def mock_bing_correct(query: str):
        return correct_city_result, 200

    monkeypatch.setattr(engine, "_query_bing", mock_bing_correct)

    email_acc, src = await engine.enrich_seller_email(
        seller_name="Royal Collections",
        city="Ahmedabad",
        state="Gujarat",
    )
    await engine.close()

    assert email_acc == "contact@royalcollectionsahmedabad.com"


@pytest.mark.asyncio
async def test_22_wrong_company_email_rejection(monkeypatch):
    """22. Email belonging to an unrelated company is rejected."""
    engine = WebResearchEngine()

    unrelated_result = [
        {
            "title": "XYZ Logistics International India",
            "url": "https://xyzlogistics.example.com/contact",
            "snippet": "Contact XYZ Logistics at support@xyzlogistics.com for parcel tracking.",
        }
    ]

    async def mock_google(query: str):
        return [], 500

    async def mock_bing(query: str):
        return unrelated_result, 200

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)

    email, _ = await engine.enrich_seller_email(
        seller_name="ABC Enterprises",
        city="Surat",
        state="Gujarat",
    )
    await engine.close()

    assert email is None


def test_23_marketplace_support_email_rejection():
    """23. Marketplace support emails (Flipkart, Amazon, Meesho, etc.) are strictly rejected."""
    assert validate_email("seller-support@flipkart.com") is None
    assert validate_email("support@flipkart.com") is None
    assert validate_email("help@amazon.in") is None
    assert validate_email("sellerdesk@meesho.com") is None
    assert validate_email("merchant@myntra.com") is None
    assert validate_email("support@indiamart.com") is None


# ==============================================================================
# 24-25: Transient Failure Non-Caching & Final Output Mapping
# ==============================================================================

@pytest.mark.asyncio
async def test_24_temporary_search_failure_not_permanently_cached(monkeypatch):
    """24. Temporary 429 / network failure does NOT permanently lock cache as NOT_FOUND."""
    engine = WebResearchEngine()

    async def mock_google(query: str):
        return [], 429

    async def mock_bing(query: str):
        return [], 500

    async def mock_brave(query: str):
        return [], 429

    async def mock_ddg(query: str):
        return []

    monkeypatch.setattr(engine, "_query_google", mock_google)
    monkeypatch.setattr(engine, "_query_bing", mock_bing)
    monkeypatch.setattr(engine, "_query_brave", mock_brave)
    monkeypatch.setattr(engine, "_query_ddg", mock_ddg)

    email, _ = await engine.enrich_seller_email(
        seller_name="TemporaryFailSeller",
        city="Surat",
        state="Gujarat",
    )
    await engine.close()

    assert email is None
    # Ensure cache did not persist a permanent NOT_FOUND for this temporary failure
    assert engine.cache.get("enriched::flipkart::temporaryfailseller") is None


@pytest.mark.asyncio
async def test_25_final_output_mapping_to_email_address():
    """25. Output dictionary correctly contains 'Email Address' and matches 'email'."""
    engine = WebResearchEngine()

    seller_record = {
        "marketplace": "flipkart",
        "seller_name": "KALYANENTERPRISES",
        "email": "sales@kalyanenterprises.co.in",  # Provided by Flipkart profile
        "city": "Bengaluru",
        "state": "Karnataka",
    }

    enriched = await engine.enrich_seller(seller_record)
    await engine.close()

    assert enriched["Email Address"] == "sales@kalyanenterprises.co.in"
    assert enriched["email"] == "sales@kalyanenterprises.co.in"
