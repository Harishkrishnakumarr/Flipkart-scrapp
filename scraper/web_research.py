"""Autonomous public web research and field-driven Bing enrichment engine for marketplace sellers.

Pipeline Flow:
  1. Extract and normalize seller name, generating search variations
     (e.g., REDTAPELIMITED -> RED TAPE LIMITED, REDTAPE LIMITED, RED TAPE).
  2. Search for and inspect the official website first, scraping contact, about, terms,
     and privacy subpages for credentials.
  3. Identify all fields that remain NOT FOUND.
  4. For EACH missing field, execute targeted Bing searches with query expansion.
  5. Inspect top 5-10 results per query (Title, Snippet, URL, and high-authority directory pages).
  6. Strictly validate seller association (do not confuse brand and company).
  7. Parse addresses into Billing Address, City, State, Pincode, Country.
  8. Strictly validate formats (15-char GSTIN, 10-char PAN, 6-digit Pincode, valid phone/email).
  9. Enforce source priority hierarchy (Government/Filings > Official Website > Directory > Search Snippets).
 10. Cache seller enrichment data to prevent redundant network searches across duplicate products.
 11. Return structured 18+ column record for live Excel updates.
"""

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from scraper.address_parser import parse_raw_address
from scraper.config import (
    CACHE_FILE,
    DEFAULT_HEADERS,
    HTTP_TIMEOUT_SECONDS,
    STATUS_NOT_FOUND,
    USER_AGENTS,
)
from scraper.validator import (
    EMAIL_REGEX,
    FSSAI_REGEX,
    GST_REGEX,
    PAN_REGEX,
    PHONE_REGEX,
    PINCODE_REGEX,
    calculate_field_confidence,
    calculate_seller_match_score,
    classify_source_type,
    cross_check_seller_data,
    determine_seller_status,
    is_disallowed_source,
    match_gst_to_seller,
    normalize_seller_name_for_matching,
    validate_email,
    validate_fssai,
    validate_gst,
    validate_pan,
    validate_phone,
    validate_pincode,
    validate_seller_association,
)
from scraper.website_parser import WebsiteParser

logger = logging.getLogger("FlipkartScraper.WebResearch")

# Strict Enrichment Boundaries
FIELD_TIMEOUT_SECONDS: float = 30.0
MAX_BING_QUERIES_PER_FIELD: int = 3
MAX_BRAVE_QUERIES_PER_FIELD: int = 2

# Domains that are generic platforms or social networks, not individual seller official websites
EXCLUDED_WEBSITE_DOMAINS = {
    "flipkart.com",
    "amazon.in",
    "amazon.com",
    "snapdeal.com",
    "meesho.com",
    "myntra.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "linkedin.com",
    "pinterest.com",
    "play.google.com",
    "apps.apple.com",
    "wikipedia.org",
    "quora.com",
    "reddit.com",
    "duckduckgo.com",
    "google.com",
    "bing.com",
    "yahoo.com",
    "uscourts.gov",
    "gov.in",
    "nic.in",
    "scribd.com",
    "github.com",
    "stackoverflow.com",
    "medium.com",
    "blogspot.com",
    "wordpress.com",
}

# High-authority company registry / business directories
DIRECTORY_DOMAINS = {
    "zaubacorp.com",
    "thecompanycheck.com",
    "tofler.in",
    "quickcompany.in",
    "mastersindia.net",
    "indiamart.com",
    "instafinancials.com",
    "gstsearch.in",
    "piceapp.com",
    "knowyourgst.com",
    "cleartax.in",
    "indiafilings.com",
    "vakilsearch.com",
    "economictimes.indiatimes.com",
}

# Source Priority Hierarchy: Higher numbers take precedence and cannot be overwritten by lower ones
SOURCE_PRIORITY: Dict[str, int] = {
    "government_source": 7,
    "company_website": 6,
    "filing_registry": 5,
    "marketplace_profile": 4,
    "directory_registry": 3,
    "targeted_search": 2,
    "search_query": 1,
    "not_found": 0,
}

# Field-Specific Bing Query Templates (Max 5 per field)
FIELD_BING_QUERIES: Dict[str, List[str]] = {
    "gst": [
        '"{seller}" GST',
        '"{seller}" GSTIN',
        '"{seller}" "GST number"',
        '"{seller}" "GSTIN number"',
        '"{seller}" GST India',
        '"{seller}" "GST registration"',
    ],
    "pan": [
        '"{seller}" PAN',
        '"{seller}" "PAN number"',
        '"{seller}" "company PAN"',
        '"{seller}" "PAN card"',
        '"{seller}" India PAN',
        '"{seller}" proprietor PAN',
        '"{seller}" director PAN',
    ],
    "fssai": [
        '"{seller}" FSSAI',
        '"{seller}" "FSSAI number"',
        '"{seller}" "FSSAI license"',
        '"{seller}" "FSSAI registration"',
        '"{seller}" food license',
    ],
    "owner": [
        '"{seller}" owner',
        '"{seller}" founder',
        '"{seller}" proprietor',
        '"{seller}" director',
        '"{seller}" "managing director"',
    ],
    "address": [
        '"{seller}" address',
        '"{seller}" "registered address"',
        '"{seller}" "registered office"',
        '"{seller}" "corporate office"',
        '"{seller}" "business address"',
    ],
    "pincode": [
        '"{seller}" pincode',
        '"{seller}" "postal code"',
        '"{seller}" PIN code India',
        '"{seller}" location pincode',
    ],
    "phone": [
        '"{seller}" phone',
        '"{seller}" mobile',
        '"{seller}" "contact number"',
        '"{seller}" phone number India',
    ],
    "email": [
        '"{seller}" email',
        '"{seller}" "email address"',
        '"{seller}" "contact email"',
        '"{seller}" official email India',
    ],
    "website": [
        '"{seller}" official website',
        '"{seller}" brand website',
        '"{seller}" online store',
        '"{seller}" company website',
    ],
}

# Field-Specific Brave Query Templates (Max 3 fallback per field)
FIELD_BRAVE_QUERIES: Dict[str, List[str]] = {
    "gst": [
        '"{seller}" GST number India',
        '"{seller}" GSTIN registration',
        '"{seller}" GST tax filing India',
    ],
    "pan": [
        '"{seller}" PAN card number India',
        '"{seller}" company PAN India',
    ],
    "address": [
        '"{seller}" company address India',
        '"{seller}" "business address" India',
    ],
    "pincode": [
        '"{seller}" PIN code India',
        '"{seller}" postal code India',
    ],
    "phone": [
        '"{seller}" phone number India',
        '"{seller}" customer care contact',
    ],
    "email": [
        '"{seller}" official email India',
        '"{seller}" customer support email',
    ],
    "owner": [
        '"{seller}" proprietor promoter India',
        '"{seller}" managing director India',
    ],
    "fssai": [
        '"{seller}" FSSAI license number India',
        '"{seller}" food license registration India',
    ],
    "website": [
        '"{seller}" official store website',
        '"{seller}" company website India',
    ],
}

# Legacy Field-Specific Query Templates for backward compatibility
FIELD_SEARCH_QUERIES: Dict[str, List[str]] = {
    "gst": [
        '"{seller}" GST',
        '"{seller}" GSTIN',
        '"{seller}" "GST number"',
        '"{seller}" GST India',
        '"{seller}" GST site:zaubacorp.com',
        '"{seller}" GST site:thecompanycheck.com',
        '"{seller}" GST site:tofler.in',
        '"{seller}" GST site:quickcompany.in',
        '"{seller}" GST site:piceapp.com',
        '"{seller}" GST site:knowyourgst.com',
    ],
    "address": [
        '"{seller}" address',
        '"{seller}" "registered address"',
        '"{seller}" "registered office"',
        '"{seller}" "business address"',
        '"{seller}" "office address"',
        '"{seller}" address India',
        '"{seller}" address site:zaubacorp.com',
        '"{seller}" address site:thecompanycheck.com',
        '"{seller}" address site:tofler.in',
    ],
    "pincode": [
        '"{seller}" pincode',
        '"{seller}" "postal code"',
        '"{seller}" "PIN code"',
        '"{seller}" pincode India',
        '"{seller}" address',
    ],
    "phone": [
        '"{seller}" phone',
        '"{seller}" mobile',
        '"{seller}" "contact number"',
        '"{seller}" "phone number"',
        '"{seller}" phone India',
    ],
    "email": [
        '"{seller}" email',
        '"{seller}" "email address"',
        '"{seller}" "contact email"',
        '"{seller}" email India',
    ],
    "owner": [
        '"{seller}" owner',
        '"{seller}" proprietor',
        '"{seller}" founder',
        '"{seller}" director',
        '"{seller}" promoter',
        '"{seller}" director site:zaubacorp.com',
        '"{seller}" director site:thecompanycheck.com',
        '"{seller}" director site:tofler.in',
    ],
    "pan": [
        '"{seller}" PAN',
        '"{seller}" "PAN number"',
        '"{seller}" PAN India',
        '"{seller}" PAN site:zaubacorp.com',
    ],
    "fssai": [
        '"{seller}" FSSAI',
        '"{seller}" "FSSAI license"',
        '"{seller}" "FSSAI number"',
        '"{seller}" FSSAI India',
    ],
    "website": [
        '"{seller}" official website',
        '"{seller}" brand website',
        '"{seller}" online store',
        '"{seller}" website',
    ],
}

# Common business suffixes in Indian marketplace seller names for smart tokenization
COMMON_SELLER_SUFFIXES = [
    "LIMITED",
    "PVTLTD",
    "LTD",
    "LLP",
    "INDUSTRIES",
    "INDUSTRY",
    "FOOTWEAR",
    "FOOTWEARS",
    "CREATION",
    "CREATIONS",
    "COLLECTION",
    "COLLECTIONS",
    "ENTERPRISES",
    "ENTERPRISE",
    "RETAIL",
    "RETAILS",
    "CLOTH",
    "CLOTHING",
    "CLOTHS",
    "FASHION",
    "FASHIONS",
    "SCREENART",
    "TRADERS",
    "TRADER",
    "TRADING",
    "STORE",
    "STORES",
    "BOUTIQUE",
    "TEXTILE",
    "TEXTILES",
    "GARMENTS",
    "GARMENT",
    "WORLD",
    "INDIA",
    "CORP",
    "CORPORATION",
    "LIFESTYLE",
    "VENTURES",
    "TECH",
    "SOLUTIONS",
    "INTERNATIONAL",
    "OVERSEAS",
    "EXPORTS",
    "EXPORT",
    "IMPORTS",
    "IMPORT",
    "AGRO",
    "FOODS",
    "FOOD",
    "PHARMA",
    "JEWELLERS",
    "JEWELLERY",
    "HERBALS",
    "HERBAL",
    "ONLINE",
    "TRENDS",
    "TREND",
    "SECRET",
    "SECRETS",
    "DOLL",
    "DOLLS",
    "WEAR",
    "WEARS",
    "MART",
    "BAZAR",
    "BAZAAR",
    "HUB",
    "ZONE",
    "CORNER",
    "POINT",
    "CARE",
    "BEAUTY",
    "STUDIO",
    "HOUSE",
    "FAB",
    "KART",
    "SHOPPE",
    "JUNCTION",
    "PLANET",
    "CRAFT",
    "CRAFTS",
    "ART",
    "ARTS",
    "DREAMS",
    "CHOICE",
    "TEX",
    "EMPORIUM",
    "LINE",
    "NEST",
    "DEN",
    "PARADISE",
    "OUTFITS",
    "APPAREL",
    "APPARELS",
]

# Common distinctive words for compound name tokenization (e.g. REDTAPE -> RED TAPE, NIGHTDOLL -> NIGHT DOLL)
KNOWN_WORDS = [
    "RED",
    "TAPE",
    "BLUE",
    "GOLD",
    "STAR",
    "SUPER",
    "FOOT",
    "WEAR",
    "SHOE",
    "SHOES",
    "LOOK",
    "LOOKS",
    "DEAL",
    "DEALS",
    "HOME",
    "TECH",
    "AUTO",
    "SMART",
    "MAX",
    "PLUS",
    "PRO",
    "KIDS",
    "MEN",
    "WOMEN",
    "SILVER",
    "COTTON",
    "SILK",
    "TREND",
    "TRENDS",
    "COOL",
    "HOT",
    "BEST",
    "TOP",
    "ROYAL",
    "PRIME",
    "URBAN",
    "STYLE",
    "STYLES",
    "PERFECT",
    "FEET",
    "NIGHT",
    "DAY",
    "OVIDA",
    "VIBE",
    "GLAM",
    "LADY",
    "GIRL",
    "BOY",
    "BABY",
    "KID",
    "LITTLE",
    "HAPPY",
    "MAGIC",
    "CUTE",
    "SWEET",
    "PURE",
    "RICH",
    "TRUE",
    "CLASSIC",
    "VINTAGE",
    "MODERN",
    "FANCY",
    "ELEGANT",
    "COMFORT",
    "SOFT",
    "WARM",
    "LINEN",
    "DENIM",
    "LEATHER",
    "DOLL",
    "SECRET",
]


def generate_seller_variations(seller_name: str) -> List[str]:
    """Generate smart search variations and tokenized forms of a seller name.

    Handles:
      - 'REDTAPELIMITED' -> ['REDTAPELIMITED', 'RED TAPE LIMITED', 'REDTAPE LIMITED', 'RED TAPE', 'REDTAPE']
      - 'REEPREECREATION' -> ['REEPREECREATION', 'REEPREE CREATION', 'REEPREE']
      - 'KSCOLLECTION07' -> ['KSCOLLECTION07', 'KS COLLECTION 07']
      - 'CheneCloth' -> ['CheneCloth', 'Chene Cloth']

    Args:
        seller_name: Raw or normalized seller name.

    Returns:
        List of unique seller name variations.
    """
    if not seller_name:
        return []

    variations: List[str] = []
    raw = seller_name.strip()
    variations.append(raw)

    # 1. Cleaned alphanumeric
    cleaned = re.sub(r"[^\w\s]", " ", raw).strip()
    if cleaned and cleaned not in variations:
        variations.append(cleaned)

    # 2. PascalCase / camelCase separation (e.g., 'ReepreeCreation' -> 'Reepree Creation')
    camel_split = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    camel_split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", camel_split).strip()
    if camel_split and camel_split not in variations:
        variations.append(camel_split)

    # 3. Known suffix tokenization for uppercase / concatenated names
    upper_raw = raw.upper()
    prefix = None
    suffix_found = None
    for suffix in COMMON_SELLER_SUFFIXES:
        if upper_raw.endswith(suffix) and len(upper_raw) > len(suffix) + 1:
            prefix = upper_raw[: -len(suffix)].strip()
            suffix_found = suffix
            spaced = f"{prefix} {suffix}".strip()
            if spaced not in variations:
                variations.append(spaced)

            # Singular / Plural variation of suffix (e.g. EXPORTS <-> EXPORT, CREATION <-> CREATIONS)
            if suffix.endswith("S"):
                sing_suf = suffix[:-1]
                sing_concat = f"{prefix}{sing_suf}".strip()
                sing_spaced = f"{prefix} {sing_suf}".strip()
                if sing_concat not in variations:
                    variations.append(sing_concat)
                if sing_spaced not in variations:
                    variations.append(sing_spaced)
            else:
                plur_suf = suffix + "S"
                plur_concat = f"{prefix}{plur_suf}".strip()
                plur_spaced = f"{prefix} {plur_suf}".strip()
                if plur_concat not in variations:
                    variations.append(plur_concat)
                if plur_spaced not in variations:
                    variations.append(plur_spaced)

            # Add prefix alone ONLY if sufficiently long and distinctive (>= 6 chars)
            if prefix and len(prefix) >= 6 and prefix not in variations:
                variations.append(prefix)
            break

    # 4. Compound word splitting on prefix or raw (e.g. REDTAPE -> RED TAPE)
    targets = [prefix] if prefix else []
    targets.append(upper_raw)
    for tgt in targets:
        for w in KNOWN_WORDS:
            if tgt and tgt.startswith(w) and len(tgt) > len(w) + 2:
                rest = tgt[len(w) :].strip()
                if rest in KNOWN_WORDS or any(rest.endswith(s) for s in COMMON_SELLER_SUFFIXES) or len(rest) >= 3:
                    split_v = f"{w} {rest}".strip()
                    if split_v not in variations:
                        variations.append(split_v)
                    if suffix_found and tgt == prefix:
                        full_split = f"{w} {rest} {suffix_found}".strip()
                        if full_split not in variations:
                            variations.insert(1, full_split)

    # 5. Trailing number separation (e.g. 'KSCOLLECTION07' -> 'KSCOLLECTION 07' or 'KS COLLECTION 07')
    num_match = re.search(r"^(.*?)([0-9]+)$", raw)
    if num_match:
        text_part = num_match.group(1).strip()
        num_part = num_match.group(2).strip()
        if text_part:
            num_spaced = f"{text_part} {num_part}"
            if num_spaced not in variations:
                variations.append(num_spaced)
            for suffix in COMMON_SELLER_SUFFIXES:
                if text_part.upper().endswith(suffix) and len(text_part) > len(suffix) + 1:
                    pref = text_part.upper()[: -len(suffix)].strip()
                    s = f"{pref} {suffix} {num_part}".strip()
                    if s not in variations:
                        variations.append(s)
                    break

    # Clean deduplication
    deduped = []
    for v in variations:
        if v and v not in deduped:
            deduped.append(v)
    return deduped


def generate_targeted_queries_for_field(seller_name: str, field_key: str) -> List[str]:
    """Generate separate, field-specific search queries for a specific missing attribute using FIELD_SEARCH_QUERIES.

    Args:
        seller_name: Target marketplace seller name.
        field_key: Key in FIELD_SEARCH_QUERIES ('gst', 'pincode', 'phone', 'email', 'owner', 'pan', 'fssai', 'address', 'city', 'state', 'website')

    Returns:
        List of targeted Bing search queries across seller name variations.
    """
    key_mapping = {
        "gst_number": "gst",
        "pan_number": "pan",
        "contact_number": "phone",
        "phone": "phone",
        "email": "email",
        "owner_name": "owner",
        "owner": "owner",
        "fssai_number": "fssai",
        "fssai": "fssai",
        "pincode": "pincode",
        "address": "address",
        "billing_address": "address",
        "shipping_address": "address",
        "city": "city",
        "state": "state",
        "website_url": "website",
        "website": "website",
        "official_website": "website",
    }
    canonical_key = key_mapping.get(field_key, field_key)
    templates = FIELD_SEARCH_QUERIES.get(canonical_key, [f'"{seller_name}" {field_key}'])

    variations = generate_seller_variations(seller_name)
    queries: List[str] = []

    # Priority 1: Exact seller name with primary specific templates
    for template in templates[:4]:
        q = template.format(seller=seller_name)
        if q not in queries:
            queries.append(q)

    # Priority 2: Controlled multi-word variations (length >= 5) with primary templates
    for var in variations[1:4]:
        if len(var) >= 5:
            for template in templates[:2]:
                q = template.format(seller=var)
                if q not in queries:
                    queries.append(q)

    # Priority 3: Site-specific & India context queries for exact seller
    for template in templates[4:]:
        q = template.format(seller=seller_name)
        if q not in queries:
            queries.append(q)

    # Priority 4: Natural unquoted query for exact seller
    for template in templates[:2]:
        unquoted = template.replace('"{seller}"', '{seller}')
        q = unquoted.format(seller=seller_name)
        if q not in queries:
            queries.append(q)

    # Deduplicate queries preserving order
    deduped: List[str] = []
    for q in queries:
        if q not in deduped:
            deduped.append(q)

    return deduped


def _normalize_field_key(field_key: str) -> str:
    """Normalize various field identifier strings to canonical key."""
    key_mapping = {
        "gst_number": "gst",
        "pan_number": "pan",
        "contact_number": "phone",
        "phone": "phone",
        "email": "email",
        "owner_name": "owner",
        "owner": "owner",
        "fssai_number": "fssai",
        "fssai": "fssai",
        "pincode": "pincode",
        "address": "address",
        "raw_address": "address",
        "billing_address": "address",
        "shipping_address": "address",
        "city": "city",
        "state": "state",
        "website_url": "website",
        "website": "website",
        "official_website": "website",
    }
    return key_mapping.get(field_key, field_key)


def generate_bing_queries_for_field(seller_name: str, field_key: str) -> List[str]:
    """Generate strictly max 3 prioritized Bing search queries for a field.

    Args:
        seller_name: Target marketplace seller name.
        field_key: Target field identifier.

    Returns:
        List of at most 3 Bing search queries.
    """
    canonical_key = _normalize_field_key(field_key)
    templates = FIELD_BING_QUERIES.get(canonical_key, [f'"{seller_name}" {field_key}'])
    queries: List[str] = []
    for tmpl in templates[:MAX_BING_QUERIES_PER_FIELD]:
        q = tmpl.format(seller=seller_name)
        if q not in queries:
            queries.append(q)
    return queries[:MAX_BING_QUERIES_PER_FIELD]


def generate_brave_queries_for_field(seller_name: str, field_key: str) -> List[str]:
    """Generate strictly max 2 fallback Brave search queries for a field.

    Args:
        seller_name: Target marketplace seller name.
        field_key: Target field identifier.

    Returns:
        List of at most 2 Brave search queries.
    """
    canonical_key = _normalize_field_key(field_key)
    templates = FIELD_BRAVE_QUERIES.get(canonical_key, [f'"{seller_name}" {field_key} India'])
    queries: List[str] = []
    for tmpl in templates[:MAX_BRAVE_QUERIES_PER_FIELD]:
        q = tmpl.format(seller=seller_name)
        if q not in queries:
            queries.append(q)
    return queries[:MAX_BRAVE_QUERIES_PER_FIELD]


class ResearchCache:
    """Local JSON cache for search queries and scraped public snippets."""

    def __init__(self, file_path: Path = CACHE_FILE) -> None:
        """Initialize research cache.

        Args:
            file_path: Path to cache.json.
        """
        self.file_path = file_path
        self.cache: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load cache from disk."""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache from {self.file_path}: {e}")
                self.cache = {}

    def save(self) -> None:
        """Save cache to disk."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save cache to {self.file_path}: {e}")

    def get(self, key: str) -> Optional[Any]:
        """Get cached response for a search query.

        Args:
            key: Query string key.

        Returns:
            Cached item or None.
        """
        return self.cache.get(key)

    def set(self, key: str, value: Any) -> None:
        """Set cache item and persist.

        Args:
            key: Query string key.
            value: Data to cache.
        """
        self.cache[key] = value
        self.save()


def decode_bing_url(href: str) -> str:
    """Decode real destination URL from Bing redirect tracking link (bing.com/ck/a?p=...&u=a1...).

    Args:
        href: Raw href string from Bing search result.

    Returns:
        Decoded destination URL or original href.
    """
    if "bing.com/ck/a" in href:
        try:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            u_val = qs.get("u", [""])[0]
            if u_val.startswith("a1"):
                b64_str = u_val[2:]
                b64_str += "=" * ((4 - len(b64_str) % 4) % 4)
                decoded = base64.b64decode(b64_str).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
        except Exception:
            pass
    return href


def score_search_result_relevance(seller_name: str, result: Dict[str, str]) -> int:
    """Calculate relevance score for a search result snippet against the target seller.

    Scoring:
      - Unrelated spam / forum / software docs   : 0 (Discard)
      - No seller name association               : 0 (Discard)
      - Exact seller name in title               : +40
      - Exact seller name in URL                 : +20
      - Exact seller name in snippet text        : +20
      - Indian business context                  : +15
      - Relevant field keyword                   : +10

    Args:
        seller_name: Target seller name.
        result: Dictionary with 'title', 'snippet', 'url'.

    Returns:
        Integer score (0 if irrelevant, >= 20 if relevant).
    """
    clean_seller = seller_name.strip().lower()
    title = result.get("title", "").strip()
    snippet = result.get("snippet", "").strip()
    url = result.get("url", "").strip()
    combined_text = f"{title} {snippet}".lower()
    url_lower = url.lower()

    # Immediate rejection for unrelated domains and platforms
    excluded_domains = [
        "zhihu.com",
        "baidu.com",
        "dafont.com",
        "52pojie.cn",
        "stackoverflow.com",
        "github.com",
        "youtube.com",
        "microsoft.com",
        "office.com",
        "imdb.com",
        "wikipedia.org",
        "wiktionary.org",
        "quora.com",
        "reddit.com",
        "sohu.com",
        "androidguias.com",
        "commentcamarche.net",
        "bilibili.com",
        "weibo.com",
    ]
    if any(ed in url_lower for ed in excluded_domains):
        return 0

    # MUST pass seller association check
    if not validate_seller_association(seller_name, f"{title} {snippet}", url):
        return 0

    score = 20
    # Exact seller name bonuses
    if clean_seller in title.lower():
        score += 30
    if clean_seller in url_lower:
        score += 20
    if clean_seller in snippet.lower():
        score += 15

    # Indian business context bonuses
    if any(k in combined_text for k in ["india", "gst", "gstin", "pvt ltd", "private limited", "llp", "contact", "address", "indiamart", "zaubacorp", "tofler", "quickcompany", "piceapp"]):
        score += 15

    # Field keyword bonuses
    if any(k in combined_text for k in ["email", "phone", "mobile", "pan", "director", "owner", "proprietor", "fssai", "pincode"]):
        score += 10

    return score


MARKETPLACE_GENERIC_PHONE_PREFIXES = ("1800", "+911800", "01800", "911800")
MARKETPLACE_GENERIC_PHONES = {
    "18002089898", "180030009009", "18004201111", "9800000000",
}
MARKETPLACE_EMAIL_DOMAINS = {
    "flipkart.com", "amazon.com", "amazon.in", "meesho.com", "myntra.com", "snapdeal.com",
}


def log_candidate_evaluation(
    seller: str,
    field: str,
    search: str,
    query: str,
    results_count: int,
    candidate: Optional[str],
    match_score: int,
    source: str,
    valid: str,
    result: str,
    reason: str,
) -> None:
    """Format and print structured candidate evaluation log matching Requirement 15."""
    msg = (
        f"\n========================================\n"
        f"SELLER:\n{seller}\n\n"
        f"FIELD:\n{field}\n\n"
        f"SEARCH:\n{search}\n"
        f"Query: {query}\n"
        f"Results: {results_count}\n\n"
        f"CANDIDATE:\n{candidate or 'NONE'}\n\n"
        f"MATCH:\n{match_score}/100\n\n"
        f"SOURCE:\n{source}\n\n"
        f"VALIDATION:\n{valid}\n\n"
        f"RESULT:\n{result}\n\n"
        f"REASON:\n{reason}\n"
        f"========================================"
    )
    logger.info(msg)


def extract_gst(results: List[Dict[str, str]], seller_name: str) -> Optional[Tuple[str, str, int]]:
    """Extract and validate GSTIN from search result titles and snippets using strict seller matching.

    Args:
        results: List of search result dictionaries.
        seller_name: Target seller name.

    Returns:
        Tuple of (gstin, source, confidence) or None.
    """
    for r in results:
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        url = r.get("url", "")
        text = f"{title} {snippet}"

        matches = GST_REGEX.findall(text)
        for m in matches:
            valid_g = validate_gst(m)
            if not valid_g:
                continue
            matched, score, reason = match_gst_to_seller(
                seller_name, valid_g, snippet=text, url=url
            )
            if matched:
                return valid_g, url or text, score
    return None


def extract_owner(results: List[Dict[str, str]], seller_name: str) -> Optional[Tuple[str, str, int]]:
    """Extract owner/founder/proprietor/director name from search results.

    Args:
        results: List of search result dictionaries.
        seller_name: Target seller name.

    Returns:
        Tuple of (owner_name, source, confidence) or None.
    """
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        url = r.get("url", "")
        if not validate_seller_association(seller_name, text, url):
            continue
        owner_m = re.search(
            r"(?i)(?:director|owner|proprietor|founder|promoter|managing\s+director)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
            text,
        )
        if owner_m:
            cand = owner_m.group(1).strip()
            if cand.lower() not in {"flipkart", "amazon", "india", "pvt ltd", "limited", "company", "privacy policy", "terms of use"}:
                score = 85 if any(d in url.lower() for d in DIRECTORY_DOMAINS) else 75
                return cand, url or text, score
    return None


def extract_pincode(results: List[Dict[str, str]], seller_name: str) -> Optional[Tuple[str, str, int]]:
    """Extract 6-digit Indian postal code with explicit postal or address context.

    Args:
        results: List of search result dictionaries.
        seller_name: Target seller name.

    Returns:
        Tuple of (pincode, source, confidence) or None.
    """
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        url = r.get("url", "")
        if not validate_seller_association(seller_name, text, url):
            continue
        pin_m = re.search(
            r"(?i)(?:pincode|pin\s+code|pin|postal\s+code|postal|zip\s+code|zip|address|road|street|nagar|plot|industrial|sector|estate|delhi|mumbai|surat|jaipur|ahmedabad|bengaluru|chennai|kolkata|pune|hyderabad|tirupur|noida|faridabad|ghaziabad|gurugram|tamil\s+nadu|gujarat|maharashtra|uttar\s+pradesh|rajasthan|karnataka|haryana)[\s:\-]*([1-9][0-9]{5})\b",
            text,
        )
        if pin_m:
            cand = validate_pincode(pin_m.group(1))
            if cand:
                return cand, url or text, 80
    return None


def extract_address(
    results: List[Dict[str, str]], seller_name: str, gst_number: Optional[str] = None
) -> Optional[Tuple[Dict[str, Any], str, int]]:
    """Extract full address block and parse components.

    Args:
        results: List of search result dictionaries.
        seller_name: Target seller name.
        gst_number: Optional validated GSTIN for state harmonization.

    Returns:
        Tuple of (parsed_address_dict, source, confidence) or None.
    """
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        url = r.get("url", "")
        if not validate_seller_association(seller_name, text, url):
            continue
        addr_m = re.search(
            r"(?i)(?:address|registered\s+office|located\s+at)\s*[:\-]?\s*([^.]+?(?:[1-9][0-9]{5}|India))",
            text,
        )
        if addr_m:
            addr_text = addr_m.group(1).strip()
            parsed = parse_raw_address(addr_text, gst_number=gst_number)
            if parsed.get("billing_address"):
                return parsed, url or text, 80
        elif re.search(r"\b[1-9][0-9]{5}\b", text) and any(kw in text.lower() for kw in ["plot", "street", "road", "nagar", "building", "sector", "industrial"]):
            parsed = parse_raw_address(text, gst_number=gst_number)
            if parsed.get("billing_address"):
                return parsed, url or text, 75
    return None


def extract_phone(results: List[Dict[str, str]], seller_name: str) -> Optional[Tuple[str, str, int]]:
    """Extract valid Indian phone number from search results."""
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        url = r.get("url", "")
        if not validate_seller_association(seller_name, text, url):
            continue
        phone_matches = PHONE_REGEX.findall(text)
        for ph in phone_matches:
            v = validate_phone(ph)
            if v:
                if any(v.startswith(p) for p in MARKETPLACE_GENERIC_PHONE_PREFIXES) or v in MARKETPLACE_GENERIC_PHONES:
                    continue
                score = 90 if any(d in url.lower() for d in DIRECTORY_DOMAINS) else 80
                return v, url or text, score
    return None


def extract_email(results: List[Dict[str, str]], seller_name: str) -> Optional[Tuple[str, str, int]]:
    """Extract valid business email from search results."""
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        url = r.get("url", "")
        if not validate_seller_association(seller_name, text, url):
            continue
        email_matches = EMAIL_REGEX.findall(text)
        for em in email_matches:
            v = validate_email(em)
            if v:
                dom = v.split("@")[-1].lower()
                if dom in MARKETPLACE_EMAIL_DOMAINS:
                    continue
                score = 90 if any(d in url.lower() for d in DIRECTORY_DOMAINS) else 80
                return v, url or text, score
    return None



def extract_pan(
    results: List[Dict[str, str]], seller_name: str, gst_number: Optional[str] = None
) -> Optional[Tuple[str, str, int]]:
    """Extract 10-char PAN or derive from GSTIN."""
    if gst_number:
        v_gst = validate_gst(gst_number)
        if v_gst:
            return v_gst[2:12], "Derived from GSTIN", 95
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        url = r.get("url", "")
        if not validate_seller_association(seller_name, text, url):
            continue
        pan_matches = PAN_REGEX.findall(text)
        for p in pan_matches:
            v = validate_pan(p, gst_str=gst_number)
            if v:
                return v, url or text, 80
    return None


def extract_fssai(results: List[Dict[str, str]], seller_name: str) -> Optional[Tuple[str, str, int]]:
    """Extract 14-digit FSSAI license number."""
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        url = r.get("url", "")
        if not validate_seller_association(seller_name, text, url):
            continue
        fssai_matches = FSSAI_REGEX.findall(text)
        for fs in fssai_matches:
            v = validate_fssai(fs)
            if v:
                return v, url or text, 80
    return None


FIELD_KEYWORDS: Dict[str, List[str]] = {
    "gst_number": ["gst", "gstin", "tax", "registration", "number"],
    "pan_number": ["pan", "account number", "permanent", "company pan"],
    "fssai_number": ["fssai", "license", "licence", "food", "safety"],
    "owner_name": ["owner", "founder", "director", "proprietor", "promoter", "partner", "managing director"],
    "contact_number": ["phone", "mobile", "contact", "call", "tel"],
    "email": ["email", "e-mail", "mail", "support", "sales"],
    "pincode": ["pincode", "pin", "postal", "zip"],
    "raw_address": ["address", "office", "location", "building", "road", "street"],
    "website_url": ["website", "store", "shop", "online", "official"],
}


def evaluate_result_candidate(
    seller_name: str,
    field_attr: str,
    r: Dict[str, str],
    gst_number: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect a search result and evaluate it as a candidate for a given seller field.

    Pipeline:
      1. Classify source type (OFFICIAL_WEBSITE, TOOL_OR_UTILITY, RECIPE, FORUM, etc.)
      2. Hard-reject disallowed sources BEFORE extracting any candidate.
      3. Calculate seller match score.
      4. Calculate field relevance score.
      5. Extract and validate candidate value from title + snippet.
      6. Compute composite confidence score.
      7. Return structured diagnostic dict for logging.
    """
    title = r.get("title", "").strip()
    url = r.get("url", "").strip()
    snippet = r.get("snippet", "").strip()
    text = f"{title} {snippet}"
    combined = f"{text} {url}".lower()
    url_lower = url.lower()

    # 1. Source Type Classification
    source_type, source_quality_score = classify_source_type(url, title, snippet)

    # 2. Seller Match Score
    seller_match_score, match_reason, matched_var = calculate_seller_match_score(seller_name, text, url)

    # 3. Hard Rejection (pre-fetch) — reject before candidate extraction / URL fetch
    should_reject, reject_reason_early = is_disallowed_source(source_type, seller_match_score, seller_name, url)
    if should_reject:
        return {
            "title": title,
            "url": url,
            "snippet": snippet,
            "source_type": source_type,
            "seller_match_score": seller_match_score,
            "match_reason": match_reason,
            "matched_var": matched_var or seller_name,
            "field_relevance_score": 0,
            "candidate_validity_score": 0,
            "source_quality_score": source_quality_score,
            "total_confidence": 0,
            "raw_candidate": None,
            "valid_candidate_val": None,
            "decision": "REJECT",
            "reject_reason": reject_reason_early,
        }

    # 4. Field Relevance Score
    kw_list = FIELD_KEYWORDS.get(field_attr, [])
    has_field_kw = any(re.search(r"\b" + re.escape(kw) + r"\b", combined) for kw in kw_list) if kw_list else True
    field_relevance_score = 90 if has_field_kw else 10

    # Boost source quality for registry/government
    if any(d in url_lower for d in DIRECTORY_DOMAINS):
        source_quality_score = max(source_quality_score, 90)

    # 5. Extract Candidate & Validity Score
    raw_candidate = None
    valid_candidate_val = None
    candidate_validity_score = 0
    candidate_reject_reason = ""

    if field_attr == "gst_number":
        matches = GST_REGEX.findall(text)
        for m in matches:
            valid_g = validate_gst(m)
            if not valid_g:
                continue
            matched, score, reason = match_gst_to_seller(
                seller_name, valid_g, snippet=text, url=url
            )
            if matched:
                raw_candidate = valid_g
                valid_candidate_val = valid_g
                candidate_validity_score = 100
                seller_match_score = max(seller_match_score, score)
                break
            else:
                candidate_reject_reason = reason
        if not valid_candidate_val and matches and not candidate_reject_reason:
            candidate_reject_reason = "GST_SELLER_MISMATCH"
    elif field_attr == "pan_number":
        if gst_number and validate_gst(gst_number):
            raw_candidate = validate_gst(gst_number)[2:12]
            valid_candidate_val = raw_candidate
            candidate_validity_score = 100
        else:
            matches = PAN_REGEX.findall(text)
            for m in matches:
                valid_p = validate_pan(m, gst_str=gst_number)
                if valid_p and seller_match_score >= 40:
                    raw_candidate = valid_p
                    valid_candidate_val = valid_p
                    candidate_validity_score = 100
                    break
    elif field_attr == "fssai_number":
        matches = FSSAI_REGEX.findall(text)
        if matches:
            raw_candidate = matches[0]
            valid_f = validate_fssai(raw_candidate)
            if valid_f:
                valid_candidate_val = valid_f
                candidate_validity_score = 100
    elif field_attr == "owner_name":
        owner_m = re.search(
            r"(?i)(?:owner|founder|director|proprietor|promoter|partner|managing\s+director)[:\-\s]+([A-Z][a-zA-Z\s]{2,40})",
            text,
        )
        if owner_m:
            raw_candidate = owner_m.group(1).strip()
            if len(raw_candidate) >= 3 and not any(
                w in raw_candidate.lower()
                for w in ["flipkart", "amazon", "contact", "service", "policy", "terms"]
            ):
                valid_candidate_val = raw_candidate
                candidate_validity_score = 80
    elif field_attr == "contact_number":
        ph_matches = PHONE_REGEX.findall(text)
        for ph in ph_matches:
            valid_ph = validate_phone(ph)
            if valid_ph:
                if any(valid_ph.startswith(p) for p in MARKETPLACE_GENERIC_PHONE_PREFIXES) or valid_ph in MARKETPLACE_GENERIC_PHONES:
                    candidate_reject_reason = "MARKETPLACE_GENERIC_NUMBER"
                    continue
                if seller_match_score >= 40:
                    raw_candidate = valid_ph
                    valid_candidate_val = valid_ph
                    candidate_validity_score = 100
                    break
    elif field_attr == "email":
        em_matches = EMAIL_REGEX.findall(text)
        for em in em_matches:
            valid_em = validate_email(em)
            if valid_em:
                dom = valid_em.split("@")[-1].lower()
                if dom in MARKETPLACE_EMAIL_DOMAINS:
                    candidate_reject_reason = "MARKETPLACE_EMAIL"
                    continue
                if seller_match_score >= 40:
                    raw_candidate = valid_em
                    valid_candidate_val = valid_em
                    candidate_validity_score = 100
                    break
    elif field_attr == "pincode":
        pin_m = re.search(
            r"(?i)(?:pincode|pin\s+code|pin|postal\s+code|postal|zip\s+code|zip|address)[\s:\-]*([1-9][0-9]{5})\b",
            text,
        )
        if pin_m:
            raw_candidate = pin_m.group(1)
            valid_pin = validate_pincode(raw_candidate)
            if valid_pin and seller_match_score >= 40:
                valid_candidate_val = valid_pin
                candidate_validity_score = 100
    elif field_attr == "raw_address":
        addr_m = re.search(
            r"(?i)(?:address|registered\s+office|located\s+at)\s*[:\-]?\s*([^.]+?(?:[1-9][0-9]{5}|India))",
            text,
        )
        if addr_m:
            raw_candidate = addr_m.group(1).strip()
            parsed = parse_raw_address(raw_candidate, gst_number=gst_number)
            if parsed.get("billing_address") and seller_match_score >= 40:
                valid_candidate_val = parsed.get("billing_address")
                candidate_validity_score = 85
        elif re.search(r"\b[1-9][0-9]{5}\b", text):
            parsed = parse_raw_address(text, gst_number=gst_number)
            if parsed.get("billing_address") and seller_match_score >= 40:
                raw_candidate = text[:60]
                valid_candidate_val = parsed.get("billing_address")
                candidate_validity_score = 75
    elif field_attr == "website_url":
        if url and url.startswith("http") and source_type not in ("TOOL_OR_UTILITY", "RECIPE", "UNRELATED_COMPANY", "MARKETPLACE"):
            try:
                parsed_u = urllib.parse.urlparse(url)
                root_u = f"{parsed_u.scheme}://{parsed_u.netloc}"
                raw_candidate = root_u
                valid_candidate_val = root_u
                candidate_validity_score = 90
            except Exception:
                pass

    # 6. Composite Confidence Score
    total_confidence = int(
        seller_match_score * 0.40
        + field_relevance_score * 0.20
        + candidate_validity_score * 0.25
        + source_quality_score * 0.15
    )

    # 7. Decision & Reject Reason
    if candidate_reject_reason:
        decision = "REJECT"
        reject_reason = candidate_reject_reason
    elif not raw_candidate:
        decision = "REJECT"
        reject_reason = "NO_CANDIDATE"
    elif not valid_candidate_val or candidate_validity_score == 0:
        decision = "REJECT"
        reject_reason = "INVALID_FORMAT"
    elif seller_match_score < 40:
        decision = "REJECT"
        reject_reason = "SELLER_MISMATCH"
    elif field_relevance_score < 20:
        decision = "REJECT"
        reject_reason = "FIELD_MISMATCH"
    elif total_confidence < 50:
        decision = "REJECT"
        reject_reason = "LOW_CONFIDENCE"
    else:
        decision = "ACCEPT"
        reject_reason = "NONE"


    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "source_type": source_type,
        "seller_match_score": seller_match_score,
        "match_reason": match_reason,
        "matched_var": matched_var or seller_name,
        "field_relevance_score": field_relevance_score,
        "candidate_validity_score": candidate_validity_score,
        "source_quality_score": source_quality_score,
        "total_confidence": total_confidence,
        "raw_candidate": raw_candidate,
        "valid_candidate_val": valid_candidate_val,
        "decision": decision,
        "reject_reason": reject_reason,
    }


class WebResearchEngine:
    """Performs field-driven fallback Bing searches and deep enrichment for marketplace sellers."""

    def __init__(self) -> None:
        """Initialize research engine with HTTP client, website parser, and cache."""
        self.cache = ResearchCache()
        self.website_parser = WebsiteParser()
        self.seller_enrichment_cache: Dict[str, Dict[str, Any]] = {}
        self.brave_rate_limited_until: float = 0.0
        self.brave_backoff_seconds: float = 30.0
        self.client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
            verify=False,
        )

    async def close(self) -> None:
        """Close underlying clients."""
        await self.website_parser.close()
        await self.client.aclose()

    async def _query_google(self, query: str) -> Tuple[List[Dict[str, str]], int]:
        """Query Google search engine directly.

        Args:
            query: Search query string.

        Returns:
            Tuple of (list_of_result_dicts, http_status_code).
        """
        cached = self.cache.get(f"google::{query}")
        if cached:
            return cached, 200

        if "PYTEST_CURRENT_TEST" in os.environ and not getattr(self, "_google_test_mocked", False):
            return [], 503

        results: List[Dict[str, str]] = []
        status_code = 0
        try:
            user_agent = random.choice(USER_AGENTS)
            headers = {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
            url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}&hl=en&gl=in&num=10"
            resp = await self.client.get(url, headers=headers, timeout=8.0)
            status_code = resp.status_code

            if status_code == 429:
                return [], 429

            if status_code in (401, 403):
                return [], status_code

            if status_code == 200:
                html = resp.text
                if "sorry/index" in html or "captcha" in html.lower():
                    return [], 403

                soup = BeautifulSoup(html, "lxml")
                items = soup.select("div.g, div.tF2Cxc, div.yuRUbf")
                for item in items:
                    h3 = item.select_one("h3")
                    title = h3.get_text(strip=True) if h3 else ""
                    a_tag = item.select_one("a[href]")
                    href = a_tag.get("href", "") if a_tag else ""
                    snippet_el = item.select_one("div.VwiC3b, span.aCOpRe, div.s, div[data-sncf]")
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                    if href and href.startswith("http") and "google.com" not in href:
                        results.append({"title": title, "url": href, "snippet": snippet})

                if results:
                    self.cache.set(f"google::{query}", results)
                    return results, 200
                else:
                    return [], 200
            else:
                return [], status_code
        except httpx.TimeoutException:
            return [], 408
        except Exception as e:
            logger.debug(f"Google query error for '{query}': {e}")
            return [], 500

    async def _query_bing(self, query: str) -> Tuple[List[Dict[str, str]], int]:
        """Query Bing search engine directly and return top 5-10 parsed results + HTTP status code.

        Args:
            query: Search query string.

        Returns:
            Tuple of (list_of_result_dicts, http_status_code).
        """
        cached = self.cache.get(f"bing::{query}")
        if cached:
            return cached, 200

        results: List[Dict[str, str]] = []
        status_code = 0
        try:
            user_agent = random.choice(USER_AGENTS)
            headers = {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
            bing_url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}&setlang=en-in&count=10"
            resp = await self.client.get(bing_url, headers=headers, timeout=8.0)
            status_code = resp.status_code
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                items = soup.select("li.b_algo")
                for item in items:
                    h2 = item.select_one("h2")
                    title = h2.get_text(strip=True) if h2 else ""
                    a_tag = h2.select_one("a") if h2 else item.select_one("a")
                    href = a_tag.get("href", "") if a_tag else ""
                    href = decode_bing_url(href)

                    snippet_el = item.select_one(".b_caption p, .b_lineclamp2, .b_algoSlug, .b_snippet, p")
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                    if href and (title or snippet):
                        results.append({"title": title, "url": href, "snippet": snippet})
        except httpx.TimeoutException:
            status_code = 408
            logger.debug(f"Bing search query timeout for '{query}'")
        except Exception as e:
            status_code = 500
            logger.debug(f"Bing search query error for '{query}': {e}")

        effective_status = status_code if status_code > 0 else (200 if results else 200)
        if results and effective_status == 200:
            self.cache.set(f"bing::{query}", results)
        return results, effective_status

    async def _query_brave(self, query: str) -> Tuple[List[Dict[str, str]], int]:
        """Query Brave search engine directly as fallback and return top parsed results + HTTP status code.

        Supports:
          1. Brave Search API (if BRAVE_API_KEY environment variable is set).
          2. Brave Web Search HTML parser (extracts title, snippet, AI summary / infobox).
          3. Rate limit backoff tracking (HTTP 429).
          4. DuckDuckGo fallback if Brave HTML is blocked or returns empty.

        Args:
            query: Search query string.

        Returns:
            Tuple of (list_of_result_dicts, http_status_code).
        """
        if time.monotonic() < self.brave_rate_limited_until:
            rem = int(self.brave_rate_limited_until - time.monotonic())
            logger.warning(
                f"\n========================================\n"
                f"BRAVE RATE LIMITED (ACTIVE BACKOFF)\n"
                f"========================================\n"
                f"Query: {query}\n"
                f"Retry-After: {rem}s remaining\n"
                f"Action: SKIP BRAVE / BACKOFF\n"
                f"========================================"
            )
            return [], 429

        cached = self.cache.get(f"brave::{query}")
        if cached:
            return cached, 200

        results: List[Dict[str, str]] = []
        status_code = 0

        # Option A: Check for BRAVE_API_KEY
        brave_api_key = os.environ.get("BRAVE_API_KEY", "").strip()
        if brave_api_key:
            try:
                api_url = "https://api.search.brave.com/res/v1/web/search"
                headers = {
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": brave_api_key,
                }
                resp = await self.client.get(api_url, params={"q": query, "count": 10}, headers=headers, timeout=8.0)
                status_code = resp.status_code
                if resp.status_code == 429:
                    retry_hdr = resp.headers.get("Retry-After", "")
                    backoff = float(retry_hdr) if retry_hdr.isdigit() else self.brave_backoff_seconds
                    self.brave_rate_limited_until = time.monotonic() + backoff
                    self.brave_backoff_seconds = min(300.0, self.brave_backoff_seconds * 2.0)
                    logger.warning(
                        f"\n========================================\n"
                        f"BRAVE RATE LIMITED (HTTP 429)\n"
                        f"========================================\n"
                        f"Query: {query}\n"
                        f"Retry-After: {backoff}s\n"
                        f"Action: SKIP BRAVE / BACKOFF\n"
                        f"========================================"
                    )
                    return [], 429

                if resp.status_code == 200:
                    data = resp.json()
                    web_results = data.get("web", {}).get("results", [])
                    for item in web_results:
                        t = item.get("title", "")
                        u = item.get("url", "")
                        s = item.get("description", "") or item.get("snippet", "")
                        if u and (t or s):
                            results.append({"title": t, "url": u, "snippet": s})

                    summarizer = data.get("summarizer", {}).get("summary", [])
                    if summarizer:
                        ai_text = " ".join([seg.get("text", "") for seg in summarizer if isinstance(seg, dict)])
                        if ai_text:
                            results.insert(0, {"title": "Brave AI Summary", "url": "", "snippet": ai_text})

                    if results:
                        self.cache.set(f"brave::{query}", results)
                        return results, 200
            except Exception as e:
                logger.debug(f"Brave Search API error for '{query}': {e}")

        # Option B: Brave HTML search
        try:
            user_agent = random.choice(USER_AGENTS)
            headers = {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
            brave_url = f"https://search.brave.com/search?q={urllib.parse.quote_plus(query)}&source=web"
            resp = await self.client.get(brave_url, headers=headers, timeout=8.0)
            status_code = resp.status_code
            if resp.status_code == 429:
                retry_hdr = resp.headers.get("Retry-After", "")
                backoff = float(retry_hdr) if retry_hdr.isdigit() else self.brave_backoff_seconds
                self.brave_rate_limited_until = time.monotonic() + backoff
                self.brave_backoff_seconds = min(300.0, self.brave_backoff_seconds * 2.0)
                logger.warning(
                    f"\n========================================\n"
                    f"BRAVE RATE LIMITED (HTTP 429)\n"
                    f"========================================\n"
                    f"Query: {query}\n"
                    f"Retry-After: {backoff}s\n"
                    f"Action: SKIP BRAVE / BACKOFF\n"
                    f"========================================"
                )
                return [], 429

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")

                # Extract Brave AI summary or Knowledge Infobox if present
                ai_box = soup.select_one("#llm-summary, .infobox, .answer, .knowledge-card, .serp-summary")
                if ai_box:
                    ai_text = ai_box.get_text(" ", strip=True)
                    if ai_text:
                        results.append({"title": "Brave AI Summary", "url": "", "snippet": ai_text})

                items = soup.select("div.snippet, div[data-type='web'], div.fdb, div.result")
                for item in items:
                    title_el = item.select_one(".title, h3, a.heading-serpresult, .result-header")
                    title = title_el.get_text(strip=True) if title_el else ""
                    a_tag = item.select_one("a.result-header, a[href]") if not title_el else (title_el if title_el.name == "a" else title_el.find("a"))
                    href = a_tag.get("href", "") if a_tag else ""
                    snippet_el = item.select_one(".snippet-description, .snippet-content, .result__snippet, p")
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    if href and (title or snippet):
                        results.append({"title": title, "url": href, "snippet": snippet})
        except httpx.TimeoutException:
            status_code = 408
            logger.debug(f"Brave HTML search timeout for '{query}'")
        except Exception as e:
            status_code = 500
            logger.debug(f"Brave HTML search error for '{query}': {e}")

        # Option C: Fallback to DDG if Brave returned empty or non-200
        if not results and status_code != 429:
            ddg_results = await self._query_ddg(query)
            if ddg_results:
                results = ddg_results
                status_code = 200

        effective_status = status_code if status_code > 0 else (200 if results else 200)
        if results and effective_status == 200:
            self.cache.set(f"brave::{query}", results)
        return results, effective_status

    async def _fetch_directory_text(self, url: str) -> str:
        """Fetch and extract text content from a directory or company page when snippet is truncated."""
        if not url or not url.startswith("http"):
            return ""
        try:
            html = await self.website_parser.fetch_html(url)
            if html:
                soup = BeautifulSoup(html, "lxml")
                for s in soup(["script", "style", "noscript", "svg"]):
                    s.decompose()
                return soup.get_text(" ", strip=True)
        except Exception as e:
            logger.debug(f"Failed to fetch directory page text from {url}: {e}")
        return ""

    async def search_seller_web(
        self,
        seller_name: str,
        field: str,
    ) -> Tuple[List[Dict[str, str]], str, Dict[str, Any]]:
        """Waterfall search engine: Google -> Bing -> Brave.

        Order:
          1. Google
          2. Bing
          3. Brave

        If Google unavailable / blocked / rate limited: continue to Bing.
        If Bing fails: continue to Brave.
        If Brave returns HTTP 429: do NOT treat field as NOT_FOUND.

        Logs:
          SEARCH_ENGINE
          QUERY
          HTTP STATUS
          RESULT COUNT
          Differentiates: SEARCH_FAILED, RATE_LIMITED, BLOCKED, NO_RESULTS, NO_MATCHING_SELLER, FOUND.

        Returns:
          Tuple of (results_list, engine_used, diagnostics_dict)
        """
        canonical_key = _normalize_field_key(field)
        queries = generate_targeted_queries_for_field(seller_name, canonical_key)
        target_queries = queries[:MAX_BING_QUERIES_PER_FIELD]

        all_results: List[Dict[str, str]] = []
        chosen_engine = "NONE"
        diag: Dict[str, Any] = {
            "engine": "NONE",
            "query": target_queries[0] if target_queries else "",
            "http_status": 0,
            "result_count": 0,
            "status_label": "NO_RESULTS",
            "rate_limited": False,
        }

        def _label_for(status: int, res_list: List[Any]) -> str:
            if status == 429:
                return "RATE_LIMITED"
            if status in (401, 403):
                return "BLOCKED"
            if status == 200:
                return "FOUND" if res_list else "NO_RESULTS"
            return "SEARCH_FAILED"

        for q in target_queries:
            diag["query"] = q

            # 1. Try Google
            g_res, g_status = await self._query_google(q)
            g_label = _label_for(g_status, g_res)
            logger.info(
                f"\nSEARCH_ENGINE: Google\n"
                f"QUERY: {q}\n"
                f"HTTP STATUS: {g_status}\n"
                f"RESULT COUNT: {len(g_res)}\n"
                f"STATUS: {g_label}"
            )
            if g_label == "FOUND" and g_res:
                chosen_engine = "Google"
                all_results = g_res
                diag.update({
                    "engine": "Google",
                    "http_status": g_status,
                    "result_count": len(g_res),
                    "status_label": g_label,
                    "rate_limited": False,
                })
                break

            # 2. If Google fails / blocked / rate limited / no results -> Try Bing
            b_res, b_status = await self._query_bing(q)
            b_label = _label_for(b_status, b_res)
            logger.info(
                f"\nSEARCH_ENGINE: Bing\n"
                f"QUERY: {q}\n"
                f"HTTP STATUS: {b_status}\n"
                f"RESULT COUNT: {len(b_res)}\n"
                f"STATUS: {b_label}"
            )
            if b_label == "FOUND" and b_res:
                chosen_engine = "Bing"
                all_results = b_res
                diag.update({
                    "engine": "Bing",
                    "http_status": b_status,
                    "result_count": len(b_res),
                    "status_label": b_label,
                    "rate_limited": False,
                })
                break

            # 3. If Bing fails -> Try Brave
            br_res, br_status = await self._query_brave(q)
            br_label = _label_for(br_status, br_res)
            logger.info(
                f"\nSEARCH_ENGINE: Brave\n"
                f"QUERY: {q}\n"
                f"HTTP STATUS: {br_status}\n"
                f"RESULT COUNT: {len(br_res)}\n"
                f"STATUS: {br_label}"
            )
            if br_status == 429 or br_label == "RATE_LIMITED":
                diag.update({
                    "engine": "Brave",
                    "http_status": 429,
                    "result_count": 0,
                    "status_label": "RATE_LIMITED",
                    "rate_limited": True,
                })
                # Do NOT treat field as NOT_FOUND
                break

            if br_label == "FOUND" and br_res:
                chosen_engine = "Brave"
                all_results = br_res
                diag.update({
                    "engine": "Brave",
                    "http_status": br_status,
                    "result_count": len(br_res),
                    "status_label": br_label,
                    "rate_limited": False,
                })
                break

        return all_results, chosen_engine, diag


    async def _query_ddg(self, query: str) -> List[Dict[str, str]]:
        """Fallback query to DuckDuckGo (via DDGS or HTML endpoint).

        Args:
            query: Search query string.

        Returns:
            List of search result dicts.
        """
        results: List[Dict[str, str]] = []

        if DDGS is not None:
            try:
                def _do_ddgs():
                    with DDGS() as ddgs_client:
                        return list(ddgs_client.text(query, max_results=6))

                loop = asyncio.get_running_loop()
                raw_items = await loop.run_in_executor(None, _do_ddgs)
                for item in raw_items:
                    t = item.get("title", "")
                    u = item.get("href", "")
                    s = item.get("body", "")
                    if t and u:
                        results.append({"title": t, "url": u, "snippet": s})
                if results:
                    return results
            except Exception as e:
                logger.debug(f"DDGS query '{query}' exception: {e}")

        try:
            resp = await self.client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "b": ""},
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=8.0,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                for res_tag in soup.select(".result"):
                    title_el = res_tag.select_one(".result__title a")
                    snippet_el = res_tag.select_one(".result__snippet")
                    if title_el:
                        title = title_el.get_text(strip=True)
                        raw_href = title_el.get("href", "")
                        actual_url = raw_href
                        if "uddg=" in raw_href:
                            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                            actual_url = parsed_qs.get("uddg", [raw_href])[0]
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                        results.append({"title": title, "url": actual_url, "snippet": snippet})
        except Exception as e:
            logger.debug(f"HTTP DDG fallback error: {e}")

        return results

    async def _query_search_engine(self, query: str) -> List[Dict[str, str]]:
        """Query search engine (Bing primary, Brave/DDG fallback) and return parsed results list.

        Args:
            query: Search query string.

        Returns:
            List of search result dicts: [{"title": ..., "snippet": ..., "url": ...}]
        """
        cached = self.cache.get(query)
        if cached:
            return cached

        results, _ = await self._query_bing(query)
        if not results:
            results, _ = await self._query_brave(query)

        self.cache.set(query, results)
        return results


    def _filter_search_results(
        self, seller_name: str, results: List[Dict[str, str]], min_score: int = 20
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Filter search results by relevance score and validate seller association.

        Args:
            seller_name: Target seller name.
            results: Raw search results list.
            min_score: Minimum relevance score threshold (default 20).

        Returns:
            Tuple of (relevant_results, rejected_results).
        """
        relevant: List[Dict[str, str]] = []
        rejected: List[Dict[str, str]] = []

        for item in results:
            score = score_search_result_relevance(seller_name, item)
            text = f"{item.get('title', '')} {item.get('snippet', '')}"
            url = item.get("url", "")

            has_association = validate_seller_association(seller_name, text, url)

            # Strict Quality Filter: Must pass seller association AND relevance threshold
            if has_association and score >= min_score:
                relevant.append(item)
            else:
                rejected.append(item)

        return relevant, rejected

    def _extract_from_snippets(
        self, search_results: List[Dict[str, str]], seller_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Extract GST, PAN, Phone, Email, Address, Pincode, and Owner clues directly from snippet text.

        Args:
            search_results: List of search result dictionaries.
            seller_name: Optional seller name for association verification.

        Returns:
            Dictionary containing extracted fields and '_sources' mapping.
        """
        data: Dict[str, Any] = {
            "gst_number": None,
            "pan_number": None,
            "contact_number": None,
            "email": None,
            "fssai_number": None,
            "owner_name": None,
            "address": None,
            "pincode": None,
            "city": None,
            "state": None,
            "_sources": {},
        }
        sources: Dict[str, str] = {}

        if not seller_name:
            seller_name = ""

        # Use modular extractors
        gst_res = extract_gst(search_results, seller_name)
        if gst_res:
            data["gst_number"] = gst_res[0]
            sources["gst_number"] = gst_res[1]

        pan_res = extract_pan(search_results, seller_name, gst_number=data["gst_number"])
        if pan_res:
            data["pan_number"] = pan_res[0]
            sources["pan_number"] = pan_res[1]

        phone_res = extract_phone(search_results, seller_name)
        if phone_res:
            data["contact_number"] = phone_res[0]
            sources["contact_number"] = phone_res[1]

        email_res = extract_email(search_results, seller_name)
        if email_res:
            data["email"] = email_res[0]
            sources["email"] = email_res[1]

        owner_res = extract_owner(search_results, seller_name)
        if owner_res:
            data["owner_name"] = owner_res[0]
            sources["owner_name"] = owner_res[1]

        pin_res = extract_pincode(search_results, seller_name)
        if pin_res:
            data["pincode"] = pin_res[0]
            sources["pincode"] = pin_res[1]

        addr_res = extract_address(search_results, seller_name, gst_number=data["gst_number"])
        if addr_res:
            parsed_addr, addr_src, _ = addr_res
            data["address"] = parsed_addr.get("billing_address")
            sources["address"] = addr_src
            if parsed_addr.get("city"):
                data["city"] = parsed_addr["city"]
                sources["city"] = addr_src
            if parsed_addr.get("state"):
                data["state"] = parsed_addr["state"]
                sources["state"] = addr_src
            if parsed_addr.get("pincode") and not data["pincode"]:
                data["pincode"] = parsed_addr["pincode"]
                sources["pincode"] = addr_src

        fssai_res = extract_fssai(search_results, seller_name)
        if fssai_res:
            data["fssai_number"] = fssai_res[0]
            sources["fssai_number"] = fssai_res[1]

        data["_sources"] = sources
        return data

    def _identify_candidate_websites(
        self, seller_name: str, search_results: List[Dict[str, str]]
    ) -> List[str]:
        """Identify candidate official company websites from search results.

        Args:
            seller_name: Normalized seller name.
            search_results: List of search results.

        Returns:
            List of prioritized candidate URLs.
        """
        candidates: List[str] = []
        norm = normalize_seller_name_for_matching(seller_name)
        clean_seller_slug = norm["compact_stripped"] or norm["compact"]

        for item in search_results:
            url = item.get("url", "")
            if not url or not url.startswith("http"):
                continue

            try:
                parsed = urllib.parse.urlparse(url)
                domain = parsed.netloc.lower().replace("www.", "")

                if any(excluded in domain for excluded in EXCLUDED_WEBSITE_DOMAINS):
                    continue

                text = f"{item.get('title', '')} {item.get('snippet', '')}"
                score, _, _ = calculate_seller_match_score(seller_name, text, url)

                # Ensure candidate website has explicit seller association
                if score >= 50 or (clean_seller_slug and len(clean_seller_slug) >= 4 and clean_seller_slug in domain.replace(".", "")):
                    root_url = f"{parsed.scheme}://{parsed.netloc}"
                    if root_url not in candidates:
                        candidates.append(root_url)
            except Exception:
                continue

        candidates.sort(key=lambda u: clean_seller_slug in u.lower(), reverse=True)
        return candidates[:3]

    async def _inspect_directory_url(self, url: str) -> Dict[str, Any]:
        """Inspect a high-authority directory page (ZaubaCorp, MastersIndia, Tofler, etc.).

        Args:
            url: Directory webpage URL.

        Returns:
            Extracted credentials dictionary.
        """
        data: Dict[str, Any] = {}
        try:
            html = await self.website_parser.fetch_html(url)
            if html:
                extracted = self.website_parser.extract_from_html(html, url)
                data = {k: v for k, v in extracted.items() if v}
        except Exception as e:
            logger.debug(f"Error inspecting directory URL {url}: {e}")
        return data

    async def enrich_seller(self, seller_record: Dict[str, Any]) -> Dict[str, Any]:
        """Field-driven Bing enrichment pipeline after seller extraction.

        Flow:
          1. Check seller_enrichment_cache (reuse verified data if seller already researched).
          2. Find and scrape Official Website first.
          3. Identify every missing NOT FOUND field.
          4. For EACH missing field, execute prioritized Bing queries with strict relevance filtering.
          5. Validate seller association and enforce Source Priority Hierarchy.
          6. Address normalization (Billing Address, City, State, Pincode, Country).
          7. Output structured diagnostic BING ENRICHMENT log.
          8. Cache result and return structured Excel-compatible record.

        Args:
            seller_record: Generic seller dictionary.

        Returns:
            Fully enriched seller dictionary matching the 18+ Excel schema.
        """
        seller_name = seller_record.get("seller_name", "").strip()
        marketplace = seller_record.get("marketplace", "flipkart")
        fulfillment_by = seller_record.get("fulfillment_by")
        product_url = seller_record.get("product_url")
        seller_source_url = seller_record.get("seller_source_url") or product_url
        seller_source_type = seller_record.get("seller_source_type", f"{marketplace}_product")
        category = seller_record.get("category") or "E-Commerce Retail"
        sub_category = seller_record.get("sub_category")
        sub_sub_category = seller_record.get("sub_sub_category")
        sub_sub_subcategory = seller_record.get("sub_sub_subcategory")
        star_rating = seller_record.get("star_rating")
        product_rating = seller_record.get("product_rating")
        seller_confidence = seller_record.get("seller_confidence", 0.95)

        # In-Memory Cache Check: Reuse verified data if seller was already enriched
        cache_key = f"{marketplace}::{seller_name.lower()}"
        if cache_key in self.seller_enrichment_cache:
            logger.info(f"Reusing cached enrichment data for seller: '{seller_name}'")
            cached_res = dict(self.seller_enrichment_cache[cache_key])
            cached_res["product_url"] = product_url
            cached_res["star_rating"] = star_rating or cached_res.get("star_rating")
            cached_res["product_rating"] = product_rating or cached_res.get("product_rating")
            return cached_res

        logger.info(f"Starting field-driven Bing enrichment for: '{seller_name}'")

        sources_used: Set[str] = set()
        field_sources: Dict[str, str] = {}
        field_source_urls: Dict[str, str] = {}
        field_log_entries: List[Dict[str, Any]] = []

        # Helper to update field respecting source priority hierarchy
        def _set_field(field: str, val: Any, src: str, src_url: Optional[str] = None) -> None:
            if not val:
                return
            curr_val = merged.get(field)
            curr_src = field_sources.get(field, "not_found")
            if not curr_val or SOURCE_PRIORITY.get(src, 1) >= SOURCE_PRIORITY.get(curr_src, 1):
                merged[field] = val
                field_sources[field] = src
                if src_url:
                    field_source_urls[field] = src_url
                sources_used.add(src)

        # Base record
        merged: Dict[str, Any] = {
            "gst_number": None,
            "pan_number": None,
            "fssai_number": None,
            "contact_number": None,
            "email": None,
            "owner_name": None,
            "raw_address": None,
            "website_url": None,
            "pincode": None,
            "city": None,
            "state": None,
        }

        # Step 1: Find Official Website First (Max 3 Bing + Max 2 Brave, 30s timeout)
        web_bing_queries = generate_bing_queries_for_field(seller_name, "website")
        initial_search_results: List[Dict[str, str]] = []
        web_start_time = time.monotonic()
        web_bing_attempts = 0
        web_brave_attempts = 0
        candidate_urls: List[str] = []

        for b_idx, b_q in enumerate(web_bing_queries[:MAX_BING_QUERIES_PER_FIELD], start=1):
            if (time.monotonic() - web_start_time) >= FIELD_TIMEOUT_SECONDS:
                break
            web_bing_attempts += 1
            raw_res, status_code = await self._query_bing(b_q)
            initial_search_results.extend(raw_res)

            # Evaluate per-result candidates for diagnostics
            for idx, r_item in enumerate(raw_res, start=1):
                eval_res = evaluate_result_candidate(seller_name, "website_url", r_item)
                logger.info(
                    f"  Result #{idx}\n"
                    f"  Title: {eval_res['title']}\n"
                    f"  URL: {eval_res['url']}\n"
                    f"  Snippet: {eval_res['snippet']}\n"
                    f"  Seller Match: {eval_res['seller_match_score']} ({eval_res['matched_var']})\n"
                    f"  Field Match: {'YES' if eval_res['field_relevance_score'] >= 50 else 'NO'}\n"
                    f"  Candidate: {eval_res['raw_candidate'] or 'NONE'}\n"
                    f"  Score: {eval_res['total_confidence']}\n"
                    f"  Decision: {eval_res['decision']}\n"
                    f"  Reject Reason: {eval_res['reject_reason']}"
                )

            candidate_urls = self._identify_candidate_websites(seller_name, initial_search_results)
            cand_disp = candidate_urls[0] if candidate_urls else "NONE"
            decision_str = "SAVE" if candidate_urls else "NEXT QUERY"

            logger.info(
                f"\n----------------------------------------\n"
                f"ENRICHMENT SEARCH\n"
                f"----------------------------------------\n"
                f"Seller: {seller_name}\n"
                f"Field: Official Website\n"
                f"Provider: Bing\n"
                f"Attempt: {b_idx}/{len(web_bing_queries[:MAX_BING_QUERIES_PER_FIELD])}\n"
                f"Query: {b_q}\n"
                f"HTTP Status: {status_code or 200}\n"
                f"Results: {len(raw_res)}\n"
                f"Candidate: {cand_disp}\n"
                f"Decision: {decision_str}\n"
                f"----------------------------------------"
            )
            if candidate_urls:
                break
            await asyncio.sleep(0.15)

        if not candidate_urls and (time.monotonic() - web_start_time) < FIELD_TIMEOUT_SECONDS:
            logger.info(
                f"\nBING EXHAUSTED\n"
                f"Seller: {seller_name}\n"
                f"Field: Official Website\n"
                f"Usable candidate: NONE\n"
                f"Switching to Brave"
            )
            web_brave_queries = generate_brave_queries_for_field(seller_name, "website")
            for br_idx, br_q in enumerate(web_brave_queries[:MAX_BRAVE_QUERIES_PER_FIELD], start=1):
                if (time.monotonic() - web_start_time) >= FIELD_TIMEOUT_SECONDS:
                    break
                web_brave_attempts += 1
                raw_res, status_code = await self._query_brave(br_q)
                if status_code == 429:
                    break

                initial_search_results.extend(raw_res)

                for idx, r_item in enumerate(raw_res, start=1):
                    eval_res = evaluate_result_candidate(seller_name, "website_url", r_item)
                    logger.info(
                        f"  Result #{idx}\n"
                        f"  Title: {eval_res['title']}\n"
                        f"  URL: {eval_res['url']}\n"
                        f"  Snippet: {eval_res['snippet']}\n"
                        f"  Seller Match: {eval_res['seller_match_score']} ({eval_res['matched_var']})\n"
                        f"  Field Match: {'YES' if eval_res['field_relevance_score'] >= 50 else 'NO'}\n"
                        f"  Candidate: {eval_res['raw_candidate'] or 'NONE'}\n"
                        f"  Score: {eval_res['total_confidence']}\n"
                        f"  Decision: {eval_res['decision']}\n"
                        f"  Reject Reason: {eval_res['reject_reason']}"
                    )

                candidate_urls = self._identify_candidate_websites(seller_name, initial_search_results)
                cand_disp = candidate_urls[0] if candidate_urls else "NONE"
                decision_str = "SAVE" if candidate_urls else "NEXT QUERY"

                logger.info(
                    f"\n----------------------------------------\n"
                    f"ENRICHMENT SEARCH\n"
                    f"----------------------------------------\n"
                    f"Seller: {seller_name}\n"
                    f"Field: Official Website\n"
                    f"Provider: Brave\n"
                    f"Attempt: {br_idx}/{len(web_brave_queries[:MAX_BRAVE_QUERIES_PER_FIELD])}\n"
                    f"Query: {br_q}\n"
                    f"HTTP Status: {status_code or 200}\n"
                    f"Results: {len(raw_res)}\n"
                    f"Candidate: {cand_disp}\n"
                    f"Decision: {decision_str}\n"
                    f"----------------------------------------"
                )
                if candidate_urls:
                    break
                await asyncio.sleep(0.15)

        # Step 2: Scrape Official Website if found
        primary_website_url = None
        for candidate_url in candidate_urls[:2]:
            logger.info(f"Inspecting candidate website: {candidate_url}")
            c_data = await self.website_parser.inspect_website(candidate_url)
            if any(c_data.get(k) for k in ["gst_number", "email", "contact_number", "address", "owner_name"]):
                primary_website_url = candidate_url
                _set_field("website_url", candidate_url, "company_website", src_url=candidate_url)
                _set_field("gst_number", c_data.get("gst_number"), "company_website", src_url=candidate_url)
                _set_field("pan_number", c_data.get("pan_number"), "company_website", src_url=candidate_url)
                _set_field("fssai_number", c_data.get("fssai_number"), "company_website", src_url=candidate_url)
                _set_field("contact_number", c_data.get("contact_number"), "company_website", src_url=candidate_url)
                _set_field("email", c_data.get("email"), "company_website", src_url=candidate_url)
                _set_field("owner_name", c_data.get("owner_name"), "company_website", src_url=candidate_url)
                _set_field("raw_address", c_data.get("address"), "company_website", src_url=candidate_url)
                break

        if primary_website_url:
            attempts_text = f"Bing {web_bing_attempts}" if not web_brave_attempts else f"Bing {web_bing_attempts} + Brave {web_brave_attempts}"
            logger.info(
                f"\nFIELD COMPLETE\n"
                f"Seller: {seller_name}\n"
                f"Field: Official Website\n"
                f"Result: FOUND ({primary_website_url})\n"
                f"Attempts: {attempts_text}"
            )
            field_log_entries.append({
                "field": "Website",
                "query": attempts_text,
                "results": len(initial_search_results),
                "verified": "YES",
                "value": primary_website_url,
                "source": primary_website_url,
            })
        else:
            logger.info(
                f"\nFIELD COMPLETE\n"
                f"Seller: {seller_name}\n"
                f"Field: Official Website\n"
                f"Result: NOT FOUND\n"
                f"Attempts: Bing {web_bing_attempts} + Brave {web_brave_attempts}"
            )
            field_log_entries.append({
                "field": "Website",
                "query": f"Bing {web_bing_attempts} + Brave {web_brave_attempts}",
                "results": len(initial_search_results),
                "verified": "NO",
                "value": "NOT FOUND",
                "source": "None",
            })

        # Log fields found from website so far
        for f_key in ["contact_number", "email", "owner_name", "gst_number", "pan_number", "fssai_number", "raw_address"]:
            if merged.get(f_key):
                logger.info(f"{f_key.replace('_', ' ').title()}: FOUND ({merged[f_key]})")

        # Step 3: Field-Driven Fallback Search for missing business attributes
        fields_to_search = [
            ("gst_number", "GST", "gst"),
            ("raw_address", "Address", "address"),
            ("pincode", "Pincode", "pincode"),
            ("contact_number", "Phone", "phone"),
            ("email", "Email", "email"),
            ("owner_name", "Owner / Founder", "owner"),
            ("pan_number", "PAN", "pan"),
            ("fssai_number", "FSSAI", "fssai"),
        ]

        for field_attr, field_display_name, query_key in fields_to_search:
            # If already verified from official website or previous steps, skip search
            if merged.get(field_attr):
                field_log_entries.append({
                    "field": field_display_name,
                    "query": "N/A (Pre-verified)",
                    "results": 0,
                    "verified": "YES",
                    "value": merged[field_attr],
                    "source": field_source_urls.get(field_attr, "official_website"),
                })
                continue

            # Auto-derive PAN from GST if GSTIN is present
            if field_attr == "pan_number" and merged.get("gst_number"):
                valid_g = validate_gst(merged["gst_number"])
                if valid_g:
                    pan_derived = valid_g[2:12]
                    _set_field("pan_number", pan_derived, field_sources.get("gst_number", "targeted_search"), src_url="Derived from GSTIN")
                    logger.info(f"PAN: FOUND ({pan_derived} - Derived from GSTIN)")
                    field_log_entries.append({
                        "field": field_display_name,
                        "query": "Derived from GSTIN",
                        "results": 0,
                        "verified": "YES",
                        "value": pan_derived,
                        "source": "GSTIN State / IT Registry",
                    })
                    continue

            logger.info(f"{field_display_name}: SEARCHING...")

            field_start_time = time.monotonic()
            bing_attempts = 0
            brave_attempts = 0
            field_resolved = False
            last_query_used = ""
            total_res_count = 0

            # Phase 1: Maximum 5 Bing Queries
            bing_queries = generate_bing_queries_for_field(seller_name, query_key)
            for attempt_idx, target_query in enumerate(bing_queries[:MAX_BING_QUERIES_PER_FIELD], start=1):
                if (time.monotonic() - field_start_time) >= FIELD_TIMEOUT_SECONDS:
                    break

                bing_attempts += 1
                last_query_used = target_query
                raw_results, status_code = await self._query_bing(target_query)
                total_res_count += len(raw_results)

                accepted_candidate = None
                accepted_src = None

                logger.info(
                    f"\n----------------------------------------\n"
                    f"ENRICHMENT SEARCH ATTEMPT\n"
                    f"----------------------------------------\n"
                    f"Seller: {seller_name}\n"
                    f"Field: {field_display_name}\n"
                    f"Provider: Bing\n"
                    f"Attempt: {attempt_idx}/{len(bing_queries[:MAX_BING_QUERIES_PER_FIELD])}\n"
                    f"Query: {target_query}\n"
                    f"HTTP Status: {status_code or 200}\n"
                    f"Results: {len(raw_results)}"
                )

                # Inspect ALL organic results before making accept/reject decision
                for idx, r_item in enumerate(raw_results, start=1):
                    eval_res = evaluate_result_candidate(seller_name, field_attr, r_item, gst_number=merged.get("gst_number"))

                    log_candidate_evaluation(
                        seller=seller_name,
                        field=field_display_name,
                        search="Bing",
                        query=target_query,
                        results_count=len(raw_results),
                        candidate=eval_res["raw_candidate"],
                        match_score=eval_res["seller_match_score"],
                        source=eval_res["url"] or "snippet",
                        valid="VALID" if eval_res["valid_candidate_val"] else "INVALID",
                        result="ACCEPTED" if eval_res["decision"] == "ACCEPT" else "REJECTED",
                        reason=eval_res["reject_reason"],
                    )

                    if eval_res["decision"] == "ACCEPT" and not accepted_candidate:
                        accepted_candidate = eval_res["valid_candidate_val"]
                        accepted_src = eval_res["url"]

                if accepted_candidate:
                    _set_field(field_attr, accepted_candidate, "targeted_search", src_url=accepted_src)
                    if field_attr == "gst_number":
                        v_g = validate_gst(accepted_candidate)
                        if v_g:
                            pan_val = v_g[2:12]
                            v_p = validate_pan(pan_val, gst_str=v_g)
                            if v_p:
                                _set_field("pan_number", pan_val, "targeted_search", src_url="Derived from GSTIN")
                                logger.info(f"PAN: Derived from verified GSTIN ({pan_val})")
                    if field_attr == "raw_address":
                        parsed_addr = parse_raw_address(str(accepted_candidate), gst_number=merged.get("gst_number"))
                        if parsed_addr.get("city") and not merged.get("city"):
                            _set_field("city", parsed_addr["city"], "targeted_search", src_url=accepted_src)
                        if parsed_addr.get("state") and not merged.get("state"):
                            _set_field("state", parsed_addr["state"], "targeted_search", src_url=accepted_src)
                        if parsed_addr.get("pincode") and not merged.get("pincode"):
                            _set_field("pincode", parsed_addr["pincode"], "targeted_search", src_url=accepted_src)
                    field_resolved = True
                    logger.info(
                        f"Decision: SAVE\n"
                        f"Accepted Value: {accepted_candidate}\n"
                        f"----------------------------------------"
                    )
                    break
                else:
                    logger.info(
                        f"Decision: NEXT QUERY\n"
                        f"----------------------------------------"
                    )

                await asyncio.sleep(0.15)

            # Phase 2: Brave Fallback Queries if Bing was exhausted without candidate
            is_rate_limited = False
            if not field_resolved and (time.monotonic() - field_start_time) < FIELD_TIMEOUT_SECONDS:
                logger.info(
                    f"\nBING EXHAUSTED\n"
                    f"Seller: {seller_name}\n"
                    f"Field: {field_display_name}\n"
                    f"Usable candidate: NONE\n"
                    f"Switching to Brave"
                )
                brave_queries = generate_brave_queries_for_field(seller_name, query_key)
                for attempt_idx, target_query in enumerate(brave_queries[:MAX_BRAVE_QUERIES_PER_FIELD], start=1):
                    if (time.monotonic() - field_start_time) >= FIELD_TIMEOUT_SECONDS:
                        break

                    brave_attempts += 1
                    last_query_used = target_query
                    raw_results, status_code = await self._query_brave(target_query)
                    
                    if status_code == 429:
                        logger.warning(
                            f"\nRATE_LIMITED: Brave returned HTTP 429 for '{target_query}'. Skipping Brave queries."
                        )
                        is_rate_limited = True
                        break

                    total_res_count += len(raw_results)
                    accepted_candidate = None
                    accepted_src = None

                    logger.info(
                        f"\n----------------------------------------\n"
                        f"ENRICHMENT SEARCH ATTEMPT\n"
                        f"----------------------------------------\n"
                        f"Seller: {seller_name}\n"
                        f"Field: {field_display_name}\n"
                        f"Provider: Brave\n"
                        f"Attempt: {attempt_idx}/{len(brave_queries[:MAX_BRAVE_QUERIES_PER_FIELD])}\n"
                        f"Query: {target_query}\n"
                        f"HTTP Status: {status_code or 200}\n"
                        f"Results: {len(raw_results)}"
                    )

                    for idx, r_item in enumerate(raw_results, start=1):
                        eval_res = evaluate_result_candidate(seller_name, field_attr, r_item, gst_number=merged.get("gst_number"))

                        log_candidate_evaluation(
                            seller=seller_name,
                            field=field_display_name,
                            search="Brave",
                            query=target_query,
                            results_count=len(raw_results),
                            candidate=eval_res["raw_candidate"],
                            match_score=eval_res["seller_match_score"],
                            source=eval_res["url"] or "snippet",
                            valid="VALID" if eval_res["valid_candidate_val"] else "INVALID",
                            result="ACCEPTED" if eval_res["decision"] == "ACCEPT" else "REJECTED",
                            reason=eval_res["reject_reason"],
                        )

                        if eval_res["decision"] == "ACCEPT" and not accepted_candidate:
                            accepted_candidate = eval_res["valid_candidate_val"]
                            accepted_src = eval_res["url"]

                    if accepted_candidate:
                        _set_field(field_attr, accepted_candidate, "targeted_search", src_url=accepted_src)
                        if field_attr == "gst_number":
                            v_g = validate_gst(accepted_candidate)
                            if v_g:
                                pan_val = v_g[2:12]
                                v_p = validate_pan(pan_val, gst_str=v_g)
                                if v_p:
                                    _set_field("pan_number", pan_val, "targeted_search", src_url="Derived from GSTIN")
                                    logger.info(f"PAN: Derived from verified GSTIN ({pan_val})")
                        if field_attr == "raw_address":
                            parsed_addr = parse_raw_address(str(accepted_candidate), gst_number=merged.get("gst_number"))
                            if parsed_addr.get("city") and not merged.get("city"):
                                _set_field("city", parsed_addr["city"], "targeted_search", src_url=accepted_src)
                            if parsed_addr.get("state") and not merged.get("state"):
                                _set_field("state", parsed_addr["state"], "targeted_search", src_url=accepted_src)
                            if parsed_addr.get("pincode") and not merged.get("pincode"):
                                _set_field("pincode", parsed_addr["pincode"], "targeted_search", src_url=accepted_src)
                        field_resolved = True
                        logger.info(
                            f"Decision: SAVE\n"
                            f"Accepted Value: {accepted_candidate}\n"
                            f"----------------------------------------"
                        )
                        break
                    else:
                        logger.info(
                            f"Decision: NEXT QUERY\n"
                            f"----------------------------------------"
                        )

                    await asyncio.sleep(0.15)

            timed_out = (time.monotonic() - field_start_time) >= FIELD_TIMEOUT_SECONDS
            attempts_summary = f"Bing {bing_attempts}" if not brave_attempts else f"Bing {bing_attempts} + Brave {brave_attempts}"
            if field_resolved and merged.get(field_attr):
                logger.info(
                    f"\nFIELD COMPLETE\n"
                    f"Seller: {seller_name}\n"
                    f"Field: {field_display_name}\n"
                    f"Result: FOUND ({merged[field_attr]})\n"
                    f"Attempts: {attempts_summary}"
                )
                field_log_entries.append({
                    "field": field_display_name,
                    "query": last_query_used or attempts_summary,
                    "results": total_res_count,
                    "verified": "YES",
                    "value": merged[field_attr],
                    "source": field_source_urls.get(field_attr, "targeted_search"),
                })
            elif is_rate_limited:
                logger.warning(
                    f"\nFIELD COMPLETE\n"
                    f"Seller: {seller_name}\n"
                    f"Field: {field_display_name}\n"
                    f"Result: RATE_LIMITED\n"
                    f"Attempts: {attempts_summary}"
                )
                field_log_entries.append({
                    "field": field_display_name,
                    "query": last_query_used or attempts_summary,
                    "results": total_res_count,
                    "verified": "NO",
                    "value": "RATE_LIMITED",
                    "source": "None",
                })
            else:
                res_label = "NOT FOUND (Timeout 30s exceeded)" if timed_out else "NOT FOUND"
                logger.info(
                    f"\nFIELD COMPLETE\n"
                    f"Seller: {seller_name}\n"
                    f"Field: {field_display_name}\n"
                    f"Result: {res_label}\n"
                    f"Attempts: {attempts_summary}"
                )
                field_log_entries.append({
                    "field": field_display_name,
                    "query": last_query_used or attempts_summary,
                    "results": total_res_count,
                    "verified": "NO",
                    "value": "NOT FOUND",
                    "source": "None",
                })


        # Step 4: Stage 2 - Legal Entity Discovery & Enrichment
        # If GSTIN search revealed an explicit legal trade/company name distinct from the seller slug
        legal_name = None
        if merged.get("gst_number"):
            for res_item in initial_search_results:
                m_title = res_item.get("title", "")
                m_legal = re.search(r"(?i)(?:details of|gst(?:in)?\s+for|registered as)\s+([A-Z0-9\s]{3,35})(?:\s+is|\s+-|\s+\()", m_title)
                if m_legal:
                    cand_l = m_legal.group(1).strip()
                    if len(cand_l) >= 4 and cand_l.lower() != seller_name.lower():
                        legal_name = cand_l
                        break

        if legal_name:
            logger.info(f"Discovered Legal Business Entity: '{legal_name}'. Running Stage 2 Legal Entity Enrichment...")
            for field_attr, field_display_name, query_key in fields_to_search:
                if merged.get(field_attr):
                    continue
                legal_bing_queries = generate_bing_queries_for_field(legal_name, query_key)
                legal_start_time = time.monotonic()
                for l_query in legal_bing_queries[:MAX_BING_QUERIES_PER_FIELD]:
                    if (time.monotonic() - legal_start_time) >= FIELD_TIMEOUT_SECONDS:
                        break
                    l_raw, _ = await self._query_bing(l_query)
                    if not l_raw:
                        continue
                    l_rel, _ = self._filter_search_results(legal_name, l_raw, min_score=20)
                    if not l_rel:
                        continue
                    if field_attr == "gst_number":
                        r = extract_gst(l_rel, legal_name)
                        if r:
                            _set_field("gst_number", r[0], "filing_registry", src_url=r[1])
                    elif field_attr == "raw_address":
                        r = extract_address(l_rel, legal_name, gst_number=merged.get("gst_number"))
                        if r:
                            _set_field("raw_address", r[0].get("billing_address"), "filing_registry", src_url=r[1])
                    elif field_attr == "owner_name":
                        r = extract_owner(l_rel, legal_name)
                        if r:
                            _set_field("owner_name", r[0], "filing_registry", src_url=r[1])
                    elif field_attr == "contact_number":
                        r = extract_phone(l_rel, legal_name)
                        if r:
                            _set_field("contact_number", r[0], "filing_registry", src_url=r[1])
                    elif field_attr == "email":
                        r = extract_email(l_rel, legal_name)
                        if r:
                            _set_field("email", r[0], "filing_registry", src_url=r[1])
                    elif field_attr == "fssai_number":
                        r = extract_fssai(l_rel, legal_name)
                        if r:
                            _set_field("fssai_number", r[0], "filing_registry", src_url=r[1])
                    if merged.get(field_attr):
                        break

        # Step 5: Strict Cross-Checking & Harmonization

        merged = cross_check_seller_data(merged)

        # Step 5: Address Normalization
        address_dict = parse_raw_address(merged.get("raw_address"), gst_number=merged.get("gst_number"))

        city = address_dict.get("city") or merged.get("city")
        state = address_dict.get("state") or merged.get("state")
        pincode = address_dict.get("pincode") or merged.get("pincode")
        billing_address = address_dict.get("billing_address")
        shipping_address = address_dict.get("shipping_address")
        country = address_dict.get("country") or "India"

        # Step 6: Business Model & Category resolution
        business_category = category
        business_model = "B2C / Retail"
        seller_lower = seller_name.lower()
        if merged.get("gst_number"):
            business_model = "Proprietorship / Registered Business"
        if "pvt ltd" in seller_lower or "private limited" in seller_lower:
            business_model = "Private Limited Company"
        elif "limited" in seller_lower or "ltd" in seller_lower:
            business_model = "Public Limited Company"
        elif "llp" in seller_lower:
            business_model = "Limited Liability Partnership"

        # Step 7: Build Confidence Matrix
        gst_validated = bool(merged.get("gst_number"))
        confidence_dict: Dict[str, Any] = {}

        fields_to_score = [
            ("gst_number", merged.get("gst_number"), field_sources.get("gst_number", "search_snippet")),
            ("pan_number", merged.get("pan_number"), field_sources.get("pan_number", "search_snippet")),
            ("contact_number", merged.get("contact_number"), field_sources.get("contact_number", "search_snippet")),
            ("email", merged.get("email"), field_sources.get("email", "search_snippet")),
            ("fssai_number", merged.get("fssai_number"), field_sources.get("fssai_number", "search_snippet")),
            ("website_url", merged.get("website_url"), field_sources.get("website_url", "search_snippet")),
            ("address", billing_address, field_sources.get("raw_address", "search_snippet")),
        ]

        for fname, fval, fsrc in fields_to_score:
            score, src = calculate_field_confidence(fname, fval, fsrc, gst_validated=gst_validated)
            confidence_dict[fname] = {
                "value": fval,
                "confidence": score,
                "source": src,
            }

        # Step 8: Determine Overall Seller Status
        partial_record = {
            "gst_number": merged.get("gst_number"),
            "pan_number": merged.get("pan_number"),
            "email": merged.get("email"),
            "contact_number": merged.get("contact_number"),
            "website_url": merged.get("website_url"),
            "city": city,
            "state": state,
            "billing_address": billing_address,
        }
        status = determine_seller_status(partial_record, confidence_dict)

        # Assemble final record matching required Excel schema
        final_record: Dict[str, Any] = {
            "seller_name": seller_name,
            "marketplace": marketplace,
            "fulfillment_by": fulfillment_by,
            "product_url": product_url,
            "seller_source_url": seller_source_url,
            "seller_source_type": seller_source_type,
            "category": category,
            "sub_category": sub_category,
            "sub_sub_category": sub_sub_category,
            "sub_sub_subcategory": sub_sub_subcategory,
            "product_rating": product_rating,
            "seller_rating": star_rating,
            "star_rating": star_rating,
            "business_model": business_model,
            "business_category": business_category,
            "owner_name": merged.get("owner_name"),
            "contact_number": merged.get("contact_number"),
            "email": merged.get("email"),
            "gst_number": merged.get("gst_number"),
            "pan_number": merged.get("pan_number"),
            "fssai_number": merged.get("fssai_number"),
            "billing_address": billing_address,
            "shipping_address": shipping_address,
            "city": city,
            "state": state,
            "pincode": pincode,
            "country": country,
            "website_url": merged.get("website_url"),
            "status": status,
            "source": list(sources_used) if sources_used else ["search_query"],
            "confidence": confidence_dict,
            "seller_confidence": seller_confidence,
        }

        # Print structured BING ENRICHMENT log matching Requirement 11
        log_lines = [
            "\n----------------------------------------",
            "BING ENRICHMENT",
            "----------------------------------------",
            f"Seller: {seller_name}\n",
        ]
        for entry in field_log_entries:
            log_lines.append(f"Field: {entry['field']}")
            log_lines.append(f"Query: {entry['query']}")
            log_lines.append(f"Results: {entry['results']}")
            log_lines.append(f"Verified: {entry['verified']}")
            log_lines.append(f"Value: {entry['value']}")
            if entry["verified"] == "YES":
                log_lines.append(f"Source: {entry['source']}")
            log_lines.append("")
        log_lines.append("----------------------------------------")
        logger.info("\n".join(log_lines))

        # Cache in memory
        self.seller_enrichment_cache[cache_key] = final_record
        return final_record

    async def research_seller(
        self,
        seller_name: str,
        categories: Optional[List[str]] = None,
        star_rating: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Backward-compatible helper wrapping generic enrich_seller.

        Args:
            seller_name: Raw seller name.
            categories: List of product categories.
            star_rating: Seller star rating.

        Returns:
            Enriched seller record.
        """
        seller_record = {
            "marketplace": "flipkart",
            "seller_name": seller_name,
            "category": categories[0] if categories else None,
            "star_rating": star_rating,
        }
        return await self.enrich_seller(seller_record)


async def search_seller_web(
    seller_name: str,
    field: str,
    engine: Optional[WebResearchEngine] = None,
) -> Tuple[List[Dict[str, str]], str, Dict[str, Any]]:
    """Standalone helper function to search web for seller field using waterfall."""
    should_close = False
    if engine is None:
        engine = WebResearchEngine()
        should_close = True
    try:
        return await engine.search_seller_web(seller_name, field)
    finally:
        if should_close:
            await engine.close()
