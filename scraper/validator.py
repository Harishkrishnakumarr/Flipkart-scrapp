"""Validation and confidence scoring module for extracted business data."""

import re
from typing import Any, Dict, List, Optional, Tuple

from scraper.config import (
    GST_STATE_CODES,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_FOUND,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_VERIFIED,
)

# Regex Patterns
GST_REGEX = re.compile(
    r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b",
    re.IGNORECASE,
)
PAN_REGEX = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b", re.IGNORECASE)
EMAIL_REGEX = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
    re.IGNORECASE,
)
PHONE_REGEX = re.compile(
    r"(?:(?:\+|0{0,2})91[\s-]*)?(?:[0]?[6-9]\d{9}|\b[6-9]\d{9}\b)"
)
PINCODE_REGEX = re.compile(r"\b([1-9][0-9]{5})\b")
FSSAI_REGEX = re.compile(r"\b([1-2][0-9]{13})\b")

# Blacklisted Dummy / Framework Emails
DISALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "domain.com",
    "test.com",
    "mysite.com",
    "sample.com",
    "wixpress.com",
    "sentry.io",
    "shopify.com",
    "schema.org",
}

DISALLOWED_EMAIL_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
}


def validate_gst(gst_str: Optional[str]) -> Optional[str]:
    """Validate Indian GSTIN (15 characters) and verify state code.

    Args:
        gst_str: Raw GST string.

    Returns:
        Normalized uppercase 15-character GSTIN string or None if invalid.
    """
    if not gst_str:
        return None

    cleaned = re.sub(r"[\s\-_]", "", str(gst_str)).upper()
    match = GST_REGEX.search(cleaned)
    if not match:
        return None

    gst_candidate = match.group(1).upper()
    state_code = gst_candidate[:2]

    # Verify that first 2 digits match a valid Indian state/UT code
    if state_code not in GST_STATE_CODES:
        return None

    return gst_candidate


def validate_pan(pan_str: Optional[str], gst_str: Optional[str] = None) -> Optional[str]:
    """Validate Indian PAN (10 characters: 5 letters, 4 digits, 1 letter).

    Cross-checks with GSTIN if available (chars 3 to 12).

    Args:
        pan_str: Candidate PAN string.
        gst_str: Optional validated GSTIN string.

    Returns:
        Normalized uppercase 10-character PAN or None if invalid.
    """
    # If valid GST is provided, PAN is inherently embedded at index 2:12
    if gst_str:
        valid_gst = validate_gst(gst_str)
        if valid_gst:
            embedded_pan = valid_gst[2:12]
            if PAN_REGEX.match(embedded_pan):
                return embedded_pan

    if not pan_str:
        return None

    cleaned = re.sub(r"[\s\-_]", "", str(pan_str)).upper()
    match = PAN_REGEX.search(cleaned)
    if not match:
        return None

    pan_candidate = match.group(1).upper()
    # 4th character must represent a valid entity type in India
    valid_entity_types = {"C", "P", "H", "F", "A", "T", "B", "L", "J", "G"}
    if pan_candidate[3] not in valid_entity_types:
        return None

    return pan_candidate


def validate_email(email_str: Optional[str]) -> Optional[str]:
    """Validate email address against RFC standards and reject dummy addresses.

    Args:
        email_str: Candidate email string.

    Returns:
        Normalized lowercase email string or None if invalid.
    """
    if not email_str:
        return None

    email_clean = str(email_str).strip().lower()
    match = EMAIL_REGEX.search(email_clean)
    if not match:
        return None

    candidate = match.group(0).lower()

    # Disallow image file names mistakenly matched
    for ext in DISALLOWED_EMAIL_EXTENSIONS:
        if candidate.endswith(ext):
            return None

    # Check disallowed domains
    domain = candidate.split("@")[-1]
    if domain in DISALLOWED_EMAIL_DOMAINS:
        return None

    return candidate


def validate_phone(phone_str: Optional[str]) -> Optional[str]:
    """Validate and normalize Indian mobile phone number (10 digits starting 6-9).

    Args:
        phone_str: Candidate phone string.

    Returns:
        Normalized 10-digit phone string or None if invalid.
    """
    if not phone_str:
        return None

    cleaned = re.sub(r"[^\d+]", "", str(phone_str))
    digits = re.sub(r"\D", "", cleaned)

    # If prefixed with 91 and total length is 12 (e.g. 919876543210)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    # If prefixed with 0 and total length is 11 (e.g. 09876543210)
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    # Valid Indian mobile is 10 digits starting with 6, 7, 8, or 9
    if len(digits) == 10 and digits[0] in {"6", "7", "8", "9"}:
        return digits

    return None


def validate_pincode(pincode_str: Optional[str]) -> Optional[str]:
    """Validate 6-digit Indian Postal Pincode.

    Args:
        pincode_str: Candidate pincode string.

    Returns:
        Validated 6-digit pincode or None if invalid.
    """
    if not pincode_str:
        return None

    cleaned = re.sub(r"\D", "", str(pincode_str))
    if len(cleaned) == 6 and cleaned[0] in "123456789":
        return cleaned

    return None


def validate_fssai(fssai_str: Optional[str]) -> Optional[str]:
    """Validate 14-digit Indian FSSAI License Number.

    Args:
        fssai_str: Candidate FSSAI string.

    Returns:
        Validated 14-digit FSSAI string or None if invalid.
    """
    if not fssai_str:
        return None

    cleaned = re.sub(r"\D", "", str(fssai_str))
    if len(cleaned) == 14 and cleaned[0] in {"1", "2"}:
        return cleaned

    return None


def normalize_seller_name_for_matching(name: str) -> Dict[str, Any]:
    """Normalize seller name for flexible fuzzy and token-based matching.

    Args:
        name: Raw or display seller name.

    Returns:
        Dict with 'clean', 'compact', 'tokens', and 'variations'.
    """
    if not name:
        return {"clean": "", "compact": "", "tokens": [], "variations": []}

    raw = str(name).lower().strip()
    raw = raw.replace("&", "and")
    # Remove business entity suffixes
    raw_cleaned = re.sub(
        r"\b(pvt|private|ltd|limited|inc|co|corp|corporation|llp|enterprises|enterprise|retail|retails|store|stores|traders|trader|trading|ind|india)\b",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"[^\w\s]", " ", raw_cleaned).strip()
    clean = re.sub(r"\s+", " ", clean)

    compact = re.sub(r"[^\w]", "", raw)
    compact_stripped = re.sub(r"[^\w]", "", clean)

    # Token breakdown (camelCase split e.g. SNAttire -> SN Attire -> sn, attire)
    camel_split = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    camel_tokens = [w.lower() for w in re.split(r"[^\w]+", camel_split) if w]
    tokens = list(set([w for w in re.split(r"[^\w]+", clean) if w] + camel_tokens))

    variations = set([raw, clean, compact, compact_stripped])
    if " " in clean:
        variations.add(clean.replace(" ", ""))
    for t in tokens:
        if len(t) >= 3:
            variations.add(t)

    return {
        "clean": clean,
        "compact": compact,
        "compact_stripped": compact_stripped,
        "tokens": tokens,
        "variations": list(variations),
    }


def calculate_seller_match_score(
    seller_name: str, candidate_text: str, source_url: str = ""
) -> Tuple[int, str, str]:
    """Calculate seller matching score (0 to 100) and details against candidate text or URL.

    Args:
        seller_name: Target marketplace seller name.
        candidate_text: Snippet, title, or webpage text.
        source_url: Result URL.

    Returns:
        Tuple of (score: int, match_reason: str, matched_variation: str)
    """
    if not seller_name or not (candidate_text or source_url):
        return 0, "NO_TEXT", ""

    norm = normalize_seller_name_for_matching(seller_name)
    combined = f"{candidate_text} {source_url}".lower()
    combined_compact = re.sub(r"[^\w]", "", combined)
    url_compact = re.sub(r"[^\w]", "", source_url.lower()) if source_url else ""

    # 1. Exact compact match in text or URL (e.g. snattire in snattire.in or 'sn attire')
    if norm["compact_stripped"] and len(norm["compact_stripped"]) >= 4:
        if norm["compact_stripped"] in combined_compact:
            return 100, "EXACT_COMPACT_MATCH", norm["compact_stripped"]
        if norm["compact_stripped"] in url_compact:
            return 95, "URL_COMPACT_MATCH", norm["compact_stripped"]

    if norm["compact"] and len(norm["compact"]) >= 4:
        if norm["compact"] in combined_compact:
            return 95, "EXACT_COMPACT_MATCH", norm["compact"]

    # 2. Check all variations
    for var in norm["variations"]:
        if not var or len(var) < 3:
            continue
        v_pattern = r"\b" + re.escape(var) + r"\b"
        if re.search(v_pattern, combined, re.IGNORECASE):
            return 90, "VARIATION_EXACT_MATCH", var

    # 3. Token coverage check (e.g. "sn" and "attire" both present)
    valid_tokens = [t for t in norm["tokens"] if len(t) >= 2]
    if valid_tokens:
        matched_tokens = [
            t for t in valid_tokens
            if re.search(r"\b" + re.escape(t) + r"\b", combined, re.IGNORECASE) or t in combined_compact
        ]
        coverage = len(matched_tokens) / len(valid_tokens)
        if coverage >= 1.0:
            return 85, "FULL_TOKEN_MATCH", " ".join(matched_tokens)
        elif coverage >= 0.5 and len(matched_tokens) >= 1:
            return 60, "PARTIAL_TOKEN_MATCH", " ".join(matched_tokens)

    return 0, "SELLER_MISMATCH", ""


def validate_seller_association(
    seller_name: str, candidate_text: str, source_url: str = ""
) -> bool:
    """Validate that candidate snippet or page is authentically associated with the seller name.

    Supports controlled normalized matching:
      - Flexible compact & token matching (e.g. SNAttire <-> SN Attire <-> snattire).
      - Suffix stripping (Pvt Ltd, Enterprises, Retail, etc.).
      - Business context awareness.

    Args:
        seller_name: Target marketplace seller name.
        candidate_text: Text snippet or webpage text where data was extracted.
        source_url: URL of the webpage.

    Returns:
        True if seller association score >= 60, False otherwise.
    """
    score, _, _ = calculate_seller_match_score(seller_name, candidate_text, source_url)
    return score >= 60


def cross_check_seller_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-check and harmonize credentials across sources.

    - Verifies PAN consistency with GSTIN.
    - Resolves State from GSTIN if missing or conflicting.
    - Validates phone and email formatting.

    Args:
        data: Dictionary of candidate seller credentials.

    Returns:
        Harmonized dictionary of verified fields.
    """
    gst = validate_gst(data.get("gst_number"))
    pan = validate_pan(data.get("pan_number"), gst_str=gst)
    phone = validate_phone(data.get("contact_number"))
    email = validate_email(data.get("email"))
    fssai = validate_fssai(data.get("fssai_number"))
    pincode = validate_pincode(data.get("pincode"))

    # State from GST state code
    state = data.get("state")
    if gst and len(gst) >= 2:
        state_code = gst[:2]
        if state_code in GST_STATE_CODES:
            gst_state = GST_STATE_CODES[state_code]
            if not state or state != gst_state:
                state = gst_state

    # Clean owner name
    owner = data.get("owner_name")
    if owner:
        owner_clean = str(owner).strip()
        if len(owner_clean) < 3 or any(w in owner_clean.lower() for w in ["contact", "service", "policy", "terms", "flipkart", "amazon"]):
            owner = None
        else:
            owner = owner_clean

    data["gst_number"] = gst
    data["pan_number"] = pan
    data["contact_number"] = phone
    data["email"] = email
    data["fssai_number"] = fssai
    data["pincode"] = pincode
    data["state"] = state
    data["owner_name"] = owner
    return data


def calculate_field_confidence(
    field_name: str, value: Any, source: str, gst_validated: bool = False
) -> Tuple[float, str]:
    """Calculate the confidence score and source indicator for an individual field.

    Confidence Rules:
      - Website + GST matched: 0.95 - 0.99
      - Official Website extracted: 0.80 - 0.90
      - Public search result / directory snippet: 0.60 - 0.75
      - Unverified / partial: < 0.50

    Args:
        field_name: Name of the field.
        value: Validated field value.
        source: Extraction source ('website', 'gst_portal', 'search_snippet', 'directory').
        gst_validated: Whether a valid GST was verified for this seller.

    Returns:
        Tuple of (confidence_score, source_description).
    """
    if value is None:
        return 0.0, "not_found"

    src_lower = source.lower()

    if "gst_portal" in src_lower or (gst_validated and "website" in src_lower):
        confidence = 0.97
    elif "website" in src_lower:
        confidence = 0.85
    elif "directory" in src_lower or "search_snippet" in src_lower:
        confidence = 0.65
    else:
        confidence = 0.45

    return confidence, source


def determine_seller_status(
    record: Dict[str, Any], confidence_dict: Dict[str, Dict[str, Any]]
) -> str:
    """Determine the overall verification status for the seller.

    Statuses:
      - VERIFIED: Valid GSTIN / PAN confirmed with high confidence.
      - PARTIALLY_VERIFIED: Contact / Email / Address confirmed from website or search.
      - NEEDS_REVIEW: Low confidence or conflicting attributes.
      - NOT_FOUND: No verifiable web data found.

    Args:
        record: Dictionary of seller fields.
        confidence_dict: Dictionary containing confidence scores for each field.

    Returns:
        Status string.
    """
    has_gst = bool(record.get("gst_number"))
    has_pan = bool(record.get("pan_number"))
    has_email = bool(record.get("email"))
    has_phone = bool(record.get("contact_number"))
    has_website = bool(record.get("website_url"))
    has_address = bool(record.get("city") or record.get("state") or record.get("billing_address"))

    # If GST is verified or PAN + Website + Contact
    if has_gst and (has_website or has_address or has_phone):
        return STATUS_VERIFIED

    if has_gst or (has_pan and (has_website or has_email or has_phone)):
        return STATUS_VERIFIED

    if (has_email or has_phone) and (has_website or has_address):
        return STATUS_PARTIALLY_VERIFIED

    if has_website or has_email or has_phone or has_address:
        return STATUS_NEEDS_REVIEW

    return STATUS_NOT_FOUND
