"""Flipkart Product Parser Module.

Extracts seller information, fulfillment entity, and ratings across multiple
resilient extraction strategies:
  1. Embedded State JSON (window.__INITIAL_STATE__, window.__PRELOADED_STATE__, __NEXT_DATA__)
     - multiWidgetState / widgetsData / slots / dlsData (modern DLS architecture)
     - 'Sold By <SellerName>' in widget text and actions
     - seller_title, seller_details, widget_seller DLS containers
     - sellerName, sellerDisplayName, sellerRating, etc.
  2. JSON-LD structured data (<script type="application/ld+json">)
  3. Raw HTML Regex (Sold By <Entity>, Seller: <Entity>, Fulfilled by <Entity>)
  4. Targeted DOM Selectors (#sellerName, div._1RLSqn, div.G6XhRU, etc.)
  5. Fulfillment entity extraction (Fulfilled by <Entity>)

Provides comprehensive diagnostic logging (JSON-LD, NEXT_DATA, Seller JSON, Seller HTML)
and distinguishes between:
  - CASE A: Page loaded, seller genuinely unavailable -> NOT_FOUND
  - CASE B: Blocked / CAPTCHA page -> BLOCKED / CAPTCHA
  - CASE C: HTTP failure -> REQUEST_FAILED
  - CASE D: Redirected -> REDIRECTED
  - CASE E: Seller extracted successfully
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from bs4 import BeautifulSoup, Tag

from scraper.config import DEBUG_DIR, GST_STATE_CODES, INDIAN_STATES
from scraper.address_parser import (
    extract_city_from_text,
    match_state_from_text,
    parse_raw_address,
)
from scraper.validator import (
    EMAIL_REGEX,
    GST_REGEX,
    PHONE_REGEX,
    PINCODE_REGEX,
    validate_email,
    validate_gst,
    validate_phone,
)

logger = logging.getLogger("FlipkartScraper.ProductParser")

GSTIN_REGEX = re.compile(r'\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b')


def parse_address_fields(raw_address: str, gst_number: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Splits raw Indian address strings into Billing Address, City, State, and Pincode."""
    if not raw_address:
        return {"billing_address": None, "city": None, "state": None, "pincode": None}

    clean_addr = " ".join(raw_address.split())
    parsed = parse_raw_address(clean_addr, gst_number=gst_number)

    pincode = parsed.get("pincode")
    if not pincode:
        pin_match = PINCODE_REGEX.search(clean_addr)
        pincode = pin_match.group(1) if pin_match else None

    # Detect state
    detected_state = parsed.get("state")
    if not detected_state:
        detected_state = match_state_from_text(clean_addr)
    if not detected_state and gst_number and len(gst_number) >= 2:
        st_code = gst_number[:2]
        detected_state = GST_STATE_CODES.get(st_code)

    # Extract City
    city = parsed.get("city")
    if not city:
        city = extract_city_from_text(clean_addr)
    if not city:
        candidate = clean_addr.split(",")[-2].strip() if "," in clean_addr else ""
        candidate = re.sub(r'\b\d{6}\b', '', candidate).strip()
        if candidate and len(candidate) < 35:
            city = candidate
    if city and city.isupper():
        city = city.title()

    return {
        "billing_address": clean_addr,
        "city": city,
        "state": detected_state,
        "pincode": pincode
    }


def _find_key_in_dict(obj: Any, target_key: str) -> List[Any]:
    """Recursively search for values associated with target_key in nested dict/list."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == target_key.lower() and v:
                results.append(v)
            if isinstance(v, (dict, list)):
                results.extend(_find_key_in_dict(v, target_key))
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                results.extend(_find_key_in_dict(item, target_key))
    return results


def extract_seller_and_ratings(soup: Union[BeautifulSoup, str], page_html: Optional[str] = None) -> Dict[str, Any]:
    """
    Extracts native Flipkart seller info, GSTIN, ratings, and addresses
    using window.__INITIAL_STATE__ and scoped DOM elements.
    """
    if isinstance(soup, str):
        raw_html = soup
        soup = BeautifulSoup(raw_html, "lxml")
        if not page_html or page_html.startswith("http"):
            page_html = raw_html
    elif page_html is None:
        page_html = str(soup)

    details: Dict[str, Any] = {
        "seller_name": None,
        "legal_name": None,
        "gst_number": None,
        "billing_address": None,
        "city": None,
        "state": None,
        "pincode": None,
        "product_rating": None,
        "seller_rating": None,
        "is_f_assured": False
    }

    # 1. State JSON Inspection
    state_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});</script>', page_html, re.DOTALL)
    if not state_match:
        state_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?})[\s;]*<', page_html, re.DOTALL)
    if not state_match:
        state_match = re.search(r'window\.__PRELOADED_STATE__\s*=\s*({.+?})[\s;]*<', page_html, re.DOTALL)
    if not state_match:
        state_match = re.search(r'window\.__PAGE_DATA__\s*=\s*({.+?})[\s;]*<', page_html, re.DOTALL)

    if state_match:
        try:
            state_data = json.loads(state_match.group(1))

            # Direct check for sellerInfo or seller dicts
            seller_infos = _find_key_in_dict(state_data, "sellerInfo") + _find_key_in_dict(state_data, "seller")
            for s_info in seller_infos:
                if isinstance(s_info, dict):
                    details["seller_name"] = s_info.get("sellerName") or s_info.get("name") or details["seller_name"]
                    details["legal_name"] = s_info.get("legalName") or s_info.get("legal_name") or details["legal_name"]
                    details["gst_number"] = s_info.get("gstin") or s_info.get("gstNumber") or details["gst_number"]
                    addr = s_info.get("address") or s_info.get("billingAddress") or s_info.get("registeredAddress")
                    if addr and isinstance(addr, str):
                        details["billing_address"] = addr
                    s_rat = s_info.get("sellerRating") or s_info.get("rating")
                    if s_rat and details["seller_rating"] is None:
                        try:
                            details["seller_rating"] = float(s_rat)
                        except (ValueError, TypeError):
                            pass

            # Also check text values in renderable components
            text_values = _find_key_in_dict(state_data, "text")
            for val_text in text_values:
                if isinstance(val_text, str):
                    gst_m = GSTIN_REGEX.search(val_text)
                    if gst_m and not details["gst_number"]:
                        details["gst_number"] = gst_m.group(1)
                    if any(w in val_text.lower() for w in ["address", "office", "pincode"]) and not details["billing_address"]:
                        details["billing_address"] = val_text
        except Exception:
            pass

    # 2. Scoped DOM Extraction for Seller Name & Rating
    seller_card = soup.select_one("#sellerName, div._1RLviY, div.V3C51r, div[data-testid='seller-badge']")
    if seller_card:
        if not details["seller_name"]:
            name_tag = seller_card.select_one("span, a") or seller_card
            details["seller_name"] = name_tag.get_text(strip=True)

        if not details["seller_rating"]:
            # Only match the badge inside the seller card
            badge = seller_card.select_one("div.XQDdHH, div._3LWZlK, div._1lRcqv, div._1RLviY, div.V3C51r, span._1lRcqv, div._1D-8DK")
            if badge:
                m = re.search(r"(\d+(?:\.\d+)?)", badge.get_text(strip=True))
                if m:
                    details["seller_rating"] = float(m.group(1))
            else:
                m = re.search(r"(\d+(?:\.\d+)?)", seller_card.get_text(strip=True))
                if m:
                    details["seller_rating"] = float(m.group(1))

    # 3. Product Rating (Avoid matching the seller badge)
    if not details["product_rating"]:
        for prod_sel in ["div.XQDdHH", "div._3LWZlK", "div._2d4LTz"]:
            badge = soup.select_one(prod_sel)
            if badge:
                if seller_card and badge in seller_card.descendants:
                    continue
                m = re.search(r"(\d+(?:\.\d+)?)", badge.get_text(strip=True))
                if m:
                    details["product_rating"] = float(m.group(1))
                    break

    # 4. F-Assured Fulfillment Badge
    if soup.select_one("img[src*='fa_'], span[data-testid='f-assured'], img[alt*='Plus'], img[src*='plus']"):
        details["is_f_assured"] = True

    # 5. Normalize Address
    if details.get("billing_address"):
        addr_parts = parse_address_fields(details["billing_address"], gst_number=details.get("gst_number"))
        for k, v in addr_parts.items():
            if v and not details.get(k):
                details[k] = v

    return details

# Hard list of invalid seller names / Flipkart UI labels & CTAs
INVALID_SELLER_NAMES = {
    "become a seller",
    "become seller",
    "sell on flipkart",
    "sell on flipkart now",
    "sell on flipkart.",
    "start selling",
    "start seller",
    "seller",
    "sellers",
    "sold by",
    "seller details",
    "about seller",
    "about seller ",
    "flipkart",
    "flipkart seller",
    "buy now",
    "add to cart",
    "ratings and reviews",
    "ratings & reviews",
    "reviews",
    "specifications",
    "services",
    "view more sellers",
    "see all sellers",
    "see other sellers",
    "other sellers",
    "7 days replacement",
    "10 days replacement",
    "7 days replacement policy",
    "10 days replacement policy",
    "gst invoice available",
    "view details",
    "share",
    "explore plus",
    "cart",
    "login",
    "sign in",
    "top offers",
    "grocery",
    "mobiles",
    "fashion",
    "electronics",
    "home & furniture",
    "appliances",
    "travel",
    "beauty, toys & more",
    "two wheelers",
    "download app",
    "24x7 customer care",
    "advertise",
    "advertise on flipkart",
    "gift cards",
    "help center",
    "f-assured",
    "plus",
    "flipkart plus",
    "quality score",
    "speed score",
    "product sold",
    "product quality",
    "service quality",
    "overall ratings",
    "show all dealers",
    "authorised installation",
    "delivery details",
    "delivery options",
    "flipkart delivery policy",
}

# Targeted CSS selectors for legacy and modern Flipkart Seller Information
TARGETED_SELLER_SELECTORS = [
    "#sellerName",
    "#sellerName span",
    "div._1RLSqn span",
    "div.G6XhRU span",
    "div.G6XhRU",
    "div._2Yx7Pp span",
    "div._2Yx7Pp",
    "div._1k45bO span",
    "div._1k45bO",
    "div._2Npd2b span",
    "div._2Npd2b",
    "div.V3C0sS span",
    "div.vR3XkF span",
    "div.vR3XkF",
    "div._25U9Qn span",
    "div[id='sellerName']",
    "div[data-testid*='seller']",
    "div[class*='sellerName']",
    "span[class*='sellerName']",
]

# Rating Pattern Regex (e.g. 3.9, 4.5, 4.8 ★)
RATING_PATTERN = re.compile(r"\b([1-5](?:\.[0-9])?)\s*(?:★|star|stars)?\b", re.IGNORECASE)


def normalize_text(value: Optional[str]) -> str:
    """Normalize text for consistent comparison (lowercase, collapsed spaces).

    Args:
        value: Input string.

    Returns:
        Normalized string.
    """
    if not value:
        return ""
    return " ".join(str(value).strip().split()).lower()


def is_valid_seller_name(candidate: Optional[str]) -> bool:
    """Validate that a candidate string is a real seller/business name.

    Strictly rejects 'Become a Seller', 'Sell on Flipkart', 'Fulfilled by ...',
    'Seller:', and generic UI labels.

    Args:
        candidate: Candidate seller name string.

    Returns:
        True if valid, False otherwise.
    """
    if not candidate or not isinstance(candidate, str):
        return False

    normalized = normalize_text(candidate)

    if len(normalized) < 2 or len(normalized) > 80:
        return False

    # Reject exact match in blacklisted phrases
    if normalized in INVALID_SELLER_NAMES:
        return False

    # Reject if starts with 'fulfilled by'
    if normalized.startswith("fulfilled by"):
        return False

    # Reject if starts with 'seller:' or 'sold by'
    if normalized.startswith("seller:") or normalized.startswith("sold by"):
        return False

    # Reject if starts with invalid CTA
    for invalid in INVALID_SELLER_NAMES:
        if len(invalid) >= 7 and normalized.startswith(invalid):
            return False

    # Must contain at least one alphabetic character
    if not re.search(r"[a-zA-Z]", normalized):
        return False

    return True


def clean_seller_candidate(raw: Optional[str]) -> Optional[str]:
    """Clean candidate seller string, stripping prefixes and trailing badges while preserving original case.

    Args:
        raw: Raw extracted string.

    Returns:
        Cleaned seller name or None if invalid.
    """
    if not raw:
        return None

    cleaned = str(raw).strip()

    # Strip leading prefixes like "Seller:", "Sold By:", "Seller Details:", "About Seller:"
    cleaned = re.sub(
        r"^(?:Seller\s*:?|Sold\s*By\s*:?|Seller\s*Details\s*:?|About\s*Seller\s*:?)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    # Strip trailing UI text like "Show all dealers", "See other sellers", "Rating", "4.5", "Flipkart"
    cleaned = re.sub(
        r"(?i)\s+(?:Show\s+all(?:\s+dealers)?|See\s+other(?:\s+sellers)?|Rating|Ratings|About|Services|Delivery|7\s*Days|10\s*Days|GST).*$",
        "",
        cleaned,
    ).strip()

    # Strip trailing star rating e.g. "ABC Enterprises 4.5" or "ABC Enterprises 4.5 ★"
    cleaned = re.sub(r"\s+[1-5]\.[0-9]\s*★?$", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Strip leading/trailing non-alphanumeric punctuation (except parentheses/periods inside name)
    cleaned = re.sub(r"^[^\w]+|[^\w\.\)]+$", "", cleaned).strip()

    if not is_valid_seller_name(cleaned):
        logger.debug(f"Rejected invalid seller candidate: '{raw}' -> '{cleaned}'")
        return None

    return cleaned


def extract_fulfillment_seller(soup: Optional[BeautifulSoup], html_text: str) -> Optional[str]:
    """Extract fulfillment entity if present (e.g. 'Fulfilled by WalkWearr' -> 'WalkWearr', 'Fulfilled by Flipkart' -> 'Flipkart').

    Args:
        soup: Optional parsed BeautifulSoup object.
        html_text: Raw HTML string.

    Returns:
        Fulfillment entity name or None.
    """
    # 1. Regex on text: "Fulfilled by <Entity>"
    match = re.search(
        r"(?i)\bFulfilled\s+by\s+([A-Za-z0-9\s.,&\-\(\)]+?)(?:[\s<\"',\\]*(?:Seller|Rating|Services|Delivery|Show\s+all|See\s+other|7\s*Days|10\s*Days|GST|\\n|<|\n|$))",
        html_text,
    )
    if match:
        cand = match.group(1).strip()
        cleaned = re.sub(r"^(?:Fulfilled\s*by\s*:?)", "", cand, flags=re.I).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[^\w]+|[^\w\.\)]+$", "", cleaned).strip()
        if len(cleaned) >= 2 and cleaned.lower() not in {"become a seller", "7 days replacement", "10 days replacement"}:
            return cleaned

    # 2. DOM inspection if soup is provided
    if soup:
        for el in soup.find_all(lambda t: t.text and "fulfilled by" in t.text.lower()):
            text = el.get_text(separator=" ", strip=True)
            m = re.search(r"(?i)fulfilled\s+by\s*[:\s]*([A-Za-z0-9\s.,&\-\(\)]+)", text)
            if m:
                cand = m.group(1).strip()
                cleaned = re.sub(r"^(?:Fulfilled\s*by\s*:?)", "", cand, flags=re.I).strip()
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
                cleaned = re.sub(r"^[^\w]+|[^\w\.\)]+$", "", cleaned).strip()
                if len(cleaned) >= 2 and cleaned.lower() not in {"become a seller", "7 days replacement", "10 days replacement"}:
                    return cleaned

    return None


# Backward-compatibility alias
extract_fulfillment = extract_fulfillment_seller


def extract_rating_from_tag(tag: Tag) -> Optional[float]:
    """Extract numeric star rating from a BeautifulSoup Tag.

    Args:
        tag: BeautifulSoup Tag to inspect.

    Returns:
        Float rating (1.0 to 5.0) or None.
    """
    text = tag.get_text(separator=" ", strip=True)
    match = RATING_PATTERN.search(text)
    if match:
        try:
            val = float(match.group(1))
            if 1.0 <= val <= 5.0:
                return val
        except ValueError:
            pass
    return None


def extract_product_rating_details(
    soup: BeautifulSoup, html_text: str
) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """Extract product-level rating, total ratings count, and reviews count.

    Checks:
      1. JSON-LD structured data (<script type="application/ld+json">)
      2. Modern Flipkart DOM selectors (div.XQDdHH, div._3LWZlK, div._2d4LTz, span.Wphh3K, span._2_R_DZ)
      3. Embedded State JSON (window.__INITIAL_STATE__, pageDataV4, widgetsData)

    Args:
        soup: Parsed BeautifulSoup object.
        html_text: Raw HTML string.

    Returns:
        Tuple of (product_rating, rating_count, review_count).
    """
    prod_rating: Optional[float] = None
    rating_count: Optional[int] = None
    review_count: Optional[int] = None

    # Strategy 1: JSON-LD aggregateRating
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                agg = item.get("aggregateRating")
                if isinstance(agg, dict):
                    if agg.get("ratingValue") is not None and prod_rating is None:
                        try:
                            val = float(agg["ratingValue"])
                            if 1.0 <= val <= 5.0:
                                prod_rating = round(val, 1)
                        except (ValueError, TypeError):
                            pass
                    if agg.get("ratingCount") is not None and rating_count is None:
                        try:
                            rating_count = int(agg["ratingCount"])
                        except (ValueError, TypeError):
                            pass
                    if agg.get("reviewCount") is not None and review_count is None:
                        try:
                            review_count = int(agg["reviewCount"])
                        except (ValueError, TypeError):
                            pass
        except Exception:
            continue

    # Strategy 2: Modern & Legacy DOM selectors for product rating badge
    if prod_rating is None:
        # Common Flipkart product rating badges (usually under the product title / buybox)
        prod_rating_selectors = [
            "div.XQDdHH",                 # Modern Flipkart rating pill
            "div._3LWZlK",                # Legacy Flipkart rating pill
            "div._2d4LTz",                # Large rating score in reviews overview
            "span._1lRcqv",
            "div.IP_AVn",
            "div[class*='XQDdHH']",
            "div[class*='_3LWZlK']",
        ]
        for sel in prod_rating_selectors:
            for el in soup.select(sel):
                # Avoid seller rating badge (which is inside sellerName or seller container)
                parent_text = el.parent.get_text(" ", strip=True).lower() if el.parent else ""
                if "seller" in parent_text and "ratings & reviews" not in parent_text:
                    continue
                r = extract_rating_from_tag(el)
                if r is not None and 1.0 <= r <= 5.0:
                    prod_rating = r
                    break
            if prod_rating is not None:
                break

    # Strategy 3: Rating count and review count from DOM (e.g. "4,512 Ratings & 320 Reviews" / span.Wphh3K / span._2_R_DZ)
    if rating_count is None or review_count is None:
        count_elements = soup.select("span.Wphh3K, span._2_R_DZ, span[class*='Wphh3K'], span[class*='_2_R_DZ']")
        for cel in count_elements:
            txt = cel.get_text(" ", strip=True)
            # Match "4,512 Ratings & 320 Reviews" or "4,512 Ratings" or "320 Reviews"
            m_rc = re.search(r"([\d,]+)\s*(?:Ratings?|ratings?)", txt)
            if m_rc and rating_count is None:
                try:
                    rating_count = int(m_rc.group(1).replace(",", ""))
                except ValueError:
                    pass
            m_rv = re.search(r"([\d,]+)\s*(?:Reviews?|reviews?)", txt)
            if m_rv and review_count is None:
                try:
                    review_count = int(m_rv.group(1).replace(",", ""))
                except ValueError:
                    pass

    # Strategy 4: Fallback to State JSON for product rating
    if prod_rating is None and html_text:
        # Check rating patterns in state JSON
        m_state_r = re.search(
            r'["\'](?:productRating|aggregatedRating|ratingValue|overallRating)["\']\s*:\s*"?([1-5](?:\.[0-9]+)?)"?',
            html_text,
            re.IGNORECASE,
        )
        if m_state_r:
            try:
                val = float(m_state_r.group(1))
                if 1.0 <= val <= 5.0:
                    prod_rating = round(val, 1)
            except (ValueError, TypeError):
                pass

    return prod_rating, rating_count, review_count


def extract_product_rating(soup: BeautifulSoup, html_text: str) -> Optional[float]:
    """Extract product-level rating (from Ratings & Reviews section, DOM, or JSON-LD).

    Args:
        soup: Parsed BeautifulSoup object.
        html_text: Raw HTML string.

    Returns:
        Float product rating (1.0 to 5.0) or None.
    """
    rating, _, _ = extract_product_rating_details(soup, html_text)
    return rating


def extract_is_f_assured(soup: BeautifulSoup, html_text: str) -> bool:
    """Detect Flipkart F-Assured / Plus fulfillment badge.

    Args:
        soup: Parsed BeautifulSoup object.
        html_text: Raw HTML string.

    Returns:
        True if F-Assured badge is detected, False otherwise.
    """
    if "isFAssured\":true" in html_text or "is_f_assured\":true" in html_text:
        return True

    # Check DOM badges
    for img in soup.find_all("img"):
        src = img.get("src", "").lower()
        alt = img.get("alt", "").lower()
        if "fa_" in src or "f-assured" in alt or "plus-icon" in alt or "fassured" in src:
            return True

    for el in soup.select("div._21AAV0, span._3j4x2k, div[class*='_21AAV0']"):
        if el:
            return True

    return False


class CandidateScore:
    """Represents a scored seller candidate."""

    def __init__(
        self,
        name: str,
        score: int,
        source: str,
        rating: Optional[float] = None,
    ) -> None:
        self.name = name
        self.score = score
        self.source = source
        self.rating = rating


def _extract_from_state_json(html_text: str) -> List[CandidateScore]:
    """Extract seller candidates from Flipkart embedded State JSON (window.__INITIAL_STATE__ / DLS).

    Inspects:
      - multiWidgetState / widgetsData / slots / dlsData
      - "Sold By <SellerName>" text values inside widget slots
      - DLS seller_title, seller_details, widget_seller structures
      - sellerName, sellerDisplayName, sellerRating fields

    Args:
        html_text: Raw HTML content.

    Returns:
        List of CandidateScore objects from State JSON.
    """
    candidates: List[CandidateScore] = []

    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});\s*</script>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'window\.__PAGE_DATA__\s*=\s*(\{.*?\});\s*</script>', html_text, re.DOTALL)

    if not m:
        return candidates

    try:
        raw_json_str = m.group(1)
        state = json.loads(raw_json_str)

        # 1. Inspect slots in multiWidgetState
        slots = state.get("multiWidgetState", {}).get("widgetsData", {}).get("slots", [])
        for s in slots:
            widget = s.get("slotData", {}).get("widget", {})
            dls_data = widget.get("data", {}).get("dlsData", {})

            for k, v in dls_data.items():
                if isinstance(v, dict):
                    # Check text fields for "Sold By <SellerName>"
                    for sub_k, sub_v in v.items():
                        if isinstance(sub_v, dict) and "value" in sub_v:
                            val_dict = sub_v["value"]
                            if isinstance(val_dict, dict) and "text" in val_dict:
                                t = val_dict["text"]
                                if isinstance(t, str) and "sold by" in t.lower():
                                    m_sold = re.search(r"(?i)\bSold\s+By\s+([A-Za-z0-9\s.,&\-\(\)]+)", t)
                                    if m_sold:
                                        cand = clean_seller_candidate(m_sold.group(1))
                                        if cand:
                                            # Look for seller rating in same slot/container
                                            rating = None
                                            for rk, rv in v.items():
                                                if isinstance(rv, dict) and "value" in rv and isinstance(rv["value"], dict):
                                                    rtxt = rv["value"].get("text", "")
                                                    if re.match(r"^[1-5]\.[0-9]$", str(rtxt).strip()):
                                                        try:
                                                            rating = float(rtxt.strip())
                                                        except ValueError:
                                                            pass
                                            candidates.append(
                                                CandidateScore(name=cand, score=100, source="state_dls_sold_by", rating=rating)
                                            )

                # Check seller_title / seller_details DLS containers
                if ("seller_title" in k.lower() or "widget_seller" in k.lower()) and isinstance(v, dict):
                    for sub_k, sub_v in v.items():
                        if isinstance(sub_v, dict) and "value" in sub_v:
                            val_dict = sub_v["value"]
                            if isinstance(val_dict, dict) and "text" in val_dict:
                                txt = val_dict["text"]
                                cand = clean_seller_candidate(txt)
                                if cand:
                                    candidates.append(
                                        CandidateScore(name=cand, score=95, source="state_dls_seller_title")
                                    )

        # 2. Fast regex fallback on raw state JSON string if not found in slots
        if not candidates:
            # Match "Sold By <Entity>" in state JSON string
            m_sold_json = re.search(r'["\']text["\']\s*:\s*["\']Sold\s+By\s+([^"\']+)["\']', raw_json_str, re.I)
            if m_sold_json:
                cand = clean_seller_candidate(m_sold_json.group(1))
                if cand:
                    r_match = re.search(r'["\'](?:sellerRating|ratingValue)["\']\s*:\s*([1-5]\.[0-9])', raw_json_str)
                    rating = float(r_match.group(1)) if r_match else None
                    candidates.append(
                        CandidateScore(name=cand, score=95, source="state_json_sold_by", rating=rating)
                    )

            # Match sellerName / sellerDisplayName in state JSON
            m_name_json = re.search(r'["\'](?:sellerName|sellerDisplayName)["\']\s*:\s*["\']([^"\']+)["\']', raw_json_str, re.I)
            if m_name_json:
                cand = clean_seller_candidate(m_name_json.group(1))
                if cand:
                    r_match = re.search(r'["\'](?:sellerRating|ratingValue)["\']\s*:\s*([1-5]\.[0-9])', raw_json_str)
                    rating = float(r_match.group(1)) if r_match else None
                    candidates.append(
                        CandidateScore(name=cand, score=90, source="state_json_seller_name", rating=rating)
                    )

    except Exception as e:
        logger.debug(f"Error parsing state JSON: {e}")

    return candidates


def _extract_from_json_ld(soup: BeautifulSoup) -> List[CandidateScore]:
    """Extract seller from JSON-LD structured data (<script type="application/ld+json">).

    Args:
        soup: Parsed BeautifulSoup object.

    Returns:
        List of CandidateScore objects from JSON-LD.
    """
    candidates: List[CandidateScore] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                offers = item.get("offers")
                offer_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
                for offer in offer_list:
                    if isinstance(offer, dict):
                        seller = offer.get("seller")
                        if isinstance(seller, dict) and seller.get("name"):
                            cand = clean_seller_candidate(seller["name"])
                            if cand:
                                candidates.append(
                                    CandidateScore(name=cand, score=85, source="json_ld")
                                )
                        elif isinstance(seller, str):
                            cand = clean_seller_candidate(seller)
                            if cand:
                                candidates.append(
                                    CandidateScore(name=cand, score=85, source="json_ld")
                                )
        except Exception:
            continue
    return candidates


def _extract_from_html_patterns(html_text: str) -> List[CandidateScore]:
    """Extract seller using regex patterns across raw HTML content.

    Matches:
      - 'Sold By <SellerName>'
      - 'Seller: <SellerName>'

    Args:
        html_text: Raw webpage HTML.

    Returns:
        List of CandidateScore objects from regex.
    """
    candidates: List[CandidateScore] = []

    # 1. "Sold By <Entity>"
    sold_by_matches = re.findall(
        r'(?i)\bSold\s+By\s+([A-Za-z0-9\s.,&\-\(\)]+?)(?:[\s<"\'\\]*(?:Show\s+all|See\s+other|Rating|Ratings|About|Services|Delivery|7\s*Days|10\s*Days|GST|\\n|<|\n|$))',
        html_text,
    )
    for raw in sold_by_matches:
        cand = clean_seller_candidate(raw)
        if cand:
            candidates.append(
                CandidateScore(name=cand, score=85, source="html_sold_by_regex")
            )

    # 2. "Seller: <Entity>"
    seller_colon_matches = re.findall(
        r'(?i)\bSeller\s*:\s*([A-Za-z0-9\s.,&\-\(\)]+?)(?:[\s<"\'\\]*(?:Rating|Ratings|Services|Delivery|Show\s+all|See\s+other|7\s*Days|10\s*Days|GST|\\n|<|\n|$))',
        html_text,
    )
    for raw in seller_colon_matches:
        cand = clean_seller_candidate(raw)
        if cand:
            candidates.append(
                CandidateScore(name=cand, score=85, source="html_seller_regex")
            )

    return candidates


def _extract_from_dom_selectors(soup: BeautifulSoup) -> List[CandidateScore]:
    """Extract seller using DOM selectors on clean soup.

    Args:
        soup: Clean BeautifulSoup object (without header/nav/footer).

    Returns:
        List of CandidateScore objects from DOM.
    """
    candidates: List[CandidateScore] = []

    # 1. Dedicated targeted seller selectors
    SELLER_RATING_BADGE_REGEX = re.compile(r"\b(?:_3LWZlK|_1RLviY|V3C51r|XQDdHH|_1D0tN1|seller_rating|seller-rating)\b", re.I)

    for selector in TARGETED_SELLER_SELECTORS:
        elements = soup.select(selector)
        for el in elements:
            href = el.get("href", "") or (el.parent.get("href", "") if el.parent else "")
            if "seller.flipkart.com" in href:
                continue

            text = el.get_text(separator=" ", strip=True)
            cand = clean_seller_candidate(text)
            if cand:
                # Look for seller rating badge inside element or direct sibling badge (avoiding reviews container)
                badge = None
                for child in el.find_all(class_=SELLER_RATING_BADGE_REGEX):
                    if "review" not in " ".join(child.get("class", [])).lower():
                        badge = child
                        break

                if not badge:
                    # Check immediate next/prev sibling element
                    for sib in list(el.find_next_siblings())[:2] + list(el.find_previous_siblings())[:2]:
                        sib_cls = " ".join(sib.get("class", []))
                        if "review" in sib_cls.lower():
                            continue
                        if SELLER_RATING_BADGE_REGEX.search(sib_cls):
                            badge = sib
                            break
                        # Check inside small sibling container
                        b_inside = sib.find(class_=SELLER_RATING_BADGE_REGEX)
                        if b_inside and "review" not in " ".join(sib.get("class", [])).lower():
                            badge = b_inside
                            break

                rating = extract_rating_from_tag(badge) if badge else None
                candidates.append(
                    CandidateScore(name=cand, score=80, source="dom_targeted_selector", rating=rating)
                )

    # 2. Label inspection in DOM
    seller_labels = soup.find_all(
        lambda t: t.name in ["div", "span", "p", "td", "th"]
        and t.text
        and t.text.strip().lower() in {"seller:", "seller", "sold by:", "sold by"}
    )
    for label in seller_labels:
        parent = label.parent
        if not parent:
            continue

        for sib in label.find_next_siblings():
            sib_text = sib.get_text(separator=" ", strip=True)
            cand = clean_seller_candidate(sib_text)
            if cand:
                badge = None
                for b_cand in sib.find_all(class_=SELLER_RATING_BADGE_REGEX):
                    if "review" not in " ".join(b_cand.get("class", [])).lower():
                        badge = b_cand
                        break
                if not badge:
                    for next_s in list(sib.find_next_siblings())[:2]:
                        sib_cls = " ".join(next_s.get("class", []))
                        if "review" in sib_cls.lower():
                            continue
                        if SELLER_RATING_BADGE_REGEX.search(sib_cls):
                            badge = next_s
                            break
                rating = extract_rating_from_tag(badge) if badge else None
                candidates.append(
                    CandidateScore(name=cand, score=80, source="dom_seller_label_sibling", rating=rating)
                )
                break

    return candidates


def find_seller_candidates_with_scores(
    soup: BeautifulSoup, html_text: str
) -> List[CandidateScore]:
    """Collect all valid seller candidates across strategies and assign confidence scores.

    Strategies evaluated in fallback order:
      1. Embedded State JSON (window.__INITIAL_STATE__ / DLS slots) -> Score 95-100
      2. JSON-LD Structured Data -> Score 85
      3. Raw HTML Regex Patterns ('Sold By <Entity>', 'Seller: <Entity>') -> Score 85
      4. Targeted DOM Selectors & Sibling traversal -> Score 80

    Args:
        soup: Parsed BeautifulSoup object.
        html_text: Raw webpage HTML.

    Returns:
        List of CandidateScore objects sorted by score descending.
    """
    candidates: List[CandidateScore] = []

    # Clean soup of top header / navigation to prevent global "Become a Seller" link
    clean_soup = BeautifulSoup(html_text, "lxml")
    for tag in clean_soup.find_all(["header", "nav", "footer"]):
        tag.decompose()

    # Strategy 1: State JSON (Modern Flipkart DLS / multiWidgetState)
    candidates.extend(_extract_from_state_json(html_text))

    # Strategy 2: JSON-LD Structured Data
    candidates.extend(_extract_from_json_ld(clean_soup))

    # Strategy 3: Raw HTML Regex Patterns
    candidates.extend(_extract_from_html_patterns(html_text))

    # Strategy 4: Targeted DOM Selectors
    candidates.extend(_extract_from_dom_selectors(clean_soup))

    # Deduplicate candidates preserving highest score
    seen_names = set()
    deduped: List[CandidateScore] = []
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        norm = normalize_text(c.name)
        if norm and norm not in seen_names:
            seen_names.add(norm)
            deduped.append(c)

    return deduped


def save_debug_artifact(html_content: str, product_id: str) -> Path:
    """Save rendered HTML locally for inspection when seller extraction fails.

    Args:
        html_content: Raw webpage HTML.
        product_id: Sanitized product ID.

    Returns:
        Path to saved HTML file.
    """
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    sanitized_id = re.sub(r"[^\w\-]", "_", product_id)[:50]
    out_path = DEBUG_DIR / f"product_{sanitized_id}.html"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.debug(f"Saved debug HTML to {out_path}")
    except Exception as e:
        logger.warning(f"Failed to save debug HTML: {e}")
    return out_path


def detect_flipkart_page_status(html_content: str, http_status: int = 200) -> str:
    """Detect whether Flipkart returned a valid product page, CAPTCHA, block, or redirect.

    Statuses:
      - 'PRODUCT_PAGE': Normal product page with content
      - 'CAPTCHA': Robot verification / CAPTCHA challenge
      - 'BLOCKED': Access denied / 403 / 429 rate limit
      - 'REDIRECTED': Page redirected or search fallback
      - 'EMPTY_RESPONSE': Empty or malformed HTML

    Args:
        html_content: Raw HTML text.
        http_status: HTTP status code.

    Returns:
        String status identifier.
    """
    if http_status in [403, 429]:
        return "BLOCKED"
    if http_status >= 500:
        return "REQUEST_FAILED"

    if not html_content or not html_content.strip():
        return "EMPTY_RESPONSE"

    lower = html_content.lower()

    # If it contains product state / multiWidgetState / initial state / ratings, it is a PRODUCT_PAGE
    if "__initial_state__" in lower or "__preloaded_state__" in lower or 'id="sellername"' in lower or "ratings & reviews" in lower or 'class="product' in lower:
        return "PRODUCT_PAGE"

    # Check for actual CAPTCHA challenge in page title or visible text
    if "robot or human" in lower or "please solve this captcha" in lower or "enter the characters you see below" in lower:
        return "CAPTCHA"

    if "access denied" in lower or "you do not have permission to access" in lower or "blocked" in lower:
        return "BLOCKED"

    return "PRODUCT_PAGE"


# ---------------------------------------------------------------------------
# Native Flipkart State-JSON Extraction Helpers
# ---------------------------------------------------------------------------

def _extract_seller_rating_from_state(raw_json_str: str) -> Optional[float]:
    """Hunt specifically for the seller's star rating inside a raw state JSON blob.

    Searches for JSON keys: sellerRating, ratingValue, averageRating, overallRating,
    qualityScore.  Enforces 1.0 <= value <= 5.0 to reject irrelevant numeric hits.

    Args:
        raw_json_str: Raw JSON string (already extracted from the page).

    Returns:
        Float seller rating or None.
    """
    patterns = [
        r'["\'](?:sellerRating|ratingValue|averageRating|overallRating|qualityScore)["\']\s*:\s*"?([0-9](?:\.[0-9]+)?)"?',
        r'["\'](?:sellerRating)["\']\s*:\s*([0-9](?:\.[0-9]+)?)',
    ]
    for pat in patterns:
        m = re.search(pat, raw_json_str, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 1.0 <= val <= 5.0:
                    return round(val, 1)
            except ValueError:
                pass
    return None


def _extract_registered_address_from_state(raw_json_str: str) -> Optional[str]:
    """Search state JSON blob for the seller's registered / dispatch address.

    Attempts the following JSON key patterns in priority order:
      - registeredAddress
      - dispatchAddress  / dispatch_address
      - pickupAddress    / pickup_address
      - sellerLocation   / sellerAddress

    Args:
        raw_json_str: Raw JSON string from page.

    Returns:
        Address string or None.
    """
    address_keys = [
        "registeredAddress", "registered_address",
        "dispatchAddress", "dispatch_address",
        "pickupAddress", "pickup_address",
        "sellerAddress", "seller_address",
        "sellerLocation", "seller_location",
        # Also match the bare "address" key (used in Flipkart page data / tests)
        "address",
    ]
    for key in address_keys:
        escaped = re.escape(key)
        # Two concrete patterns: double-quoted and single-quoted JSON strings.
        # These avoid rf-string brace-escaping issues inside character classes.
        pat_dq = '"' + escaped + r'"\s*:\s*"([^"{}]{5,})"'
        pat_sq = "'" + escaped + r"'\s*:\s*'([^'{}]{5,})'"
        m = re.search(pat_dq, raw_json_str, re.IGNORECASE) or \
            re.search(pat_sq, raw_json_str, re.IGNORECASE)
        if m:
            addr = m.group(1).strip()
            # Must be non-trivial: at least 5 chars and not a URL/path
            if len(addr) >= 5 and "http" not in addr and "/" not in addr[:6]:
                return addr
    return None


def extract_flipkart_seller_metadata(
    soup: Optional[BeautifulSoup], html_text: str
) -> Dict[str, Any]:
    """Extract seller profile URL, location, address, phone number, and GSTIN directly from Flipkart page.

    Checks:
      - Embedded State JSON (window.__INITIAL_STATE__, __PRELOADED_STATE__, __PAGE_DATA__)
      - JSON-LD structured data (<script type="application/ld+json">)
      - Direct DOM links / anchors (seller links, tel: links)
      - Seller details / contact / business details sections
    """
    meta: Dict[str, Any] = {
        "seller_url": None,
        "seller_location": None,
        "city": None,
        "state": None,
        "pincode": None,
        "phone": None,
        "email": None,
        "gst_number": None,
        "raw_address": None,
        # Extended fields populated by deeper state JSON scan
        "seller_rating": None,       # numeric star rating (1.0–5.0)
        "rating_count": None,        # total number of ratings
        "seller_id": None,           # marketplace seller / platform ID
        "marketplace_seller_id": None,
    }

    if not html_text:
        return meta

    # --- Pass 1: regex-match the raw JSON string for a broad set of keys ---
    # We search the full page text (not just parsed JSON) to handle partially
    # broken or truncated JSON blobs that cannot be parsed by json.loads.
    raw_scan = html_text  # full page; parsers below limit scope where possible

    # Seller rating — always try raw scan first (fastest path)
    if not meta["seller_rating"]:
        rating_val = _extract_seller_rating_from_state(raw_scan)
        if rating_val is not None:
            meta["seller_rating"] = rating_val

    # Seller ID / marketplace ID
    for id_key in ["sellerId", "seller_id", "sellerMarketplaceId", "marketplace_seller_id"]:
        m_id = re.search(
            rf'["\'{re.escape(id_key)}["\']\s*:\s*["\']([A-Za-z0-9_\-]+)["\']',
            raw_scan,
            re.IGNORECASE,
        )
        if m_id:
            val_id = m_id.group(1).strip()
            if len(val_id) >= 3:
                if "seller_id" in id_key.lower() and not "marketplace" in id_key.lower():
                    meta["seller_id"] = val_id
                else:
                    meta["marketplace_seller_id"] = val_id
            break

    # Rating count
    m_rc = re.search(
        r'["\'](?:ratingCount|ratingsCount|totalRatings|numRatings|reviewCount)["\']\s*:\s*(\d+)',
        raw_scan,
        re.IGNORECASE,
    )
    if m_rc:
        try:
            meta["rating_count"] = int(m_rc.group(1))
        except ValueError:
            pass

    # Registered / dispatch address from raw scan
    if not meta["seller_location"]:
        addr_str = _extract_registered_address_from_state(raw_scan)
        if addr_str:
            meta["seller_location"] = addr_str
            meta["raw_address"] = addr_str
            addr_parsed = parse_raw_address(addr_str)
            meta["city"] = meta["city"] or addr_parsed.get("city")
            meta["state"] = meta["state"] or addr_parsed.get("state")
            meta["pincode"] = meta["pincode"] or addr_parsed.get("pincode")

    # 1. Inspect State JSON (structured — for URL, phone, email, GST, location)
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})[\s;]*</script>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})[\s;]*</script>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'window\.__PAGE_DATA__\s*=\s*(\{.+?\})[\s;]*</script>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})[\s;]*<', html_text, re.DOTALL)

    if m:
        try:
            raw_json_str = m.group(1)
            # Find seller profile / seller URL
            seller_url_m = re.search(r'["\'](?:sellerUrl|sellerProfileUrl|profileUrl)["\']\s*:\s*["\']([^"\']+)["\']', raw_json_str, re.I)
            if seller_url_m:
                u = seller_url_m.group(1).strip()
                if u.startswith("/"):
                    u = f"https://www.flipkart.com{u}"
                meta["seller_url"] = u

            # Find seller phone in state JSON
            phone_m = re.search(r'["\'](?:sellerPhone|phone|mobile|contactNumber|telephone)["\']\s*:\s*["\']([^"\']+)["\']', raw_json_str, re.I)
            if phone_m:
                valid_p = validate_phone(phone_m.group(1))
                if valid_p:
                    meta["phone"] = valid_p

            # Find seller email in state JSON
            email_m = re.search(r'["\'](?:sellerEmail|email|contactEmail|supportEmail)["\']\s*:\s*["\']([^"\']+)["\']', raw_json_str, re.I)
            if email_m:
                valid_e = validate_email(email_m.group(1))
                if valid_e:
                    meta["email"] = valid_e

            # Find seller GSTIN in state JSON
            gst_m = re.search(r'["\'](?:sellerGst|sellerGSTIN|gstNumber|gstin|gst|taxId|taxIdentification|registrationNumber)["\']\s*:\s*["\']([^"\']+)["\']', raw_json_str, re.I)
            if gst_m:
                valid_g = validate_gst(gst_m.group(1))
                if valid_g:
                    meta["gst_number"] = valid_g

            # Find seller location / address in state JSON
            loc_m = re.search(r'["\'](?:sellerLocation|sellerAddress|location|pickupAddress)["\']\s*:\s*["\']([^"\']+)["\']', raw_json_str, re.I)
            if loc_m:
                loc_str = loc_m.group(1).strip()
                if len(loc_str) >= 2:
                    meta["seller_location"] = loc_str
                    addr_parsed = parse_raw_address(loc_str)
                    if addr_parsed.get("city"):
                        meta["city"] = addr_parsed["city"]
                    if addr_parsed.get("state"):
                        meta["state"] = addr_parsed["state"]
                    if addr_parsed.get("pincode"):
                        meta["pincode"] = addr_parsed["pincode"]
        except Exception:
            pass

    # 2. Inspect JSON-LD
    if soup:
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    offers = item.get("offers")
                    offer_list = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
                    for offer in offer_list:
                        if isinstance(offer, dict):
                            seller = offer.get("seller")
                            if isinstance(seller, dict):
                                if not meta["seller_url"] and seller.get("url"):
                                    su = seller["url"]
                                    if su.startswith("/"):
                                        su = f"https://www.flipkart.com{su}"
                                    meta["seller_url"] = su
                                if not meta["phone"] and seller.get("telephone"):
                                    valid_p = validate_phone(seller["telephone"])
                                    if valid_p:
                                        meta["phone"] = valid_p
                                if not meta["email"] and seller.get("email"):
                                    valid_e = validate_email(seller["email"])
                                    if valid_e:
                                        meta["email"] = valid_e
                                if not meta["gst_number"]:
                                    for gst_k in ["taxID", "vatID", "identifier", "gst", "gstin"]:
                                        if seller.get(gst_k):
                                            valid_g = validate_gst(str(seller[gst_k]))
                                            if valid_g:
                                                meta["gst_number"] = valid_g
                                                break
                                if seller.get("address"):
                                    addr = seller["address"]
                                    if isinstance(addr, dict):
                                        meta["city"] = meta["city"] or addr.get("addressLocality")
                                        meta["state"] = meta["state"] or addr.get("addressRegion")
                                        meta["pincode"] = meta["pincode"] or addr.get("postalCode")
                                        if not meta["seller_location"]:
                                            parts = [addr.get("streetAddress"), addr.get("addressLocality"), addr.get("addressRegion"), addr.get("postalCode")]
                                            meta["seller_location"] = ", ".join([p for p in parts if p])
            except Exception:
                pass

    # 3. Inspect DOM Links & Text
    if soup:
        # Check tel: links
        if not meta["phone"]:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if href.startswith("tel:"):
                    raw_tel = href.replace("tel:", "").strip()
                    valid_p = validate_phone(raw_tel)
                    if valid_p:
                        meta["phone"] = valid_p
                        break

        # Check mailto: links
        if not meta["email"]:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if href.startswith("mailto:"):
                    raw_mailto = href.replace("mailto:", "").split("?")[0].strip()
                    valid_e = validate_email(raw_mailto)
                    if valid_e:
                        meta["email"] = valid_e
                        break

        # Check seller link href
        if not meta["seller_url"]:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if ("/seller" in href or "/sellers" in href) and "seller.flipkart.com" not in href:
                    full_href = href if href.startswith("http") else f"https://www.flipkart.com{href}"
                    meta["seller_url"] = full_href
                    break

        # Check visible phone numbers in seller / contact section
        if not meta["phone"]:
            for seller_el in soup.find_all(lambda t: t.name in ["div", "span", "p", "section"] and t.text and ("seller details" in t.text.lower() or "contact seller" in t.text.lower() or "sold by" in t.text.lower())):
                stext = seller_el.get_text(" ", strip=True)
                ph_matches = PHONE_REGEX.findall(stext)
                for ph in ph_matches:
                    valid_p = validate_phone(ph)
                    if valid_p and not any(valid_p.startswith(pfx) for pfx in ["1800", "01800", "911800"]):
                        meta["phone"] = valid_p
                        break
                if meta["phone"]:
                    break

        # Check visible email in seller / contact section
        if not meta["email"]:
            for seller_el in soup.find_all(lambda t: t.name in ["div", "span", "p", "section"] and t.text and ("seller details" in t.text.lower() or "contact seller" in t.text.lower() or "sold by" in t.text.lower() or "grievance" in t.text.lower())):
                stext = seller_el.get_text(" ", strip=True)
                em_matches = EMAIL_REGEX.findall(stext)
                for em in em_matches:
                    valid_e = validate_email(em)
                    if valid_e:
                        meta["email"] = valid_e
                        break
                if meta["email"]:
                    break

        # Check visible location in seller / contact section
        if not meta["seller_location"]:
            for seller_el in soup.find_all(lambda t: t.name in ["div", "span", "p", "section"] and t.text and any(k in t.text.lower() for k in ["location:", "address:", "seller details", "sold by"])):
                stext = seller_el.get_text(" ", strip=True)
                loc_m = re.search(r"(?:Location|Address)\s*[:\-]\s*([A-Za-z0-9\s,.\-]+)", stext, re.I)
                if loc_m:
                    loc_val = loc_m.group(1).strip()
                    if len(loc_val) >= 3:
                        meta["seller_location"] = loc_val
                        parsed_loc = parse_raw_address(loc_val)
                        if parsed_loc.get("city"):
                            meta["city"] = parsed_loc["city"]
                        if parsed_loc.get("state"):
                            meta["state"] = parsed_loc["state"]
                        if parsed_loc.get("pincode"):
                            meta["pincode"] = parsed_loc["pincode"]
                        break

        # Check visible GSTIN in seller / contact section
        if not meta["gst_number"]:
            for seller_el in soup.find_all(lambda t: t.name in ["div", "span", "p", "section", "tr", "td", "li"] and t.text and any(k in t.text.lower() for k in ["seller details", "contact seller", "sold by", "gstin", "gst no", "gst number", "tax id", "registration"])):
                stext = seller_el.get_text(" ", strip=True)
                gst_matches = GST_REGEX.findall(stext)
                for gm in gst_matches:
                    valid_g = validate_gst(gm)
                    if valid_g:
                        meta["gst_number"] = valid_g
                        break
                if meta["gst_number"]:
                    break

    return meta


def parse_product_page(
    html_content: str, page_url: str = "", http_status: int = 200
) -> Dict[str, Any]:
    """Extract seller name, fulfillment seller, ratings, location, and phone from product page HTML.

    Distinguishes:
      - CASE A: Page successfully loaded, seller genuinely unavailable -> Seller = NOT_FOUND
      - CASE B: Blocked / CAPTCHA -> Extraction Status = BLOCKED / CAPTCHA
      - CASE C: HTTP failure -> Extraction Status = REQUEST_FAILED
      - CASE D: Redirected -> Extraction Status = REDIRECTED
      - CASE E: Seller exists -> Extract Seller Name, Fulfilled By, Rating, Location, Phone

    Args:
        html_content: Raw HTML text of the Flipkart product page.
        page_url: Product page URL for logging context.
        http_status: HTTP response status code.

    Returns:
        Structured Dict containing seller_name, fulfilled_by_seller, star_rating, status, etc.
    """
    page_status = detect_flipkart_page_status(html_content, http_status=http_status)

    soup = BeautifulSoup(html_content, "lxml")

    # Native Flipkart extraction (State JSON + Scoped DOM elements)
    native_data = extract_seller_and_ratings(soup, html_content)

    # Step 1: Extract Fulfillment Entity separately
    fulfilled_by_seller = extract_fulfillment_seller(soup, html_content)
    is_f_assured = native_data.get("is_f_assured") or extract_is_f_assured(soup, html_content)

    # Step 2: Extract Product Rating details separately (from Ratings & Reviews / JSON-LD / DOM)
    product_rating, product_rating_count, product_review_count = extract_product_rating_details(soup, html_content)
    if native_data.get("product_rating") is not None:
        product_rating = native_data["product_rating"]

    # Step 3: Extract and score all valid seller candidates
    candidates = find_seller_candidates_with_scores(soup, html_content)

    # Step 4: Extract metadata (seller_url, seller_location, city, state, pincode, phone) directly from Flipkart page
    seller_meta = extract_flipkart_seller_metadata(soup, html_content)

    # Pick highest scoring seller candidate
    selected_candidate: Optional[CandidateScore] = candidates[0] if candidates else None

    # Determine primary seller_name
    seller_name: Optional[str] = native_data.get("seller_name")
    seller_rating: Optional[float] = native_data.get("seller_rating")
    seller_source: Optional[str] = "native_state_json" if seller_name else None
    seller_confidence = 1.0 if seller_name else 0.0

    if not seller_name:
        if selected_candidate:
            seller_name = selected_candidate.name
            seller_rating = selected_candidate.rating
            seller_source = selected_candidate.source
            seller_confidence = selected_candidate.score / 100.0
        elif fulfilled_by_seller:
            # Fallback to fulfillment seller if no explicit Seller: label
            seller_name = fulfilled_by_seller
            seller_source = "fulfillment_label"
            seller_confidence = 0.85

    # Final seller rating resolution
    final_seller_rating = native_data.get("seller_rating") or seller_rating or seller_meta.get("seller_rating")
    if final_seller_rating is not None:
        try:
            final_seller_rating = round(float(final_seller_rating), 1)
        except (ValueError, TypeError):
            final_seller_rating = None

    # Harmonize native address and metadata
    final_billing_addr = native_data.get("billing_address") or seller_meta.get("raw_address") or seller_meta.get("seller_location")
    final_city = native_data.get("city") or seller_meta.get("city")
    final_state = native_data.get("state") or seller_meta.get("state")
    final_pincode = native_data.get("pincode") or seller_meta.get("pincode")
    final_gst = native_data.get("gst_number") or seller_meta.get("gst_number")
    final_legal_name = native_data.get("legal_name")

    # Build list of unique seller values found
    seller_values_found: List[str] = []
    if fulfilled_by_seller and fulfilled_by_seller not in seller_values_found:
        seller_values_found.append(fulfilled_by_seller)
    if seller_name and seller_name not in seller_values_found:
        seller_values_found.append(seller_name)
    if final_legal_name and final_legal_name not in seller_values_found:
        seller_values_found.append(final_legal_name)

    # Diagnostic logging
    has_json_ld = bool(soup.find("script", type="application/ld+json"))
    has_next_data = "__INITIAL_STATE__" in html_content or "__PRELOADED_STATE__" in html_content or "__NEXT_DATA__" in html_content
    has_seller_json = bool(native_data.get("seller_name") or native_data.get("gst_number")) or any(c.source.startswith("state_") or c.source == "json_ld" for c in candidates)
    has_seller_html = any(c.source.startswith("html_") or c.source.startswith("dom_") for c in candidates)

    logger.info(
        f"SELLER EXTRACTION\n"
        f"URL: {page_url}\n"
        f"Flipkart Response: {page_status}\n"
        f"HTTP Status: {http_status}\n"
        f"Page Length: {len(html_content)}\n"
        f"JSON-LD Found: {'YES' if has_json_ld else 'NO'}\n"
        f"NEXT_DATA Found: {'YES' if has_next_data else 'NO'}\n"
        f"Seller JSON Found: {'YES' if has_seller_json else 'NO'}\n"
        f"Seller HTML Found: {'YES' if has_seller_html else 'NO'}\n"
        f"Extracted Seller: {seller_name or 'NOT_FOUND'}\n"
        f"Legal Name: {final_legal_name or 'N/A'}\n"
        f"Fulfilled By Seller: {fulfilled_by_seller or 'N/A'}\n"
        f"F-Assured: {'YES' if is_f_assured else 'NO'}\n"
        f"Product Rating: {product_rating if product_rating is not None else 'N/A'}\n"
        f"Seller Rating: {final_seller_rating if final_seller_rating is not None else 'N/A'}\n"
        f"Flipkart GST: {final_gst or 'NOT_FOUND'}\n"
        f"Flipkart Phone: {seller_meta.get('phone') or 'NOT_FOUND'}\n"
        f"Flipkart Location: {final_billing_addr or 'NOT_FOUND'}"
    )

    if not seller_name:
        product_id = page_url.split("/p/")[-1].split("?")[0] if "/p/" in page_url else "unknown"
        save_debug_artifact(html_content, product_id)
        if page_status == "PRODUCT_PAGE":
            logger.warning(
                f"Extracted Seller: 'NOT_FOUND'\n"
                f"Fulfilled By Seller: '{fulfilled_by_seller or 'N/A'}'\n"
                f"Rating: N/A"
            )

    return {
        "seller_name": seller_name or "",
        "legal_name": final_legal_name,
        "fulfilled_by_seller": fulfilled_by_seller,
        "fulfillment_by": fulfilled_by_seller,
        "is_f_assured": is_f_assured,
        "seller_values_found": seller_values_found,
        # Rating: prefer candidate-scored rating, fall back to state-JSON rating
        "star_rating": final_seller_rating,
        "seller_rating": final_seller_rating,
        "rating_count": seller_meta.get("rating_count") or product_rating_count,
        "product_rating": product_rating,
        "product_rating_count": product_rating_count,
        "product_review_count": product_review_count,
        "seller_source": seller_source,
        "seller_name_source": seller_source,
        "rating_source": "seller_section" if final_seller_rating else ("state_json" if seller_meta.get("seller_rating") else None),
        "seller_confidence": seller_confidence,
        "rating_confidence": 0.95 if final_seller_rating else 0.0,
        "page_status": page_status,
        "seller_url": seller_meta.get("seller_url"),
        "seller_location": final_billing_addr,
        "raw_address": final_billing_addr,
        "billing_address": final_billing_addr,
        "city": final_city,
        "state": final_state,
        "pincode": final_pincode,
        "contact_number": seller_meta.get("phone"),
        "phone": seller_meta.get("phone"),
        "email": seller_meta.get("email"),
        "gst_number": final_gst,
        "gst": final_gst,
        # Extended identifiers
        "seller_id": seller_meta.get("seller_id"),
        "marketplace_seller_id": seller_meta.get("marketplace_seller_id"),
    }
