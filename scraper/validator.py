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
    r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",
    re.IGNORECASE,
)
PHONE_REGEX = re.compile(
    r"(?:(?:\+|0{0,2})91[\s-]*)?(?:[0]?[6-9]\d{9}|\b[6-9]\d{9}\b)"
)
PINCODE_REGEX = re.compile(r"\b([1-9][0-9]{5})\b")
FSSAI_REGEX = re.compile(r"\b([1-2][0-9]{13})\b")

# ---------------------------------------------------------------------------
# ADDRESS QUALITY BLACKLIST
# ---------------------------------------------------------------------------

# Phrases that immediately identify a string as noise / boilerplate, NOT a
# real seller address. Any candidate address containing these (case-insensitive)
# must be rejected before being written to the Billing Address column.
ADDRESS_BLACKLIST_PHRASES: List[str] = [
    # Flipkart corporate boilerplate
    "flipkart internet private limited",
    "flipkart india private limited",
    "flipkart india pvt",
    "flipkart internet pvt",
    "walmart",
    # Cache / crawl artefacts
    "google cache",
    "cached version",
    "web cache",
    "cache:",
    "webcache.googleusercontent",
    # Legal / policy footer text
    "all rights reserved",
    "terms of use",
    "terms & conditions",
    "terms and conditions",
    "privacy policy",
    "cookie policy",
    "legal notice",
    "copyright notice",
    # Navigation / UI text
    "click here",
    "sign in",
    "log in",
    "login",
    "register now",
    "add to cart",
    "buy now",
    "view cart",
    "advertisement",
    "sponsored",
    # Marketplace navigation labels
    "sell on flipkart",
    "become a seller",
    "customer care",
    "help center",
    # Generic web-scrape noise
    "javascript is disabled",
    "enable javascript",
    "please enable",
    "loading...",
    "page not found",
    "404 not found",
    "access denied",
    "meesho",
    "amazon.in",
    "snapdeal",
]


def is_junk_address(text: Optional[str], max_length: int = 350) -> bool:
    """Return True if the address candidate is junk / boilerplate and must be rejected.

    Rejects strings that:
      - Are empty or None
      - Exceed *max_length* characters (raw search snippet, not a real address)
      - Contain any phrase from ADDRESS_BLACKLIST_PHRASES (case-insensitive)

    Args:
        text: Candidate address string.
        max_length: Maximum permissible length for a valid address.

    Returns:
        True if the text should be discarded, False if it looks like a real address.
    """
    if not text or not str(text).strip():
        return True

    s = str(text).strip()

    if len(s) > max_length:
        return True

    s_lower = s.lower()
    for phrase in ADDRESS_BLACKLIST_PHRASES:
        if phrase in s_lower:
            return True

    return False



# Blacklisted Dummy / Framework / Marketplace Support Emails
DISALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "test.com",
    "mysite.com",
    "sample.com",
    "wixpress.com",
    "sentry.io",
    "shopify.com",
    "schema.org",
    "github.com",
    "wordpress.org",
    "google.com",
    "bing.com",
    "microsoft.com",
    "cloudflare.com",
    "godaddy.com",
    "gravatar.com",
    "w3.org",
    "mozilla.org",
    "apple.com",
    "flipkart.com",
    "seller.flipkart.com",
    "amazon.com",
    "amazon.in",
    "meesho.com",
    "myntra.com",
    "jiomart.com",
    "snapdeal.com",
    "ebay.com",
    "walmart.com",
    "indiamart.com",
    "justdial.com",
    "tradeindia.com",
    "zaubacorp.com",
    "tofler.in",
    "quikr.com",
    "sulekha.com",
}

DISALLOWED_EMAIL_PREFIXES = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
    "root",
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
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
}


def decode_obfuscated_email(text: str) -> str:
    """Decode common human-readable obfuscated email representations.

    Handles:
      - 'name [at] domain [dot] com'
      - 'name(at)domain(dot)com'
      - 'name [AT] domain [DOT] com'
      - 'name AT domain DOT com'
      - 'name [dot] sur [at] domain [dot] com'

    Args:
        text: Raw text string.

    Returns:
        Decoded text string with '@' and '.' restored.
    """
    if not text or ("at" not in text.lower() and "dot" not in text.lower()):
        return text

    decoded = text
    # Replace bracketed/parenthesized at & dot
    decoded = re.sub(r"\s*[\[\(<]\s*at\s*[\]\)>]\s*", "@", decoded, flags=re.I)
    decoded = re.sub(r"\s*[\[\(<]\s*dot\s*[\]\)>]\s*", ".", decoded, flags=re.I)
    # Replace standalone uppercase / spaced AT and DOT
    decoded = re.sub(r"\b\s+AT\s+\b", "@", decoded)
    decoded = re.sub(r"\b\s+DOT\s+\b", ".", decoded)
    return decoded


def validate_email(email_str: Optional[str]) -> Optional[str]:
    """Validate email address against RFC standards, decode obfuscations, and reject dummy addresses.

    Args:
        email_str: Candidate email string.

    Returns:
        Normalized lowercase email string or None if invalid.
    """
    if not email_str:
        return None

    raw = str(email_str).strip()
    decoded = decode_obfuscated_email(raw)
    cleaned = re.sub(r"^[\s\"'<(\[]+|[\s\"'>)\].,;:]+$", "", decoded).strip().lower()

    match = EMAIL_REGEX.search(cleaned)
    if not match:
        return None

    candidate = match.group(0).lower().rstrip(".,;:")

    # Disallow image file names mistakenly matched
    for ext in DISALLOWED_EMAIL_EXTENSIONS:
        if candidate.endswith(ext):
            return None

    if "@" not in candidate:
        return None

    user_part, domain_part = candidate.split("@", 1)
    user_part = user_part.strip()
    domain_part = domain_part.strip().lower()

    if not user_part or not domain_part or "." not in domain_part:
        return None

    # Check disallowed domains
    if domain_part in DISALLOWED_EMAIL_DOMAINS or any(domain_part.endswith("." + d) for d in DISALLOWED_EMAIL_DOMAINS):
        return None

    # Disallow generic noreply / system addresses unless specified
    if user_part in DISALLOWED_EMAIL_PREFIXES:
        return None

    # Reject placeholder addresses like yourname@domain.com or email@domain.com
    if user_part in {"yourname", "name", "email", "username", "user"} and domain_part in {"domain.com", "company.com", "example.com", "site.com"}:
        return None

    return candidate


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


def extract_pan_from_gstin(gstin: Optional[str]) -> Optional[str]:
    """Extract and validate the 10-character Indian PAN embedded inside a GSTIN (chars 3 to 12).

    Structure of standard 15-character GSTIN:
      - 2 digits: State code (index 0:2)
      - 10 chars: PAN (index 2:12) -> 5 letters, 4 digits, 1 letter
      - 1 char: Entity number (index 12)
      - 1 char: 'Z' (index 13)
      - 1 char: Checksum (index 14)

    Args:
        gstin: Candidate or verified GSTIN string.

    Returns:
        Normalized uppercase 10-character PAN if valid, else None.
    """
    if not gstin:
        return None

    valid_gst = validate_gst(gstin)
    if not valid_gst:
        return None

    embedded_pan = valid_gst[2:12]
    return validate_pan(embedded_pan, gst_str=valid_gst)


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
                valid_entity_types = {"C", "P", "H", "F", "A", "T", "B", "L", "J", "G"}
                if embedded_pan[3] in valid_entity_types:
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


# Generic business words that must not alone satisfy seller identity matching
GENERIC_SELLER_WORDS = {
    "india", "retail", "retails", "enterprise", "enterprises", "trading", "traders",
    "trader", "store", "stores", "shop", "shops", "online", "pvt", "ltd", "limited",
    "llp", "co", "company", "corp", "corporation", "inc", "ind", "solutions",
    "international", "group", "services", "hub", "mart", "bazaar", "bazar",
    "wholesalers", "wholesaler", "distributor", "distributors", "fashion", "fashions",
    "general", "generalstore", "exports", "export", "imports", "import", "handicrafts",
    "handicraft", "electronics", "electronic", "furniture", "collections", "collection",
    "industries", "industry", "creations", "creation", "textiles", "textile", "garments",
    "garment", "apparels", "apparel", "footwear", "footwears", "cloth", "clothing",
}

# Entity qualifier keywords often present in Indian business names
ENTITY_QUALIFIER_WORDS = {
    "enterprises", "enterprise", "traders", "trader", "trading", "retail", "retails",
    "store", "stores", "creations", "creation", "collections", "collection", "exports",
    "export", "imports", "import", "textiles", "textile", "garments", "garment",
    "fashions", "fashion", "industries", "industry", "apparel", "apparels", "footwear",
    "footwears", "jewellers", "jewellery", "cloth", "clothing", "cloths", "lifestyle",
    "handicrafts", "handicraft", "electronics", "electronic", "furniture",
}

GENERIC_MODIFIERS = {
    "royal", "fashion", "fashions", "metro", "star", "supreme", "national", "global",
    "super", "grand", "prime", "classic", "modern", "golden", "elite", "perfect",
    "smart", "best", "top", "choice", "alpha", "express", "direct", "point", "corner",
    "world", "india", "indian", "shree", "sri", "om", "new", "all", "pro", "plus",
    "hub", "zone", "house", "planet", "empire", "galaxy", "bazaar", "bazar", "plaza",
    "center", "centre", "mall", "market", "mart", "gallery", "junction", "palace",
    "vogue", "look", "style", "styles", "wear", "wears", "trend", "trends", "care",
    "tech", "goods", "supply", "supplies", "agency", "agencies", "co", "sons",
    "brothers", "bros", "gupta", "sharma", "singh", "kumar", "general", "digital", "city",
    "abc",
}

BUSINESS_CONTEXT_KEYWORDS = {
    "gst", "gstin", "pan", "fssai", "proprietor", "owner", "director", "founder", "promoter",
    "registered", "registration", "office", "address", "pincode", "postal", "wholesale", "retail",
    "manufacturer", "supplier", "company", "firm", "business", "tax", "cin", "din", "msme",
    "tirupur", "noida", "delhi", "mumbai", "bengaluru", "surat", "jaipur", "ahmedabad", "chennai",
    "kolkata", "hyderabad", "pune", "gujarat", "maharashtra", "tamil nadu", "karnataka", "haryana",
    "contact", "email", "phone", "mobile", "official", "store",
}


def is_generic_seller_name(name: str) -> bool:
    """Check if seller name consists mostly or entirely of generic business words.

    Examples:
      'Trader', 'Store', 'Enterprises', 'Industries', 'Collections', 'Trading', 'Retail Store',
      'ABC Enterprises', 'Fashion Store', 'Metro Traders', 'Royal Collections'

    Args:
        name: Seller name string.

    Returns:
        True if name is generic and requires location/strong matching, False otherwise.
    """
    if not name:
        return True
    raw_lower = name.lower().strip()
    norm = normalize_seller_name_for_matching(name)
    tokens = [w.lower() for w in re.split(r"[^\w]+", raw_lower) if w]
    if not tokens:
        return True

    # Check if all tokens are generic, qualifiers, common modifiers, or short (<= 3 chars)
    distinctive = [
        t for t in tokens
        if t not in GENERIC_SELLER_WORDS
        and t not in ENTITY_QUALIFIER_WORDS
        and t not in GENERIC_MODIFIERS
        and len(t) > 3
    ]
    if len(distinctive) == 0:
        return True
    if len(distinctive) == 1 and any(
        t in GENERIC_SELLER_WORDS or t in ENTITY_QUALIFIER_WORDS or t in GENERIC_MODIFIERS for t in tokens
    ):
        return True
    return False


def normalize_seller_name_for_matching(name: str) -> Dict[str, Any]:
    """Normalize seller name for flexible fuzzy, token-based, and controlled variation matching."""
    if not name:
        return {"clean": "", "compact": "", "compact_stripped": "", "tokens": [], "variations": []}

    raw = str(name).lower().strip().replace("&", "and")
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

    # Singular / Plural normalization (e.g. teamexports -> teamexport, creation -> creations)
    if compact.endswith("s") and len(compact) > 4:
        variations.add(compact[:-1])
    else:
        variations.add(compact + "s")

    # Common suffixes check
    for suf in [
        "limited", "ltd", "pvtltd", "pvt", "llp", "industries", "industry", "footwear",
        "footwears", "creation", "creations", "collection", "collections", "enterprises",
        "enterprise", "exports", "export", "imports", "import", "retail", "retails",
        "cloth", "clothing", "cloths", "fashion", "fashions", "traders", "trader", "trading",
        "store", "stores", "textile", "textiles", "garments", "garment", "apparel", "apparels",
    ]:
        if compact.endswith(suf) and len(compact) > len(suf) + 2:
            pref = compact[: -len(suf)]
            variations.add(pref)
            variations.add(f"{pref} {suf}")
            if suf.endswith("s"):
                variations.add(f"{pref}{suf[:-1]}")
                variations.add(f"{pref} {suf[:-1]}")
            else:
                variations.add(f"{pref}{suf}s")
                variations.add(f"{pref} {suf}s")
            break

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
        "variations": [v for v in variations if v],
    }


def calculate_seller_match_score(
    seller_name: str, candidate_text: str, source_url: str = ""
) -> Tuple[int, str, str]:
    """Calculate seller matching score (0 to 100) and details against candidate text or URL."""
    if not seller_name or not (candidate_text or source_url):
        return 0, "NO_TEXT", ""

    norm = normalize_seller_name_for_matching(seller_name)
    combined = f"{candidate_text} {source_url}".lower()
    combined_compact = re.sub(r"[^\w]", "", combined)
    url_compact = re.sub(r"[^\w]", "", source_url.lower()) if source_url else ""

    # Check business context with word boundaries for short keywords
    has_business_context = False
    for k in BUSINESS_CONTEXT_KEYWORDS:
        if len(k) <= 3:
            if re.search(r"\b" + re.escape(k) + r"\b", combined):
                has_business_context = True
                break
        else:
            if k in combined:
                has_business_context = True
                break

    # 1. Exact match of raw seller name in text as standalone word
    raw_lower = seller_name.strip().lower()
    if raw_lower and re.search(r"\b" + re.escape(raw_lower) + r"\b", combined, re.IGNORECASE):
        return 100, "EXACT_SELLER_NAME_MATCH", raw_lower

    # 2. Exact compact match in text or URL
    if norm["compact_stripped"] and len(norm["compact_stripped"]) >= 4:
        if re.search(r"\b" + re.escape(norm["compact_stripped"]) + r"\b", combined):
            return 100, "EXACT_COMPACT_MATCH", norm["compact_stripped"]
        if source_url and norm["compact_stripped"] in url_compact:
            return 95, "URL_COMPACT_MATCH", norm["compact_stripped"]
        if has_business_context and norm["compact_stripped"] in combined_compact:
            return 90, "EXACT_COMPACT_MATCH", norm["compact_stripped"]

    if norm["compact"] and len(norm["compact"]) >= 4:
        if re.search(r"\b" + re.escape(norm["compact"]) + r"\b", combined):
            return 95, "EXACT_COMPACT_MATCH", norm["compact"]
        if source_url and norm["compact"] in url_compact:
            return 95, "URL_COMPACT_MATCH", norm["compact"]
        if has_business_context and norm["compact"] in combined_compact:
            return 90, "EXACT_COMPACT_MATCH", norm["compact"]

    # 3. Check all variations
    for var in norm["variations"]:
        if not var or len(var) < 3:
            continue
        v_pattern = r"\b" + re.escape(var) + r"\b"
        if re.search(v_pattern, combined, re.IGNORECASE):
            if " " in var or var != raw_lower:
                if has_business_context or (source_url and var in source_url.lower()):
                    return 90, "VARIATION_EXACT_MATCH", var
            else:
                return 90, "VARIATION_EXACT_MATCH", var

    # 4. Token coverage check (e.g. "sn" and "attire" both present)
    distinctive_tokens = [t for t in norm["tokens"] if len(t) >= 2 and t not in GENERIC_SELLER_WORDS]
    check_tokens = distinctive_tokens if distinctive_tokens else [t for t in norm["tokens"] if len(t) >= 2]
    if check_tokens:
        matched_tokens = [
            t for t in check_tokens
            if re.search(r"\b" + re.escape(t) + r"\b", combined, re.IGNORECASE)
        ]
        coverage = len(matched_tokens) / len(check_tokens)
        if coverage >= 1.0 and (has_business_context or (source_url and any(t in source_url.lower() for t in check_tokens))):
            return 85, "FULL_TOKEN_MATCH", " ".join(matched_tokens)
        elif coverage >= 0.5 and len(matched_tokens) >= 1 and has_business_context:
            return 60, "PARTIAL_TOKEN_MATCH", " ".join(matched_tokens)

    return 0, "SELLER_MISMATCH", ""



def validate_seller_association(
    seller_name: str, candidate_text: str, source_url: str = ""
) -> bool:
    """Validate that candidate snippet or page is authentically associated with the seller name."""
    score, _, _ = calculate_seller_match_score(seller_name, candidate_text, source_url)
    return score >= 60


def match_gst_to_seller(
    seller_name: str,
    gst_number: str,
    legal_name: Optional[str] = None,
    trade_name: Optional[str] = None,
    snippet: str = "",
    url: str = "",
    city: Optional[str] = None,
    state: Optional[str] = None,
    location: Optional[str] = None,
    pincode: Optional[str] = None,
) -> Tuple[bool, int, str]:
    """Dedicated Indian GST-to-seller identity matcher.
    
    Verifies that the GSTIN candidate authentically belongs to the specific target seller.
    
    Signals evaluated:
      + Exact seller name match
      + Normalized & compact seller name match
      + Legal name & Trade name match
      + Distinctive token match
      + Entity-type consistency (rejects conflicting entity e.g. TRADERS vs ENTERPRISES)
      + State code consistency with seller state/location
      + City / Address location consistency
      + Pincode consistency
      + Domain / URL match
      
    Rejects candidates matching only generic terms ('India', 'Retail', 'Enterprises', 'Trading', 'Store')
    or possessing strong location/state conflicts.
    
    Returns:
      Tuple of (matched: bool, score: int, reason: str)
    """
    valid_gst = validate_gst(gst_number)
    if not valid_gst:
        return False, 0, "INVALID_GST_FORMAT"

    if not seller_name:
        return False, 0, "NO_SELLER_NAME"

    seller_lower = seller_name.strip().lower()
    seller_clean = re.sub(r"[^\w\s]", " ", seller_lower).strip()
    seller_tokens = [w for w in seller_clean.split() if w]

    # Distinctive words vs entity qualifiers
    distinctive_tokens = [w for w in seller_tokens if w not in GENERIC_SELLER_WORDS and len(w) >= 2]
    seller_entity_words = [w for w in seller_tokens if w in ENTITY_QUALIFIER_WORDS]

    combined = f"{legal_name or ''} {trade_name or ''} {snippet} {url}".lower()
    combined_compact = re.sub(r"[^\w]", "", combined)

    # 0. Location Disambiguation & Conflict Check
    gst_state_code = valid_gst[:2]
    gst_state_name = GST_STATE_CODES.get(gst_state_code, "").lower()

    # If seller state is known, check GST state code match
    if state:
        state_clean = state.strip().lower()
        if gst_state_name and state_clean not in gst_state_name and gst_state_name not in state_clean:
            return (
                False,
                0,
                f"LOCATION_MISMATCH: Candidate GST state '{gst_state_name}' ({gst_state_code}) conflicts with seller state '{state}'",
            )

    # If seller city is known, verify candidate does not conflict
    known_cities = {
        "mumbai", "delhi", "new delhi", "bengaluru", "bangalore", "chennai", "kolkata",
        "hyderabad", "ahmedabad", "surat", "pune", "jaipur", "lucknow", "kanpur", "nagpur",
        "indore", "thane", "bhopal", "visakhapatnam", "patna", "vadodara", "ghaziabad",
        "ludhiana", "agra", "nashik", "faridabad", "meerut", "rajkot", "varanasi",
        "srinagar", "aurangabad", "dhanbad", "amritsar", "navi mumbai", "allahabad",
        "prayagraj", "howrah", "gwalior", "jabalpur", "coimbatore", "vijayawada", "jodhpur",
        "madurai", "raipur", "kota", "guwahati", "chandigarh", "solapur", "hubballi",
        "dharwad", "tiruchirappalli", "bareilly", "moradabad", "mysuru", "tirupur",
        "gurgaon", "gurugram", "aligarh", "jalandhar", "bhubaneswar", "salem", "warangal",
        "mira bhayandar", "jalgaon", "guntur", "thiruvananthapuram", "bhiwandi", "saharanpur",
        "gorakhpur", "bikaner", "amravati", "noida", "jamshedpur", "bhilai", "cuttack",
        "firozabad", "kochi", "nellore", "bhavnagar", "dehradun", "durgapur", "asansol",
        "rourkela", "nanded", "kolhapur", "ajmer", "akola", "gulbarga", "jamnagar",
        "ujjain", "loni", "siliguri", "jhansi", "ulhasnagar", "jammu", "sangli",
        "mangalore", "erode", "belgaum", "ambattur", "tirunelveli", "malegaon", "gaya",
        "jalna", "udaipur", "maheshtala", "davanagere", "kozhikode", "kurnool", "rajpur",
        "bokaro", "south dumdum", "bellary", "patiala", "gopalpur", "agartala", "bhagalpur",
        "muzaffarnagar", "bhatpara", "panihati", "latur", "dhule", "rohtak", "korba",
        "bhilwara", "berhampur", "muzaffarpur", "ahmednagar", "mathura", "kollam",
        "avadi", "kadapa", "kamarhati", "sambalpur", "bilaspur", "shahjahanpur",
        "satara", "bijapur", "rampur", "shivamogga", "chandrapur", "junagadh", "thrissur",
        "alwar", "bardhaman", "kulti", "kakinada", "nizamabad", "parbhani", "tumkur",
        "khammam", "ozhukarai", "bihar sharif", "panipat", "darbhanga", "bally", "aizawl",
        "dewas", "ichalkaranji", "karnal", "bathinda", "jalpaiguri", "eluru", "barasat",
        "kirari suleman nagar", "purnia", "satna", "mau", "sonipat", "farrukhabad",
        "sagar", "rourkela", "durg", "imphal", "ratlam", "hapur", "arrah", "karimnagar",
        "anantapur", "etawah", "ambernath", "north dumdum", "bharatpur", "begusarai",
        "new delhi", "gandhidham", "baranagar", "tiruvannamalai", "thoothukudi", "tuticorin",
        "eral", "sivakasi", "hosur", "pollachi", "dindigul", "karur", "thanjavur",
    }
    if city:
        city_clean = city.strip().lower()
        if city_clean not in combined:
            # Check if an explicitly different city from the known cities is present in candidate
            for other_city in known_cities:
                if other_city != city_clean and len(other_city) >= 4 and re.search(r"\b" + re.escape(other_city) + r"\b", combined):
                    return (
                        False,
                        0,
                        f"LOCATION_MISMATCH: Candidate location '{other_city}' conflicts with target seller city '{city}'",
                    )

    # 1. Reject if candidate explicitly mentions a conflicting entity type
    # e.g., Target is "ABC ENTERPRISES", candidate is "ABC TRADERS"
    if seller_entity_words:
        target_entity = seller_entity_words[0]
        for conf_word in ENTITY_QUALIFIER_WORDS:
            if conf_word != target_entity and re.search(r"\b" + re.escape(conf_word) + r"\b", combined):
                # If target entity word is completely absent from candidate
                if not any(re.search(r"\b" + re.escape(se) + r"\b", combined) for se in seller_entity_words):
                    # Check if the distinctive token is nearby this conflicting word (e.g. "ABC Traders")
                    if distinctive_tokens and any(
                        re.search(r"\b" + re.escape(dt) + r"\s+" + re.escape(conf_word) + r"\b", combined)
                        for dt in distinctive_tokens
                    ):
                        return False, 20, f"SELLER_IDENTITY_MISMATCH: Candidate has conflicting entity '{conf_word}' vs target '{target_entity}'"

    # 2. Reject if candidate matches ONLY generic common words
    matched_words = [w for w in seller_tokens if re.search(r"\b" + re.escape(w) + r"\b", combined)]
    if matched_words and all(w in GENERIC_SELLER_WORDS for w in matched_words):
        return False, 0, "COMMON_WORD_ONLY: Candidate matched only generic business words"

    # Generic seller name protection check
    is_generic = is_generic_seller_name(seller_name)
    has_city_match = bool(city and city.strip().lower() in combined)
    has_state_match = bool(
        (state and (state.strip().lower() in gst_state_name or state.strip().lower() in combined))
        or (gst_state_name and gst_state_name in combined)
    )
    has_pincode_match = bool(pincode and pincode.strip() in combined)
    has_domain_match = bool(url and any(t in url.lower() for t in distinctive_tokens if len(t) >= 4))

    if is_generic:
        if city:
            # If target city is specified, require city, pincode, or domain match
            if not (has_city_match or has_pincode_match or has_domain_match):
                return (
                    False,
                    0,
                    "GENERIC_NAME_LOCATION_MISMATCH: Generic seller name requires matching city, pincode, or domain",
                )
        else:
            # Require state, pincode, or domain match
            if not (has_state_match or has_pincode_match or has_domain_match):
                return (
                    False,
                    0,
                    "GENERIC_NAME_LOCATION_MISMATCH: Generic seller name requires location or domain verification",
                )

    # 3. Exact seller name match (e.g. "ABC Enterprises" in legal/trade/snippet)
    if re.search(r"\b" + re.escape(seller_lower) + r"\b", combined):
        score = 95
        # Bonus if state code matches state name in text or known state
        if gst_state_name and (gst_state_name in combined or (state and state.lower() in gst_state_name)):
            score = 99
        if city and city.lower() in combined:
            score = 100
        if pincode and pincode.strip() in combined:
            score = 100
        return True, score, "EXACT_SELLER_NAME_MATCH"

    # 4. Legal / Trade name match
    for cand_name in [legal_name, trade_name]:
        if cand_name:
            cn_lower = cand_name.lower().strip()
            if seller_lower in cn_lower:
                score = 95
                if city and city.lower() in combined:
                    score = 100
                if pincode and pincode.strip() in combined:
                    score = 100
                return True, score, "LEGAL_OR_TRADE_NAME_EXACT_MATCH"
            # Check if all distinctive tokens match
            if distinctive_tokens and all(re.search(r"\b" + re.escape(dt) + r"\b", cn_lower) for dt in distinctive_tokens):
                score = 90
                if city and city.lower() in combined:
                    score = 98
                if pincode and pincode.strip() in combined:
                    score = 98
                return True, score, "LEGAL_NAME_DISTINCTIVE_TOKEN_MATCH"

    # 5. Compact seller name match in combined_compact
    norm = normalize_seller_name_for_matching(seller_name)
    compact = norm["compact"]
    if compact and len(compact) >= 5 and compact in combined_compact:
        score = 90
        if city and city.lower() in combined:
            score = 98
        if pincode and pincode.strip() in combined:
            score = 98
        return True, score, "COMPACT_SELLER_NAME_MATCH"

    # Check singular/plural compact
    if compact.endswith("s") and len(compact) > 5 and compact[:-1] in combined_compact:
        return True, 88, "COMPACT_SELLER_NAME_SINGULAR_MATCH"

    # 6. Check variations (e.g. "team export", "reepree creation")
    for var in norm["variations"]:
        if len(var) >= 4:
            if re.search(r"\b" + re.escape(var) + r"\b", combined):
                # Verify distinctive token is included
                if distinctive_tokens and any(dt in var.lower() for dt in distinctive_tokens):
                    score = 85
                    if city and city.lower() in combined:
                        score = 95
                    if pincode and pincode.strip() in combined:
                        score = 95
                    return True, score, f"VARIATION_MATCH: {var}"

    # 7. Distinctive tokens match check
    if distinctive_tokens:
        all_dist_present = all(
            re.search(r"\b" + re.escape(dt) + r"\b", combined) or dt in combined_compact
            for dt in distinctive_tokens
        )
        if all_dist_present:
            # If distinctive token is short (<= 3 chars, like "ABC") and entity word is missing, reject
            if all(len(dt) <= 3 for dt in distinctive_tokens) and seller_entity_words and not any(se in combined for se in seller_entity_words):
                return False, 30, "INSUFFICIENT_DISTINCTIVE_EVIDENCE: Short token without entity qualifier"
            score = 80
            if city and city.lower() in combined:
                score = 95
            if pincode and pincode.strip() in combined:
                score = 95
            return True, score, "DISTINCTIVE_TOKEN_MATCH"

    return False, 0, "NO_MATCHING_SELLER: No reliable seller identity evidence"



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


# ---------------------------------------------------------------------------
# SOURCE TYPE CLASSIFICATION ENGINE
# ---------------------------------------------------------------------------

# Source type priority weights (higher = better source)
SOURCE_TYPE_WEIGHTS: Dict[str, int] = {
    "GOVERNMENT": 100,
    "COMPANY_REGISTRY": 95,
    "OFFICIAL_WEBSITE": 90,
    "BUSINESS_DIRECTORY": 80,
    "MARKETPLACE": 50,
    "SOCIAL_PROFILE": 40,
    "NEWS": 35,
    "BLOG": 20,
    "BLOG_FORUM": 15,
    "FORUM": 15,
    "JOB_PORTAL": 10,
    "WIKIPEDIA": 5,
    "TOOL_OR_UTILITY": 0,
    "RECIPE": 0,
    "UNRELATED_COMPANY": 0,
    "UNKNOWN": 10,
}

# Domains mapped directly to their source type
_DISALLOWED_DOMAIN_MAP: Dict[str, str] = {
    # Tools / translate
    "translate.google.com": "TOOL_OR_UTILITY",
    "translate.google.co.in": "TOOL_OR_UTILITY",
    "google.com": "TOOL_OR_UTILITY",
    "play.google.com": "TOOL_OR_UTILITY",
    "bing.com": "TOOL_OR_UTILITY",
    "yahoo.com": "TOOL_OR_UTILITY",
    "duckduckgo.com": "TOOL_OR_UTILITY",
    "wolfram.com": "TOOL_OR_UTILITY",
    # US/non-India government — unrelated to Indian business registration
    "uscourts.gov": "UNRELATED_COMPANY",
    "pacer.uscourts.gov": "UNRELATED_COMPANY",
    # Wikipedia-like
    "wikipedia.org": "WIKIPEDIA",
    "wikimedia.org": "WIKIPEDIA",
    "wikidata.org": "WIKIPEDIA",
    # Major unrelated tech / corporate companies
    "logitech.com": "UNRELATED_COMPANY",
    "apple.com": "UNRELATED_COMPANY",
    "microsoft.com": "UNRELATED_COMPANY",
    "samsung.com": "UNRELATED_COMPANY",
    "sony.com": "UNRELATED_COMPANY",
    "intel.com": "UNRELATED_COMPANY",
    "nvidia.com": "UNRELATED_COMPANY",
    "adobe.com": "UNRELATED_COMPANY",
    "oracle.com": "UNRELATED_COMPANY",
    "ibm.com": "UNRELATED_COMPANY",
    # Forums / social
    "reddit.com": "FORUM",
    "quora.com": "FORUM",
    "stackoverflow.com": "FORUM",
    "stackexchange.com": "FORUM",
    # Job portals
    "naukri.com": "JOB_PORTAL",
    "indeed.com": "JOB_PORTAL",
    "glassdoor.com": "JOB_PORTAL",
    "linkedin.com": "JOB_PORTAL",
    "shine.com": "JOB_PORTAL",
    "monster.com": "JOB_PORTAL",
    "foundit.in": "JOB_PORTAL",
    "apna.co": "JOB_PORTAL",
    # Social media / video
    "facebook.com": "SOCIAL_PROFILE",
    "instagram.com": "SOCIAL_PROFILE",
    "twitter.com": "SOCIAL_PROFILE",
    "x.com": "SOCIAL_PROFILE",
    "youtube.com": "SOCIAL_PROFILE",
    "pinterest.com": "SOCIAL_PROFILE",
    "snapchat.com": "SOCIAL_PROFILE",
    # E-commerce marketplaces
    "amazon.in": "MARKETPLACE",
    "amazon.com": "MARKETPLACE",
    "flipkart.com": "MARKETPLACE",
    "myntra.com": "MARKETPLACE",
    "snapdeal.com": "MARKETPLACE",
    "meesho.com": "MARKETPLACE",
    "ajio.com": "MARKETPLACE",
    "shopclues.com": "MARKETPLACE",
    "paytmmall.com": "MARKETPLACE",
    # Developer / code
    "github.com": "TOOL_OR_UTILITY",
    "gitlab.com": "TOOL_OR_UTILITY",
    "pypi.org": "TOOL_OR_UTILITY",
    "npmjs.com": "TOOL_OR_UTILITY",
    # Blogging platforms
    "medium.com": "BLOG",
    "blogspot.com": "BLOG",
    "wordpress.com": "BLOG",
    "tumblr.com": "BLOG",
    # Document sharing
    "scribd.com": "BLOG",
    "slideshare.net": "BLOG",
    # Misc irrelevant
    "zhihu.com": "UNKNOWN",
    "slotdemoindonesia.com": "TOOL_OR_UTILITY",
    "slot-demo.com": "TOOL_OR_UTILITY",
}

# High-authority Indian business registries / government portals
_REGISTRY_DOMAINS = {
    "mca.gov.in", "gst.gov.in", "incometax.gov.in", "fssai.gov.in",
    "zaubacorp.com", "quickcompany.in", "tofler.in", "thecompanycheck.com",
    "mastersindia.co", "zauba.com", "vakilsearch.com",
}

# Keywords in URL path signalling recipe content
_RECIPE_PATH_SIGNALS = {"/recipe", "/recipes", "/food", "/cuisine", "/cook", "/dish", "/ingredient", "/menu"}
_RECIPE_TITLE_SIGNALS = {"recipe", "ingredients", "cooking", "bake", "grill", "cuisine", "chef", "dish"}

# Keywords signalling job portal content
_JOB_PATH_SIGNALS = {"/job", "/jobs", "/career", "/careers", "/vacancy", "/vacancies", "/hiring", "/apply"}
_JOB_TITLE_SIGNALS = {"job opening", "career opportunity", "vacancy", "hiring", "apply now", "job description"}

# Utility / gambling / unrelated title signals
_UTILITY_TITLE_SIGNALS = {
    "translate", "calculator", "converter", "free pdf", "download",
    "slot demo", "casino", "gambling", "gacor", "betting", "lottery",
    "cpu benchmark", "gpu benchmark",
}

# Domain fragments for forums
_FORUM_DOMAIN_SIGNALS = {"forum", "discuss", "community", "talk.", "boards.", "answers."}


def classify_source_type(url: str, title: str, snippet: str) -> Tuple[str, int]:
    """Classify the type and quality weight of a search result source.

    Args:
        url: Search result URL.
        title: Search result title text.
        snippet: Search result snippet text.

    Returns:
        Tuple of (source_type_string, quality_weight 0-100).
    """
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(url.lower() if url else "")
        domain = parsed.netloc.replace("www.", "").strip()
        path = parsed.path.lower()
    except Exception:
        domain = ""
        path = ""

    title_lower = title.lower() if title else ""
    snippet_lower = snippet.lower() if snippet else ""

    # 1. Exact disallowed domain map check
    for d_key, d_type in _DISALLOWED_DOMAIN_MAP.items():
        if d_key in domain:
            return d_type, SOURCE_TYPE_WEIGHTS.get(d_type, 0)

    # 2. Indian government / company registry
    for g_dom in _REGISTRY_DOMAINS:
        if g_dom in domain:
            if any(s in domain for s in ("zaubacorp", "tofler", "thecompanycheck", "quickcompany", "mastersindia", "zauba")):
                return "COMPANY_REGISTRY", SOURCE_TYPE_WEIGHTS["COMPANY_REGISTRY"]
            return "GOVERNMENT", SOURCE_TYPE_WEIGHTS["GOVERNMENT"]

    # 3. General .gov.in detection
    if domain.endswith(".gov.in") or domain == "gov.in":
        return "GOVERNMENT", SOURCE_TYPE_WEIGHTS["GOVERNMENT"]

    # 4. Recipe detection
    url_is_recipe = any(sig in path for sig in _RECIPE_PATH_SIGNALS)
    title_is_recipe = any(sig in title_lower for sig in _RECIPE_TITLE_SIGNALS)
    if url_is_recipe or (title_is_recipe and "company" not in title_lower and "seller" not in title_lower):
        return "RECIPE", SOURCE_TYPE_WEIGHTS["RECIPE"]

    # 5. Job portal detection
    url_is_job = any(sig in path for sig in _JOB_PATH_SIGNALS)
    title_is_job = any(sig in title_lower for sig in _JOB_TITLE_SIGNALS)
    if url_is_job or title_is_job:
        return "JOB_PORTAL", SOURCE_TYPE_WEIGHTS["JOB_PORTAL"]

    # 6. Utility / gambling / unrelated title
    if any(sig in title_lower for sig in _UTILITY_TITLE_SIGNALS):
        return "TOOL_OR_UTILITY", SOURCE_TYPE_WEIGHTS["TOOL_OR_UTILITY"]

    # 7. Forum detection by domain fragments
    if any(sig in domain for sig in _FORUM_DOMAIN_SIGNALS):
        return "BLOG_FORUM", SOURCE_TYPE_WEIGHTS["BLOG_FORUM"]

    # 8. News outlets
    _news_domains = {
        "ndtv.com", "thehindu.com", "hindustantimes.com", "economictimes",
        "businessstandard", "livemint.com", "moneycontrol.com", "inc42.com",
        "techcrunch.com", "reuters.com", "bbc.com", "timesofindia",
    }
    if any(nd in domain for nd in _news_domains):
        return "NEWS", SOURCE_TYPE_WEIGHTS["NEWS"]

    # 9. Blog platforms
    if any(s in domain for s in ("blogspot", "wordpress", "substack", "medium")):
        return "BLOG", SOURCE_TYPE_WEIGHTS["BLOG"]

    # 10. Default UNKNOWN — may be an official website; let seller match decide
    return "UNKNOWN", SOURCE_TYPE_WEIGHTS["UNKNOWN"]


def is_disallowed_source(
    source_type: str,
    seller_match_score: int,
    seller_name: str,
    url: str,
) -> Tuple[bool, str]:
    """Determine if a search result should be hard-rejected BEFORE any HTTP fetch.

    Args:
        source_type: Classified source type string.
        seller_match_score: Seller match score 0-100.
        seller_name: Target seller name.
        url: Result URL string.

    Returns:
        Tuple of (should_reject: bool, reject_reason: str).
    """
    import urllib.parse
    try:
        domain = urllib.parse.urlparse(url.lower()).netloc.replace("www.", "")
    except Exception:
        domain = ""

    seller_slug = re.sub(r"[^a-z0-9]", "", seller_name.lower())

    if source_type == "TOOL_OR_UTILITY":
        # Allow only if seller slug is clearly in the domain
        if seller_slug and len(seller_slug) >= 4 and seller_slug in domain.replace(".", ""):
            return False, ""
        return True, "DISALLOWED_SOURCE_TYPE"

    if source_type == "RECIPE":
        return True, "UNRELATED_DOMAIN"

    if source_type == "UNRELATED_COMPANY":
        if seller_match_score >= 70:
            return False, ""
        return True, "UNRELATED_COMPANY"

    if source_type in ("FORUM", "BLOG_FORUM"):
        if seller_match_score >= 70:
            return False, ""
        return True, "DISALLOWED_SOURCE_TYPE"

    if source_type == "BLOG":
        if seller_match_score >= 70:
            return False, ""
        return True, "DISALLOWED_SOURCE_TYPE"

    if source_type == "JOB_PORTAL":
        if seller_match_score >= 80:
            return False, ""
        return True, "DISALLOWED_SOURCE_TYPE"

    if source_type == "WIKIPEDIA":
        return True, "DISALLOWED_SOURCE_TYPE"

    if source_type == "MARKETPLACE":
        # Accept only seller-specific pages (slug in URL path)
        if seller_slug and len(seller_slug) >= 4 and seller_slug in url.lower():
            return False, ""
        return True, "DISALLOWED_SOURCE_TYPE"

    if source_type == "SOCIAL_PROFILE":
        if seller_match_score >= 60:
            return False, ""
        return True, "SELLER_MISMATCH"

    # OFFICIAL_WEBSITE, COMPANY_REGISTRY, GOVERNMENT, BUSINESS_DIRECTORY, NEWS, UNKNOWN
    if seller_match_score >= 50:
        return False, ""

    # Final check: seller slug literally in domain
    if seller_slug and len(seller_slug) >= 4 and seller_slug in domain.replace(".", ""):
        return False, ""

    return True, "SELLER_MISMATCH"
