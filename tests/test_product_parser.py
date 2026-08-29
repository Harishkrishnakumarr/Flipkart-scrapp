"""Unit tests for Flipkart product parser, fulfillment separation, and candidate scoring."""

import pytest
from scraper.product_parser import (
    extract_fulfillment,
    extract_product_rating,
    is_valid_seller_name,
    parse_product_page,
)


def test_reject_become_a_seller():
    """Verify that generic Flipkart CTAs and navigation labels are rejected."""
    invalid_candidates = [
        "Become a Seller",
        "become a seller",
        "BECOME A SELLER",
        "Become seller",
        "Sell on Flipkart",
        "Sell on Flipkart now",
        "Sell On Flipkart",
        "Start Selling",
        "start seller",
        "Seller",
        "Sold By",
        "Flipkart",
        "Buy Now",
        "Add to Cart",
        "Ratings and reviews",
        "Specifications",
        "7 Days Replacement",
        "GST invoice available",
        "Explore Plus",
        "Cart",
        "Fulfilled by WalkWearr",
        "Seller: ABC",
    ]
    for candidate in invalid_candidates:
        assert not is_valid_seller_name(candidate), f"Expected '{candidate}' to be rejected"


# =========================================================================
# REQUIRED TEST CASES FROM STEP 15
# =========================================================================

def test_case_1_fulfilled_and_seller_inline():
    """Test Case 1: Fulfilled by WalkWearr, Seller: ABC ENTERPRISES."""
    html = """
    <html>
      <body>
        <div>Fulfilled by WalkWearr</div>
        <div>Seller: ABC ENTERPRISES</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm1")
    assert res["fulfilled_by_seller"] == "WalkWearr"
    assert res["fulfillment_by"] == "WalkWearr"
    assert res["seller_name"] == "ABC ENTERPRISES"
    assert "WalkWearr" in res["seller_values_found"]
    assert "ABC ENTERPRISES" in res["seller_values_found"]


def test_case_2_fulfilled_and_seller_next_line():
    """Test Case 2: Fulfilled by WalkWearr, Seller: on next line ABC ENTERPRISES."""
    html = """
    <html>
      <body>
        <div>Fulfilled by WalkWearr</div>
        <div>
          <span>Seller:</span>
          <span>ABC ENTERPRISES</span>
        </div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm2")
    assert res["fulfilled_by_seller"] == "WalkWearr"
    assert res["fulfillment_by"] == "WalkWearr"
    assert res["seller_name"] == "ABC ENTERPRISES"


def test_case_3_fulfilled_by_flipkart_seller_xyz():
    """Test Case 3: Fulfilled by Flipkart, Seller: XYZ RETAIL."""
    html = """
    <html>
      <body>
        <div>Fulfilled by Flipkart</div>
        <div>Seller: XYZ RETAIL</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm3")
    assert res["fulfilled_by_seller"] == "Flipkart"
    assert res["fulfillment_by"] == "Flipkart"
    assert res["seller_name"] == "XYZ RETAIL"


def test_case_4_become_a_seller_only():
    """Test Case 4: Page with only Become a Seller -> NOT_FOUND."""
    html = """
    <html>
      <header>
        <a href="https://seller.flipkart.com">Become a Seller</a>
      </header>
      <body>
        <div>Out of stock</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm4")
    assert res["seller_name"] in ["", "NOT_FOUND"]
    assert res["fulfilled_by_seller"] in [None, "NOT_FOUND"]


def test_case_5_seller_label_followed_by_become_a_seller():
    """Test Case 5: Seller followed by Become a Seller -> NOT_FOUND."""
    html = """
    <html>
      <body>
        <div>Seller:</div>
        <div>Become a Seller</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm5")
    assert res["seller_name"] in ["", "NOT_FOUND"]
    assert res["fulfilled_by_seller"] in [None, "NOT_FOUND"]


def test_case_6_fulfilled_by_abc_seller_xyz():
    """Test Case 6: Fulfilled by ABC, Seller: XYZ."""
    html = """
    <html>
      <body>
        <div>Fulfilled by ABC</div>
        <div>Seller: XYZ</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm6")
    assert res["fulfilled_by_seller"] == "ABC"
    assert res["fulfillment_by"] == "ABC"
    assert res["seller_name"] == "XYZ"


# =========================================================================
# ADDITIONAL LAYOUT & RATING TESTS
# =========================================================================

def test_extract_real_seller_with_navbar_become_seller():
    """Verify extracting real seller name when navbar has 'Become a Seller'."""
    html = """
    <html>
      <header>
        <a href="https://seller.flipkart.com">Become a Seller</a>
        <a href="/login">Login</a>
      </header>
      <body>
        <div class="product-details">
          <h1>Wireless Gaming Mouse</h1>
          <div class="_1RLSqn">
            <div><span>Seller</span></div>
            <div id="sellerName">
              <span>CloudByte Retail Pvt Ltd</span>
              <div class="_3LWZlK _1D-8DK">4.4 ★</div>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/wireless-mouse/p/itm123")
    assert res["seller_name"] == "CloudByte Retail Pvt Ltd"
    assert res["star_rating"] == 4.4


def test_extract_seller_rating():
    """Verify extraction of seller rating from seller badge."""
    html = """
    <html>
      <body>
        <div id="sellerName">
          <span>Apex Infotech</span>
          <div class="_3LWZlK">4.8</div>
        </div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm456")
    assert res["seller_name"] == "Apex Infotech"
    assert res["star_rating"] == 4.8


def test_reject_invalid_rating():
    """Verify that prices, review counts, and years are not parsed as ratings."""
    html = """
    <html>
      <body>
        <div id="sellerName">
          <span>Zenith Retail</span>
        </div>
        <div class="price">₹4,999</div>
        <div class="reviews">1,245 reviews</div>
        <div class="year">2026 Model</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm789")
    assert res["seller_name"] == "Zenith Retail"
    assert res["star_rating"] is None


def test_distinguish_product_rating_from_seller_rating():
    """Verify that product rating under 'Ratings & Reviews' is not assigned as seller star_rating."""
    html = """
    <html>
      <body>
        <div id="sellerName">
          <span>SuperDeals India</span>
        </div>
        <div class="ratings-and-reviews">
          <h2>Ratings & Reviews</h2>
          <div class="_3LWZlK">3.9 ★</div>
          <span>Good</span>
          <span>1,820 ratings and 230 reviews</span>
        </div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/product/p/itm111")
    assert res["seller_name"] == "SuperDeals India"
    assert res["star_rating"] is None
    assert res["product_rating"] == 3.9


def test_parse_product_json_ld():
    """Verify extracting seller from schema.org JSON-LD."""
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org/",
          "@type": "Product",
          "name": "Super Smartphone",
          "offers": {
            "@type": "Offer",
            "price": "14999",
            "seller": {
              "@type": "Organization",
              "name": "OmniTech Retail"
            }
          }
        }
        </script>
      </head>
      <body><div>Product Details</div></body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/test-product/p/itm123")
    assert res["seller_name"] == "OmniTech Retail"


def test_parse_product_initial_state():
    """Verify extracting seller from window.__INITIAL_STATE__."""
    html = """
    <html>
      <body>
        <script>
          window.__INITIAL_STATE__ = {"pageData": {"seller": {"sellerName": "Cloudtail India", "sellerRating": 4.6}}};
        </script>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/test-product/p/itm789")
    assert res["seller_name"] == "Cloudtail India"
    assert res["star_rating"] == 4.6


# =========================================================================
# REGRESSION TESTS (CASES 1 to 8)
# =========================================================================

def test_regression_1_seller_found_in_embedded_dls_json():
    """1. Seller found in embedded DLS multiWidgetState JSON."""
    html = """
    <html>
      <body>
        <script>
          window.__INITIAL_STATE__ = {
            "multiWidgetState": {
              "widgetsData": {
                "slots": [
                  {
                    "slotData": {
                      "widget": {
                        "data": {
                          "dlsData": {
                            "default_fk_pp_delivery_widget_seller_v3_3": {
                              "box_0": {
                                "value": {
                                  "text": "Sold By Khodifab"
                                }
                              },
                              "rating_box": {
                                "value": {
                                  "text": "4.1"
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                ]
              }
            }
          };
        </script>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/shirt/p/itm123")
    assert res["seller_name"] == "Khodifab"
    assert res["star_rating"] == 4.1
    assert res["page_status"] == "PRODUCT_PAGE"


def test_regression_2_seller_found_in_html_sold_by():
    """2. Seller found in HTML text via 'Sold By <Entity>'."""
    html = """
    <html>
      <body>
        <div class="delivery-container">
          <span>Delivery Details</span>
          <div>Sold By REEPREECREATION</div>
          <span>Show all dealers</span>
        </div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/saree/p/itm456")
    assert res["seller_name"] == "REEPREECREATION"
    assert res["page_status"] == "PRODUCT_PAGE"


def test_regression_3_seller_missing():
    """3. Seller missing on genuine accessible product page."""
    html = """
    <html>
      <body>
        <h1>Casual Cotton Shirt</h1>
        <div>Currently unavailable</div>
        <div>No sellers available for this pincode</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/shirt/p/itm000")
    assert res["seller_name"] == ""
    assert res["page_status"] == "PRODUCT_PAGE"


def test_regression_4_captcha_block_response():
    """4. CAPTCHA or block response detected properly."""
    captcha_html = """
    <html>
      <head><title>Flipkart - Robot or Human?</title></head>
      <body>
        <div>Please solve this captcha to continue</div>
      </body>
    </html>
    """
    res_captcha = parse_product_page(captcha_html, "https://www.flipkart.com/item/p/itm111")
    assert res_captcha["page_status"] == "CAPTCHA"

    blocked_html = "<html><body>Access Denied: 403 Forbidden</body></html>"
    res_blocked = parse_product_page(blocked_html, "https://www.flipkart.com/item/p/itm222", http_status=403)
    assert res_blocked["page_status"] == "BLOCKED"


def test_regression_5_http_failure():
    """5. HTTP failure status code handled without guessing."""
    res_err = parse_product_page("<html><body>Internal Server Error</body></html>", "https://www.flipkart.com/item/p/itm333", http_status=500)
    assert res_err["page_status"] == "REQUEST_FAILED"


def test_regression_6_malformed_empty_page():
    """7. Malformed / empty page handled gracefully."""
    res_empty = parse_product_page("", "https://www.flipkart.com/item/p/itm444")
    assert res_empty["page_status"] == "EMPTY_RESPONSE"
    assert res_empty["seller_name"] == ""


def test_regression_8_seller_name_with_spaces_and_special_chars():
    """8. Seller name containing spaces, dots, and special characters preserved."""
    html = """
    <html>
      <body>
        <div>Sold By LAKHANI FOOTWEAR PVT. LTD.</div>
      </body>
    </html>
    """
    res = parse_product_page(html, "https://www.flipkart.com/shoes/p/itm888")
    assert res["seller_name"] == "LAKHANI FOOTWEAR PVT. LTD."

    html_special = """
    <html>
      <body>
        <div>Seller: A & B Retail (India)</div>
      </body>
    </html>
    """
    res_sp = parse_product_page(html_special, "https://www.flipkart.com/item/p/itm999")
    assert res_sp["seller_name"] == "A & B Retail (India)"

