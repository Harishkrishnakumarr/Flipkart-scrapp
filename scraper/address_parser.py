"""Address parsing, normalization, and validation module for Indian addresses."""

import html
import re
import urllib.parse
from typing import Dict, List, Optional, Tuple

from scraper.config import GST_STATE_CODES, INDIAN_STATES, MAJOR_INDIAN_CITIES
from scraper.validator import (
    PINCODE_REGEX,
    calculate_seller_match_score,
    is_generic_seller_name,
    normalize_seller_name_for_matching,
    validate_pincode,
)


def normalize_address_text(raw_address: Optional[str]) -> str:
    """Safely normalize address text without destroying useful information.

    Handles:
      - HTML entity decoding (&amp;, &#39;, &quot;, etc.)
      - Line breaks and HTML <br> tags conversion to comma separators
      - Collapsing redundant whitespace and multiple commas
      - Stripping leading/trailing punctuation

    Args:
        raw_address: Raw address string.

    Returns:
        Cleaned, normalized address string.
    """
    if not raw_address:
        return ""

    text = str(raw_address).strip()

    # 1. Decode HTML entities
    text = html.unescape(text)

    # 2. Replace HTML line break tags with commas
    text = re.sub(r"(?i)<br\s*/?>", ", ", text)
    text = re.sub(r"(?i)</?p>", ", ", text)
    text = re.sub(r"(?i)</?div>", ", ", text)

    # 3. Normalize newline and carriage return characters
    text = re.sub(r"[\r\n\t]+", ", ", text)

    # 4. Clean repeated commas and spaces
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r",(\s*,)+", ", ", text)
    text = re.sub(r"\s+", " ", text)

    # 5. Clean leading/trailing commas and hyphens
    text = text.strip(" ,-–—\t\r\n")

    return text


def match_state_from_text(text: str, gst_number: Optional[str] = None) -> Optional[str]:
    """Identify the standardized Indian State or UT name from raw text.

    Args:
        text: Raw text containing address.
        gst_number: Optional GSTIN to aid state deduction.

    Returns:
        Standardized state name (e.g., 'Maharashtra', 'Karnataka') or None.
    """
    if not text:
        if gst_number and len(gst_number) >= 2:
            st_code = gst_number[:2]
            if st_code in GST_STATE_CODES:
                return GST_STATE_CODES[st_code]
        return None

    text_lower = text.lower()

    # 1. Direct State / UT match (multi-word first)
    for state_name, aliases in INDIAN_STATES.items():
        # Check full state name first
        if re.search(r"\b" + re.escape(state_name.lower()) + r"\b", text_lower):
            return state_name

        # Check aliases
        for alias in aliases:
            # For 2-letter state codes, enforce strict word boundaries
            if len(alias) <= 2:
                pattern = r"(?:^|[,\s\-])" + re.escape(alias) + r"(?:[,\s\-\d]|$)"
            else:
                pattern = r"\b" + re.escape(alias) + r"\b"
            if re.search(pattern, text_lower):
                return state_name

    # 2. City fallback match to identify state
    for city, state_name in MAJOR_INDIAN_CITIES.items():
        pattern = r"\b" + re.escape(city) + r"\b"
        if re.search(pattern, text_lower):
            return state_name

    # 3. GST state code fallback
    if gst_number and len(gst_number) >= 2:
        st_code = gst_number[:2]
        if st_code in GST_STATE_CODES:
            return GST_STATE_CODES[st_code]

    return None


def extract_city_from_text(text: str, identified_state: Optional[str] = None) -> Optional[str]:
    """Extract Indian city name from address text.

    Args:
        text: Raw address text.
        identified_state: Optional known state to refine matching.

    Returns:
        Extracted city name capitalized or None.
    """
    if not text:
        return None

    text_clean = normalize_address_text(text)
    text_lower = text_clean.lower()

    # Strategy 1: Check known Indian cities filtered by identified state
    if identified_state:
        for city, state_name in MAJOR_INDIAN_CITIES.items():
            if state_name.lower() == identified_state.lower():
                pattern = r"\b" + re.escape(city) + r"\b"
                if re.search(pattern, text_lower):
                    return city.title()

    # Strategy 2: Check all known cities
    for city, _ in MAJOR_INDIAN_CITIES.items():
        pattern = r"\b" + re.escape(city) + r"\b"
        if re.search(pattern, text_lower):
            return city.title()

    # Strategy 3: Token immediately preceding the identified state
    # e.g., "164/1 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002" -> "Thoothukudi"
    if identified_state:
        state_pattern = r",\s*([A-Za-z\s]+?)\s*,\s*(?:[A-Za-z\s]*\b)?" + re.escape(identified_state)
        match_before_state = re.search(state_pattern, text_clean, re.IGNORECASE)
        if match_before_state:
            cand = match_before_state.group(1).strip()
            if len(cand) > 2 and len(cand.split()) <= 3 and cand.lower() != identified_state.lower():
                # Avoid street suffixes in city
                if not any(cand.lower().endswith(s) for s in ["street", "road", "marg", "nagar", "lane", "building", "estate"]):
                    return cand.title()

    # Strategy 4: Comma-separated token before 6-digit pincode
    # e.g., "Road, Andheri East, Mumbai, Maharashtra 400069"
    match_pin = re.search(r",\s*([A-Za-z\s]+?)\s*(?:,\s*[A-Za-z\s]+)?\s*[-,\s]+\b[1-9][0-9]{5}\b", text_clean)
    if match_pin:
        candidate = match_pin.group(1).strip()
        if len(candidate) > 2 and len(candidate.split()) <= 3:
            if identified_state and candidate.lower() == identified_state.lower():
                pass
            else:
                if not any(candidate.lower().endswith(s) for s in ["street", "road", "marg", "lane", "building", "estate"]):
                    return candidate.title()

    # Strategy 5: Comma-separated chunks inspection
    chunks = [c.strip() for c in text_clean.split(",") if c.strip()]
    if len(chunks) >= 2:
        # Check second-to-last or last non-state chunk
        for ch in reversed(chunks):
            ch_clean = re.sub(r"\b[1-9][0-9]{5}\b", "", ch).strip()
            ch_clean = re.sub(r"(?i)\bIndia\b", "", ch_clean).strip()
            if identified_state and ch_clean.lower() == identified_state.lower():
                continue
            if len(ch_clean) >= 3 and len(ch_clean.split()) <= 2 and not any(char.isdigit() for char in ch_clean):
                return ch_clean.title()

    return None


def extract_pincode_from_text(text: str) -> Optional[str]:
    """Extract a valid 6-digit Indian postal code from address text.

    Args:
        text: Raw address text.

    Returns:
        6-digit string or None.
    """
    if not text:
        return None

    # Find 6-digit numbers starting 1-9
    matches = PINCODE_REGEX.findall(text)
    for m in matches:
        # Check that it is not part of a phone number (e.g. +91 9876543210 or 022-28345678)
        phone_context = re.search(r"(?:\+91|tel|phone|mob|contact)[\s:\-]*\b" + re.escape(m), text, re.I)
        if phone_context:
            continue
        valid = validate_pincode(m)
        if valid:
            return valid

    return None


def parse_raw_address(
    raw_address: Optional[str], gst_number: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """Normalize a raw address string into structured components.

    Output format:
      - billing_address: Full complete normalized address
      - shipping_address: Full complete normalized address
      - city: Separated city name
      - state: Separated state name
      - pincode: 6-digit postal code
      - country: 'India'

    Args:
        raw_address: Raw address string extracted from website or registry.
        gst_number: Optional GSTIN to aid state deduction.

    Returns:
        Dictionary with structured address keys.
    """
    result: Dict[str, Optional[str]] = {
        "billing_address": None,
        "shipping_address": None,
        "city": None,
        "state": None,
        "pincode": None,
        "country": "India",
    }

    if not raw_address or not str(raw_address).strip():
        # If no raw address is available, but GSTIN is, resolve state
        if gst_number and len(gst_number) >= 2:
            state_code = gst_number[:2]
            if state_code in GST_STATE_CODES:
                result["state"] = GST_STATE_CODES[state_code]
        return result

    cleaned_addr = normalize_address_text(raw_address)

    # 1. Extract Pincode
    pincode = extract_pincode_from_text(cleaned_addr)

    # 2. Extract State
    state = match_state_from_text(cleaned_addr, gst_number=gst_number)

    # 3. Extract City
    city = extract_city_from_text(cleaned_addr, identified_state=state)

    # 4. Fallback: if city identified state, but state was not resolved
    if not state and city and city.lower() in MAJOR_INDIAN_CITIES:
        state = MAJOR_INDIAN_CITIES[city.lower()]

    result["billing_address"] = cleaned_addr
    result["shipping_address"] = cleaned_addr
    result["city"] = city
    result["state"] = state
    result["pincode"] = pincode
    result["country"] = "India"

    return result


def validate_address_consistency(
    billing_address: Optional[str],
    city: Optional[str],
    state: Optional[str],
    pincode: Optional[str],
    country: Optional[str] = "India",
) -> Tuple[bool, str]:
    """Validate that billing address, city, state, and pincode are internally consistent.

    Args:
        billing_address: Full normalized billing address.
        city: Extracted city.
        state: Extracted state.
        pincode: Extracted pincode.
        country: Extracted country.

    Returns:
        Tuple of (is_consistent, reason).
    """
    if not billing_address:
        return True, "EMPTY_ADDRESS"

    addr_lower = billing_address.lower()

    # Check city consistency
    if city and city.lower() not in addr_lower:
        # Check if known city in major cities
        if city.lower() in MAJOR_INDIAN_CITIES:
            state_for_city = MAJOR_INDIAN_CITIES[city.lower()]
            if state and state.lower() != state_for_city.lower():
                return False, f"CITY_STATE_CONFLICT: City '{city}' belongs to '{state_for_city}', not '{state}'"

    # Check state consistency
    if state and state.lower() not in addr_lower:
        # Check if abbreviation exists in address
        state_aliases = INDIAN_STATES.get(state, [])
        if not any(re.search(r"\b" + re.escape(a) + r"\b", addr_lower) for a in state_aliases):
            return False, f"STATE_MISMATCH: State '{state}' not represented in billing address"

    # Check pincode consistency
    if pincode:
        if pincode not in billing_address:
            return False, f"PINCODE_MISMATCH: Pincode '{pincode}' not found in billing address text"

    return True, "CONSISTENT"


def extract_address_candidates_from_text(text: str) -> List[str]:
    """Extract candidate address snippets from raw text, HTML, or search snippets.

    Args:
        text: Raw text string.

    Returns:
        List of candidate normalized address strings.
    """
    if not text:
        return []

    candidates: List[str] = []

    # 1. Labeled address blocks
    labeled_patterns = [
        r"(?i)(?:Address|Registered Office|Regd\.?\s*Office|Billing Address|Corporate Office|Office Address|Head Office|Location|Store Address)[\s:\-]+([^\n\r<>\t]{15,250})",
        r"(?i)(?:Shop|Plot|Flat|Unit|Door|Building|Tower|Premises|Survey|Sector|Block|GIDC|MIDC|Phase|Road|Street|Marg|Lane|Nagar|Colony|Enclave|Bazaar|Estate|Industrial Area)[^.\n<>\t]{10,220}?(?:[1-9][0-9]{5}|Tamil Nadu|Maharashtra|Gujarat|Karnataka|Delhi|Kerala|Uttar Pradesh|Telangana|West Bengal|Rajasthan|Madhya Pradesh|Punjab|Haryana|Andhra Pradesh|Bihar|Odisha|Assam|Jharkhand|Goa|India)",
    ]

    for pat in labeled_patterns:
        for m in re.finditer(pat, text):
            cand = m.group(1) if m.groups() else m.group(0)
            norm = normalize_address_text(cand)
            if len(norm) >= 15 and any(c.isalpha() for c in norm):
                candidates.append(norm)

    # 2. Window around 6-digit Indian pincode
    pin_matches = list(PINCODE_REGEX.finditer(text))
    for pm in pin_matches:
        start_idx = max(0, pm.start() - 100)
        end_idx = min(len(text), pm.end() + 30)
        window = text[start_idx:end_idx]
        # Clean leading noise up to first delimiter or capitalization
        norm_w = normalize_address_text(window)
        if len(norm_w) >= 15:
            candidates.append(norm_w)

    # 3. Whole text if it looks like a clean single address
    norm_full = normalize_address_text(text)
    if 15 <= len(norm_full) <= 300:
        if any(w in norm_full.lower() for w in ["street", "road", "marg", "nagar", "building", "floor", "plot", "shop", "estate", "india"]) or PINCODE_REGEX.search(norm_full):
            candidates.append(norm_full)

    # Deduplicate while preserving order
    deduped: List[str] = []
    seen = set()
    for c in candidates:
        c_clean = c.strip(" ,.-")
        if c_clean and c_clean.lower() not in seen:
            seen.add(c_clean.lower())
            deduped.append(c_clean)

    return deduped


def match_address_to_seller(
    seller_name: str,
    address_text: str,
    url: Optional[str] = None,
    snippet: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    location: Optional[str] = None,
    pincode: Optional[str] = None,
    gst_number: Optional[str] = None,
) -> Tuple[bool, int, str]:
    """Score and validate address candidate against target seller identity.

    Args:
        seller_name: Target seller business name.
        address_text: Candidate address string.
        url: Optional result URL.
        snippet: Optional search result snippet or full context.
        city: Optional known anchor city.
        state: Optional known anchor state.
        location: Optional known anchor location.
        pincode: Optional known anchor pincode.
        gst_number: Optional verified GSTIN.

    Returns:
        Tuple of (is_accepted, confidence_score, decision_reason).
    """
    from scraper.validator import (
        calculate_seller_match_score,
        is_generic_seller_name,
    )

    if not address_text:
        return False, 0, "EMPTY_ADDRESS"

    addr_clean = normalize_address_text(address_text)
    addr_lower = addr_clean.lower()
    full_text = f"{snippet or ''} {addr_clean}"

    # 1. Reject marketplace offices / logistics hubs unless verified target seller
    marketplace_hubs = [
        "flipkart internet", "amazon seller services", "ekart logistics",
        "instakart", "courier hub", "warehouse return", "return center",
        "logistics hub", "fulfillment center", "delhivery hub", "e-kart logistics"
    ]
    if any(hub in addr_lower for hub in marketplace_hubs):
        # Only accept if seller name is explicitly part of the text
        if not seller_name or seller_name.lower() not in full_text.lower():
            return False, 0, "MARKETPLACE_OR_LOGISTICS_HUB"

    # 2. Parse candidate components
    parsed = parse_raw_address(addr_clean, gst_number=gst_number)
    cand_city = parsed.get("city")
    cand_state = parsed.get("state")
    cand_pincode = parsed.get("pincode")

    score = 40  # Base score for valid address structure

    # 3. Seller Name Match Score
    name_score, _, _ = calculate_seller_match_score(seller_name, full_text, url)
    score += int(name_score * 0.4)

    # 3b. Domain match bonus
    has_domain = False
    if url:
        try:
            domain = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
            norm_seller = normalize_seller_name_for_matching(seller_name)
            seller_compact = norm_seller.get("compact_stripped", "")
            if domain and seller_compact and (seller_compact in domain or domain.split(".")[0] in seller_compact):
                score += 35
                has_domain = True
            elif any(t in domain for t in norm_seller.get("tokens", []) if len(t) > 3):
                score += 25
                has_domain = True
        except Exception:
            pass

    # 4. City validation
    has_city_match = False
    if city:
        city_lower = city.lower()
        if cand_city and cand_city.lower() == city_lower:
            score += 25
            has_city_match = True
        elif city_lower in addr_lower or city_lower in full_text.lower():
            score += 20
            has_city_match = True
        elif cand_city and cand_city.lower() in MAJOR_INDIAN_CITIES:
            # Different major city detected
            if cand_city.lower() != city_lower:
                # Strong conflict -> Reject
                return False, 0, f"CITY_MISMATCH: expected '{city}', found '{cand_city}'"
    elif location:
        loc_lower = location.lower()
        if loc_lower in addr_lower or loc_lower in full_text.lower():
            score += 20
            has_city_match = True

    # 5. State validation
    has_state_match = False
    if state:
        state_lower = state.lower()
        if cand_state and cand_state.lower() == state_lower:
            score += 15
            has_state_match = True
        elif state_lower in addr_lower or state_lower in full_text.lower():
            score += 15
            has_state_match = True
        else:
            state_aliases = INDIAN_STATES.get(state, [])
            if any(re.search(r"\b" + re.escape(a) + r"\b", addr_lower) for a in state_aliases):
                score += 15
                has_state_match = True
            elif cand_state and cand_state.lower() != state_lower:
                score -= 30
    elif gst_number and len(gst_number) >= 2:
        gst_st = GST_STATE_CODES.get(gst_number[:2], "").lower()
        if gst_st and (gst_st in addr_lower or (cand_state and cand_state.lower() == gst_st)):
            score += 20
            has_state_match = True

    # 6. Pincode validation
    if pincode:
        if cand_pincode and cand_pincode == pincode:
            score += 20
        elif pincode in full_text:
            score += 15

    # 7. Generic seller name protection
    if is_generic_seller_name(seller_name):
        if not (has_city_match or has_state_match or has_domain or (gst_number and gst_number in full_text)):
            return False, 0, f"GENERIC_NAME_INSUFFICIENT_EVIDENCE: '{seller_name}' requires location, domain, or GST match"
        if name_score < 40 and not (has_city_match or has_domain):
            return False, 0, f"GENERIC_NAME_LOW_MATCH: '{seller_name}' requires strong evidence"

    # 8. Length / completeness validation
    if len(addr_clean) < 15:
        return False, 0, "ADDRESS_TOO_SHORT"

    # Score clamping and acceptance decision
    score = min(100, max(0, score))
    if score >= 40:
        return True, score, "VERIFIED_ADDRESS"

    return False, score, f"LOW_CONFIDENCE_SCORE_{score}"

