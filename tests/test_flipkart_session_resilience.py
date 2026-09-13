"""Comprehensive unit tests for Flipkart session resilience, product validation, website parser, and web research."""

import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import httpx

from scraper.flipkart_search import (
    classify_flipkart_response,
    clean_flipkart_product_url,
    is_valid_flipkart_product_url,
    FlipkartSearchScraper,
)
from scraper.product_parser import (
    detect_flipkart_page_status,
    is_valid_flipkart_product_page,
    parse_product_page,
    is_valid_seller_name,
    find_seller_candidates_with_scores,
)
from scraper.website_parser import WebsiteParser, MAX_RESPONSE_BYTES
from scraper.web_research import (
    WebResearchEngine,
    generate_identity_queries_for_field,
    _seller_domain_match,
    _strict_seller_domain_match,
    _domain_is_excluded,
    _root_domain,
)
from scraper.seller_extractor import seller_key


# =========================================================================
# 1. Product URL Validation & Cleaning
# =========================================================================

def test_valid_flipkart_product_urls():
    """Verify valid product URLs are accepted and query tracking stripped."""
    url1 = "https://www.flipkart.com/boat-airdopes-131-bluetooth-headset/p/itm12345?pid=ACCE123&lid=LST123"
    assert is_valid_flipkart_product_url(url1) is True
    cleaned = clean_flipkart_product_url(url1)
    assert cleaned == "https://www.flipkart.com/boat-airdopes-131-bluetooth-headset/p/itm12345?pid=ACCE123"

    url2 = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4"
    assert is_valid_flipkart_product_url(url2) is True
    assert clean_flipkart_product_url(url2) == "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4"


def test_invalid_flipkart_urls_rejected():
    """Verify login, cart, search, account, and help URLs are rejected."""
    invalid_urls = [
        "https://www.flipkart.com/account/login?ret=/",
        "https://www.flipkart.com/viewcart?otracker=Cart_Icon",
        "https://www.flipkart.com/checkout/init",
        "https://www.flipkart.com/search?q=running+shoes",
        "https://www.flipkart.com/helpcentre",
        "https://seller.flipkart.com/sell-online",
        "https://www.flipkart.com/wishlist",
        "not_a_url",
        "",
        None,
    ]
    for url in invalid_urls:
        assert is_valid_flipkart_product_url(url) is False, f"Expected {url} to be rejected"


# =========================================================================
# 2. Page & Response Classification (401, 403, 429, CAPTCHA, Login, Empty)
# =========================================================================

def test_classify_http_error_codes():
    """Verify HTTP status codes 401, 403, 429, 500 classification."""
    assert classify_flipkart_response(401, "<html></html>", "https://flipkart.com") == "FLIPKART_LOGIN_REQUIRED"
    assert classify_flipkart_response(403, "<html></html>", "https://flipkart.com") == "FLIPKART_BLOCKED"
    assert classify_flipkart_response(429, "<html></html>", "https://flipkart.com") == "FLIPKART_BLOCKED"
    assert classify_flipkart_response(500, "<html></html>", "https://flipkart.com") == "REQUEST_FAILED"
    assert classify_flipkart_response(503, "<html></html>", "https://flipkart.com") == "REQUEST_FAILED"


def test_classify_captcha_and_challenge_pages():
    """Verify CAPTCHA / bot challenge detection."""
    captcha_html = "<html><head><title>Robot or human?</title></head><body><div id='px-captcha'>Please solve this captcha</div></body></html>"
    assert classify_flipkart_response(200, captcha_html, "https://www.flipkart.com/product/p/itm123") == "FLIPKART_CHALLENGE"

    status = detect_flipkart_page_status(captcha_html, page_url="https://www.flipkart.com/product/p/itm123")
    assert status == "CAPTCHA"

    is_valid, reason = is_valid_flipkart_product_page(captcha_html, page_url="https://www.flipkart.com/product/p/itm123")
    assert is_valid is False
    assert reason == "CAPTCHA"


def test_classify_access_denied_pages():
    """Verify access denied / blocked page detection."""
    blocked_html = "<html><body><h1>Access Denied</h1><p>You do not have permission to access on this server.</p></body></html>"
    assert classify_flipkart_response(200, blocked_html, "https://www.flipkart.com/product/p/itm123") == "FLIPKART_BLOCKED"

    is_valid, reason = is_valid_flipkart_product_page(blocked_html, page_url="https://www.flipkart.com/product/p/itm123")
    assert is_valid is False
    assert reason == "BLOCKED"


def test_classify_login_redirection():
    """Verify redirection to login page is detected."""
    login_url = "https://www.flipkart.com/account/login?ret=/product/p/itm123"
    login_html = "<html><body><h1>Login to Flipkart</h1><input type='text' placeholder='Email/Mobile'></body></html>"
    assert classify_flipkart_response(200, login_html, login_url) == "FLIPKART_LOGIN_REQUIRED"

    is_valid, reason = is_valid_flipkart_product_page(login_html, page_url=login_url)
    assert is_valid is False
    assert reason == "LOGIN_REQUIRED"


def test_classify_empty_or_degraded_response():
    """Verify empty or tiny HTML responses are classified as empty."""
    assert classify_flipkart_response(200, "", "https://www.flipkart.com/product/p/itm123") == "FLIPKART_EMPTY_RESPONSE"
    assert classify_flipkart_response(200, "<html></html>", "https://www.flipkart.com/product/p/itm123") == "FLIPKART_EMPTY_RESPONSE"

    is_valid, reason = is_valid_flipkart_product_page("   ", page_url="https://www.flipkart.com/product/p/itm123")
    assert is_valid is False
    assert reason == "EMPTY_RESPONSE"


# =========================================================================
# 3. Product Page Seller Extraction & Validation
# =========================================================================

def test_parse_product_page_on_blocked_or_captcha():
    """Verify parse_product_page does not invent fake seller info when page is blocked or challenge."""
    captcha_html = "<html><head><title>Robot or human?</title></head><body><div class='px-captcha'>Solve</div><div>Become a seller</div></body></html>"
    res = parse_product_page(captcha_html, "https://www.flipkart.com/product/p/itm123")
    assert res["seller_name"] == ""
    assert res["page_status"] == "CAPTCHA"
    assert res["seller_confidence"] == 0.0

    login_html = "<html><body><form action='/login'><span>Login to Flipkart</span></form></body></html>"
    res_login = parse_product_page(login_html, "https://www.flipkart.com/account/login")
    assert res_login["seller_name"] == ""
    assert res_login["page_status"] == "LOGIN_REQUIRED"


def test_parse_product_page_valid_json_ld():
    """Verify extraction from JSON-LD structured data on valid page."""
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Product",
          "name": "Noise Smart Watch",
          "offers": {
            "@type": "Offer",
            "price": "1499",
            "seller": {
              "@type": "Organization",
              "name": "Nexx Retail India",
              "telephone": "9876543210"
            }
          },
          "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4.3",
            "ratingCount": "1250"
          }
        }
        </script>
      </head>
      <body>
        <div class="product-title">Noise Smart Watch</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/noise-smart-watch/p/itm12345")
    assert res["seller_name"] == "Nexx Retail India"
    assert res["product_rating"] == 4.3
    assert res["product_rating_count"] == 1250
    assert res["page_status"] == "PRODUCT_PAGE"


def test_parse_product_page_multiple_candidates_scoring():
    """Verify candidate scoring prefers explicit state JSON / DOM over generic text."""
    html = """
    <html>
      <body>
        <div>Explore Plus</div>
        <div>Become a Seller</div>
        <div id="sellerName">
          <span>Aura Enterprises</span>
          <div class="_3LWZlK">4.6 ★</div>
        </div>
        <div>7 Days Replacement Policy</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/item/p/itm999")
    assert res["seller_name"] == "Aura Enterprises"
    assert res["star_rating"] == 4.6
    assert res["seller_rating"] == 4.6
    assert is_valid_seller_name("Aura Enterprises") is True
    assert is_valid_seller_name("Become a Seller") is False


# =========================================================================
# 4. Website Parser TLS Security & HTTP Robustness
# =========================================================================

def test_website_parser_tls_verification_default():
    """Verify WebsiteParser enables secure TLS verification by default."""
    parser = WebsiteParser()
    assert parser.verify_ssl is True
    assert parser.client._transport is not None


def test_website_parser_tls_dev_override(monkeypatch):
    """Verify development override for insecure TLS when explicitly enabled."""
    monkeypatch.setenv("FLIPKART_DEV_INSECURE_TLS", "1")
    parser = WebsiteParser()
    assert parser.verify_ssl is False

    parser_custom = WebsiteParser(verify_ssl=True)
    assert parser_custom.verify_ssl is True


@pytest.mark.asyncio
async def test_website_parser_robust_http_handling(monkeypatch):
    """Verify WebsiteParser gracefully handles timeouts, connect errors, and non-HTML."""
    parser = WebsiteParser()

    # Mock timeout
    async def mock_timeout(url):
        raise httpx.TimeoutException("Connection timed out")

    monkeypatch.setattr(parser.client, "get", mock_timeout)
    res = await parser.fetch_html("https://timeout-domain.com")
    assert res is None

    # Mock ConnectError
    async def mock_connect_err(url):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(parser.client, "get", mock_connect_err)
    res2 = await parser.fetch_html("https://refused-domain.com")
    assert res2 is None

    # Mock non-HTML response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/pdf"}
    mock_resp.text = "%PDF-1.4..."
    monkeypatch.setattr(parser.client, "get", AsyncMock(return_value=mock_resp))
    res3 = await parser.fetch_html("https://pdf-domain.com/doc.pdf")
    assert res3 is None

    await parser.close()


# =========================================================================
# 5. Web Research: Query Quality, Candidate Scoring, Domain Matching
# =========================================================================

def test_query_generation_remains_seller_specific():
    """Verify generated queries contain seller identity and are not vague generic searches."""
    queries = generate_identity_queries_for_field("Boat Lifestyle", "gst", city="New Delhi", state="Delhi")
    for q in queries:
        assert "boat" in q.lower()
        # Must not be a vague query without seller
        assert len(q.strip()) > 10

    phone_queries = generate_identity_queries_for_field("Red Tape Limited", "phone", city="Kanpur")
    for q in phone_queries:
        assert "red tape" in q.lower() or "redtape" in q.lower() or "red" in q.lower()


def test_domain_filtering_and_exclusion():
    """Verify disallowed domains (login, gaming, adult, social) are strictly excluded."""
    excluded = [
        "login.microsoftonline.com",
        "account.live.com",
        "game8.co",
        "maxroll.gg",
        "pornhub.com",
        "reddit.com",
        "flipkart.com",
        "amazon.in",
    ]
    for dom in excluded:
        assert _domain_is_excluded(dom) is True, f"Expected {dom} to be excluded"

    assert _domain_is_excluded("boat-lifestyle.com") is False
    assert _domain_is_excluded("sidhindia.com") is False


def test_seller_domain_matching():
    """Verify seller domain match requires meaningful association."""
    assert _seller_domain_match("Boat Lifestyle", "boat-lifestyle.com") is True
    assert _seller_domain_match("Sidh India Plastics", "sidhindia.com") is True
    assert _seller_domain_match("Alpha Traders", "alphatraders.in") is True
    # Unrelated domain should be rejected
    assert _seller_domain_match("Boat Lifestyle", "unrelatedgamingwiki.com") is False
    assert _seller_domain_match("Sidh India Plastics", "microsoft.com") is False


def test_seller_key_normalization():
    """Verify deterministic canonical seller keys for deduplication and caching."""
    assert seller_key("Boat Lifestyle") == "boat lifestyle"
    assert seller_key("A & B Retail Pvt. Ltd.") == "a and b retail pvt ltd"
    assert seller_key("REDTAPELIMITED") == "redtapelimited"
    assert seller_key("  Shanmugam   Store  ") == "shanmugam store"


# =========================================================================
# 6. Web Research Engine: TLS, Caching & Reuse
# =========================================================================

def test_web_research_engine_tls_default():
    """Verify WebResearchEngine enables secure TLS verification by default."""
    engine = WebResearchEngine()
    assert engine.verify_ssl is True
    assert engine.website_parser.verify_ssl is True


@pytest.mark.asyncio
async def test_enrich_seller_cache_reuse():
    """Verify deterministic cache hit on duplicate seller records."""
    engine = WebResearchEngine()

    seller_record = {
        "seller_name": "Verified Brand Retail",
        "marketplace": "flipkart",
        "website_url": "https://verifiedbrandretail.com",
        "email": "contact@verifiedbrandretail.com",
        "contact_number": "9876543210",
        "gst_number": "27AABCV1234F1Z8",
        "city": "Mumbai",
        "state": "Maharashtra",
        "billing_address": "Plot 10, MIDC, Andheri East, Mumbai, Maharashtra 400093",
    }

    # First run enriches and caches
    enriched1 = await engine.enrich_seller(seller_record)
    assert enriched1["GST Number"] == "27AABCV1234F1Z8"
    assert enriched1["Email Address"] == "contact@verifiedbrandretail.com"

    # Second run with same seller reuses cache
    seller_record_2 = {
        "seller_name": "Verified Brand Retail",
        "marketplace": "flipkart",
        "product_url": "https://www.flipkart.com/another-product/p/itm567",
    }
    enriched2 = await engine.enrich_seller(seller_record_2)
    assert enriched2["GST Number"] == "27AABCV1234F1Z8"
    assert enriched2["Email Address"] == "contact@verifiedbrandretail.com"
    assert enriched2["product_url"] == "https://www.flipkart.com/another-product/p/itm567"

    await engine.close()


# =========================================================================
# 7. Playwright Storage State Configuration Support
# =========================================================================

def test_scraper_storage_state_configuration(tmp_path):
    """Verify FlipkartSearchScraper supports user-provided storage_state path."""
    state_file = tmp_path / "storage_state.json"
    state_file.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")

    scraper = FlipkartSearchScraper(storage_state_path=str(state_file))
    assert scraper.storage_state_path == str(state_file)
