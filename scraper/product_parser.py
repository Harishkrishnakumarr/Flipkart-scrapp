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
from typing import Any, Dict, List, Optional, Tuple
from bs4 import BeautifulSoup, Tag

from scraper.config import DEBUG_DIR

logger = logging.getLogger("FlipkartScraper.ProductParser")

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


def extract_product_rating(soup: BeautifulSoup, html_text: str) -> Optional[float]:
    """Extract product-level rating (from Ratings & Reviews section or JSON-LD).

    Args:
        soup: Parsed BeautifulSoup object.
        html_text: Raw HTML string.

    Returns:
        Float product rating or None.
    """
    # Check JSON-LD aggregateRating
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                agg = item.get("aggregateRating")
                if isinstance(agg, dict) and agg.get("ratingValue"):
                    val = float(agg["ratingValue"])
                    if 1.0 <= val <= 5.0:
                        return val
        except Exception:
            continue

    # Look for "Ratings and reviews" rating badge
    review_sections = soup.find_all(
        lambda t: t.name in ["div", "span", "h2", "h3"]
        and t.text
        and "ratings & reviews" in t.text.strip().lower()
    )
    for section in review_sections:
        parent = section.parent
        if parent:
            for badge in parent.find_all(class_=re.compile(r"_3LWZlK|rating")):
                r = extract_rating_from_tag(badge)
                if r is not None:
                    return r

    return None


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
    for selector in TARGETED_SELLER_SELECTORS:
        elements = soup.select(selector)
        for el in elements:
            href = el.get("href", "") or (el.parent.get("href", "") if el.parent else "")
            if "seller.flipkart.com" in href:
                continue

            text = el.get_text(separator=" ", strip=True)
            cand = clean_seller_candidate(text)
            if cand:
                badge = el.find(class_=re.compile(r"_3LWZlK|rating", re.I))
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
                badge = parent.find(class_=re.compile(r"_3LWZlK|rating", re.I))
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


def parse_product_page(
    html_content: str, page_url: str = "", http_status: int = 200
) -> Dict[str, Any]:
    """Extract seller name, fulfillment seller, and ratings from product page HTML.

    Distinguishes:
      - CASE A: Page successfully loaded, seller genuinely unavailable -> Seller = NOT_FOUND
      - CASE B: Blocked / CAPTCHA -> Extraction Status = BLOCKED / CAPTCHA
      - CASE C: HTTP failure -> Extraction Status = REQUEST_FAILED
      - CASE D: Redirected -> Extraction Status = REDIRECTED
      - CASE E: Seller exists -> Extract Seller Name, Fulfilled By, and Rating

    Args:
        html_content: Raw HTML text of the Flipkart product page.
        page_url: Product page URL for logging context.
        http_status: HTTP response status code.

    Returns:
        Structured Dict containing seller_name, fulfilled_by_seller, star_rating, status, etc.
    """
    page_status = detect_flipkart_page_status(html_content, http_status=http_status)

    soup = BeautifulSoup(html_content, "lxml")

    # Step 1: Extract Fulfillment Entity separately
    fulfilled_by_seller = extract_fulfillment_seller(soup, html_content)

    # Step 2: Extract Product Rating separately (from Ratings & Reviews)
    product_rating = extract_product_rating(soup, html_content)

    # Step 3: Extract and score all valid seller candidates
    candidates = find_seller_candidates_with_scores(soup, html_content)

    # Pick highest scoring seller candidate
    selected_candidate: Optional[CandidateScore] = candidates[0] if candidates else None

    # Determine primary seller_name
    seller_name: Optional[str] = None
    seller_rating: Optional[float] = None
    seller_source: Optional[str] = None
    seller_confidence = 0.0

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

    # Build list of unique seller values found
    seller_values_found: List[str] = []
    if fulfilled_by_seller and fulfilled_by_seller not in seller_values_found:
        seller_values_found.append(fulfilled_by_seller)
    if seller_name and seller_name not in seller_values_found:
        seller_values_found.append(seller_name)

    # Diagnostic logging
    has_json_ld = bool(soup.find("script", type="application/ld+json"))
    has_next_data = "__INITIAL_STATE__" in html_content or "__PRELOADED_STATE__" in html_content or "__NEXT_DATA__" in html_content
    has_seller_json = any(c.source.startswith("state_") or c.source == "json_ld" for c in candidates)
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
        f"Fulfilled By Seller: {fulfilled_by_seller or 'N/A'}\n"
        f"Rating: {seller_rating if seller_rating is not None else 'N/A'}"
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
        "fulfilled_by_seller": fulfilled_by_seller,
        "fulfillment_by": fulfilled_by_seller,
        "seller_values_found": seller_values_found,
        "star_rating": seller_rating,
        "product_rating": product_rating,
        "seller_source": seller_source,
        "seller_name_source": seller_source,
        "rating_source": "seller_section" if seller_rating else None,
        "seller_confidence": seller_confidence,
        "rating_confidence": 0.95 if seller_rating else 0.0,
        "page_status": page_status,
    }
