"""Address parsing and normalization module for Indian addresses."""

import re
from typing import Dict, Optional, Tuple

from scraper.config import GST_STATE_CODES, INDIAN_STATES, MAJOR_INDIAN_CITIES
from scraper.validator import validate_pincode


def match_state_from_text(text: str) -> Optional[str]:
    """Identify the standardized Indian State or UT name from raw text.

    Args:
        text: Raw text containing address.

    Returns:
        Standardized state name (e.g., 'Maharashtra', 'Karnataka') or None.
    """
    if not text:
        return None

    text_lower = text.lower()

    # 1. Direct State / UT match
    for state_name, aliases in INDIAN_STATES.items():
        for alias in aliases:
            # Match word boundary
            pattern = r"\b" + re.escape(alias) + r"\b"
            if re.search(pattern, text_lower):
                return state_name

    # 2. City fallback match to identify state
    for city, state_name in MAJOR_INDIAN_CITIES.items():
        pattern = r"\b" + re.escape(city) + r"\b"
        if re.search(pattern, text_lower):
            return state_name

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

    text_lower = text.lower()

    # Search against our database of major Indian cities
    for city, state_name in MAJOR_INDIAN_CITIES.items():
        if identified_state and state_name != identified_state:
            continue
        pattern = r"\b" + re.escape(city) + r"\b"
        if re.search(pattern, text_lower):
            return city.title()

    # Fallback to searching all cities regardless of state filter
    for city, _ in MAJOR_INDIAN_CITIES.items():
        pattern = r"\b" + re.escape(city) + r"\b"
        if re.search(pattern, text_lower):
            return city.title()

    # Heuristic: Check comma-separated token immediately before identified state (e.g. "Street, Thoothukudi, Tamil Nadu 628002")
    if identified_state:
        state_pattern = r",\s*([A-Za-z\s]+)\s*,\s*" + re.escape(identified_state)
        match_before_state = re.search(state_pattern, text, re.IGNORECASE)
        if match_before_state:
            cand = match_before_state.group(1).strip()
            if len(cand) > 2 and len(cand.split()) <= 2 and cand.lower() != identified_state.lower():
                return cand.title()

    # Regex heuristic for comma-separated tokens before pincode
    # e.g., "Road, Sector 5, Salt Lake, Kolkata - 700091"
    match = re.search(r",\s*([A-Za-z\s]+)\s*[-,\s]+\b[1-9][0-9]{5}\b", text)
    if match:
        candidate = match.group(1).strip()
        if len(candidate) > 2 and len(candidate.split()) <= 2:
            if identified_state and candidate.lower() == identified_state.lower():
                pass
            else:
                return candidate.title()

    return None


def parse_raw_address(
    raw_address: Optional[str], gst_number: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """Normalize a raw address string into structured components.

    Output format:
      - billing_address
      - shipping_address
      - city
      - state
      - pincode
      - country ("India")

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

    cleaned_addr = re.sub(r"\s+", " ", str(raw_address)).strip()

    # Extract 6-digit Pincode
    pincode_match = re.search(r"\b([1-9][0-9]{5})\b", cleaned_addr)
    pincode = validate_pincode(pincode_match.group(1)) if pincode_match else None

    # Identify State
    state = match_state_from_text(cleaned_addr)

    # Fallback to GST state code if state wasn't resolved from address text
    if not state and gst_number and len(gst_number) >= 2:
        state_code = gst_number[:2]
        if state_code in GST_STATE_CODES:
            state = GST_STATE_CODES[state_code]

    # Extract City
    city = extract_city_from_text(cleaned_addr, identified_state=state)

    result["billing_address"] = cleaned_addr
    result["shipping_address"] = cleaned_addr
    result["city"] = city
    result["state"] = state
    result["pincode"] = pincode
    result["country"] = "India"

    return result
