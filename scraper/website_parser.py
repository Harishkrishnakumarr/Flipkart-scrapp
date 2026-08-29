"""Company website crawler and parser for extracting business identity and contact details."""

import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set
from bs4 import BeautifulSoup
import httpx

from scraper.config import DEFAULT_HEADERS, HTTP_TIMEOUT_SECONDS
from scraper.validator import (
    EMAIL_REGEX,
    FSSAI_REGEX,
    GST_REGEX,
    PAN_REGEX,
    PHONE_REGEX,
    PINCODE_REGEX,
    validate_email,
    validate_fssai,
    validate_gst,
    validate_pan,
    validate_phone,
    validate_pincode,
)

logger = logging.getLogger("FlipkartScraper.WebsiteParser")

# High-priority relative sub-paths to inspect on candidate websites
PRIORITY_SUBPATHS = [
    "",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/terms-and-conditions",
    "/terms",
    "/privacy-policy",
    "/legal",
]


class WebsiteParser:
    """Crawls company websites and extracts business and contact information."""

    def __init__(self, timeout: int = HTTP_TIMEOUT_SECONDS) -> None:
        """Initialize the website parser.

        Args:
            timeout: HTTP request timeout in seconds.
        """
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=self.timeout,
            follow_redirects=True,
            verify=False,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()

    async def fetch_html(self, url: str) -> Optional[str]:
        """Safely fetch HTML content of a webpage.

        Args:
            url: Absolute URL to fetch.

        Returns:
            HTML string or None if request fails.
        """
        try:
            response = await self.client.get(url)
            if response.status_code == 200:
                return response.text
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
        return None

    def find_internal_priority_links(
        self, base_url: str, soup: BeautifulSoup
    ) -> List[str]:
        """Find internal links matching Contact, About, Terms, or Privacy.

        Args:
            base_url: Website base URL.
            soup: Parsed BeautifulSoup object.

        Returns:
            List of priority internal URLs to crawl.
        """
        parsed_base = urllib.parse.urlparse(base_url)
        found_links: Set[str] = set()

        keywords = [
            "contact",
            "about",
            "terms",
            "privacy",
            "legal",
            "reach-us",
            "support",
            "profile",
        ]

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            text = a_tag.get_text(separator=" ", strip=True).lower()
            href_lower = href.lower()

            if any(k in href_lower or k in text for k in keywords):
                full_url = urllib.parse.urljoin(base_url, href)
                parsed_full = urllib.parse.urlparse(full_url)
                # Ensure same host
                if parsed_full.netloc == parsed_base.netloc:
                    # Strip fragment / query
                    clean = urllib.parse.urlunparse(
                        (parsed_full.scheme, parsed_full.netloc, parsed_full.path, "", "", "")
                    )
                    found_links.add(clean)

        return list(found_links)[:4]

    def extract_from_html(
        self, html_content: str, source_url: str
    ) -> Dict[str, Any]:
        """Extract structured business credentials, contacts, and addresses from HTML.

        Args:
            html_content: Raw webpage HTML.
            source_url: URL being parsed.

        Returns:
            Extracted candidates dictionary.
        """
        soup = BeautifulSoup(html_content, "lxml")

        # Strip script and style tags for plain text scanning
        for s in soup(["script", "style", "noscript", "svg"]):
            s.decompose()

        plain_text = soup.get_text(separator=" ", strip=True)

        extracted: Dict[str, Any] = {
            "company_name": None,
            "owner_name": None,
            "contact_number": None,
            "email": None,
            "gst_number": None,
            "pan_number": None,
            "fssai_number": None,
            "address": None,
            "website_url": source_url,
        }

        # 1. GST Extraction
        gst_matches = GST_REGEX.findall(plain_text)
        for match in gst_matches:
            valid = validate_gst(match)
            if valid:
                extracted["gst_number"] = valid
                break

        # 2. PAN Extraction (or derived from GST)
        if extracted["gst_number"]:
            extracted["pan_number"] = validate_pan(None, extracted["gst_number"])
        else:
            pan_matches = PAN_REGEX.findall(plain_text)
            for match in pan_matches:
                valid = validate_pan(match)
                if valid:
                    extracted["pan_number"] = valid
                    break

        # 3. FSSAI Extraction
        fssai_match = re.search(r"(?:FSSAI|Lic(?:ense)?\s*(?:No\.?)?)[:\s]*([1-2][0-9]{13})", plain_text, re.I)
        if fssai_match:
            valid_fssai = validate_fssai(fssai_match.group(1))
            if valid_fssai:
                extracted["fssai_number"] = valid_fssai
        else:
            fssai_matches = FSSAI_REGEX.findall(plain_text)
            for m in fssai_matches:
                valid_fssai = validate_fssai(m)
                if valid_fssai:
                    extracted["fssai_number"] = valid_fssai
                    break

        # 4. Email Extraction (check mailto links first, then regex)
        for mailto in soup.select("a[href^='mailto:']"):
            href = mailto.get("href", "")
            candidate = href.replace("mailto:", "").split("?")[0].strip()
            valid = validate_email(candidate)
            if valid:
                extracted["email"] = valid
                break

        if not extracted["email"]:
            email_matches = EMAIL_REGEX.findall(plain_text)
            for em in email_matches:
                valid = validate_email(em)
                if valid:
                    extracted["email"] = valid
                    break

        # 5. Phone Extraction (check tel: links first, then regex)
        for tel in soup.select("a[href^='tel:']"):
            href = tel.get("href", "")
            candidate = href.replace("tel:", "").split("?")[0].strip()
            valid = validate_phone(candidate)
            if valid:
                extracted["contact_number"] = valid
                break

        if not extracted["contact_number"]:
            phone_matches = PHONE_REGEX.findall(plain_text)
            for ph in phone_matches:
                valid = validate_phone(ph)
                if valid:
                    extracted["contact_number"] = valid
                    break

        # 6. Owner / Director / Proprietor Extraction
        owner_patterns = [
            r"(?:Proprietor|Director|Founder|Owner|Managing\s+Director|Key\s+Person)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
            r"(?:Mr\.|Mrs\.|Ms\.|Dr\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*(?:\((?:Proprietor|Director|Owner)\))",
        ]
        for pat in owner_patterns:
            owner_m = re.search(pat, plain_text)
            if owner_m:
                cand = owner_m.group(1).strip()
                if len(cand) > 3 and not any(w in cand.lower() for w in ["contact", "about", "company", "service", "policy"]):
                    extracted["owner_name"] = cand
                    break

        # 7. Address Block Extraction (Search near address / office labels or pincode)
        address_patterns = [
            r"(?:Registered\s+Office|Corporate\s+Office|Office\s+Address|Head\s+Office|Address)[:\s]+([^<\n\r]{15,180}\b[1-9][0-9]{5}\b)",
            r"(?:Plot\s+No|Shop\s+No|Flat\s+No|Building|Tower|Sector|Road|Street|Nagar|Marg)[^<\n\r]{10,160}\b[1-9][0-9]{5}\b",
        ]
        for pat in address_patterns:
            addr_m = re.search(pat, plain_text, re.IGNORECASE)
            if addr_m:
                addr_cand = addr_m.group(0).strip()
                # Clean up excess whitespace
                addr_cand = re.sub(r"\s+", " ", addr_cand)
                if len(addr_cand) >= 15:
                    extracted["address"] = addr_cand
                    break

        # Check footer or contact elements directly if address not found
        if not extracted["address"]:
            for el in soup.select("footer, div[class*='footer'], div[class*='contact'], div[id*='contact'], address"):
                el_text = el.get_text(separator=" ", strip=True)
                pincode_m = PINCODE_REGEX.search(el_text)
                if pincode_m:
                    # Grab a slice around the pincode
                    idx = pincode_m.start()
                    snippet = el_text[max(0, idx - 120) : min(len(el_text), idx + 20)]
                    snippet = re.sub(r"\s+", " ", snippet).strip()
                    if len(snippet) > 20:
                        extracted["address"] = snippet
                        break

        return extracted

    async def inspect_website(self, base_url: str) -> Dict[str, Any]:
        """Crawl homepage and key sub-pages of a company website to extract all data.

        Args:
            base_url: Root or candidate URL of the company.

        Returns:
            Merged extracted business details dictionary.
        """
        logger.info(f"Inspecting company website: {base_url}")
        parsed = urllib.parse.urlparse(base_url)
        if not parsed.scheme:
            base_url = f"https://{base_url}"

        aggregated_data: Dict[str, Any] = {
            "company_name": None,
            "owner_name": None,
            "contact_number": None,
            "email": None,
            "gst_number": None,
            "pan_number": None,
            "fssai_number": None,
            "address": None,
            "website_url": base_url,
        }

        # Step 1: Fetch homepage
        home_html = await self.fetch_html(base_url)
        if not home_html:
            return aggregated_data

        home_soup = BeautifulSoup(home_html, "lxml")
        home_data = self.extract_from_html(home_html, base_url)
        self._merge_data(aggregated_data, home_data)

        # Step 2: Discover and crawl key sub-pages (Contact, About, Terms)
        priority_urls = self.find_internal_priority_links(base_url, home_soup)
        for sub_url in priority_urls:
            # If we already have full details, stop early
            if (
                aggregated_data["gst_number"]
                and aggregated_data["email"]
                and aggregated_data["contact_number"]
                and aggregated_data["address"]
            ):
                break

            sub_html = await self.fetch_html(sub_url)
            if sub_html:
                sub_data = self.extract_from_html(sub_html, sub_url)
                self._merge_data(aggregated_data, sub_data)

        return aggregated_data

    def _merge_data(
        self, target: Dict[str, Any], source: Dict[str, Any]
    ) -> None:
        """Merge newly discovered non-null fields into the target data record.

        Args:
            target: Destination dictionary.
            source: Source dictionary with newly extracted values.
        """
        for key, val in source.items():
            if val is not None and not target.get(key):
                target[key] = val
