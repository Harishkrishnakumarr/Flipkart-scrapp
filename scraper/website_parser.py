"""Company website crawler and parser for extracting business identity and contact details."""

import json
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

# Relevant same-domain pages to inspect for business identity/legal/contact data.
# Keep this bounded so one seller cannot cause an unbounded crawl.
PRIORITY_SUBPATHS = [
    "",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/company",
    "/company-profile",
    "/support",
    "/customer-care",
    "/legal",
    "/legal-notice",
    "/compliance",
    "/registration",
    "/gst",
    "/tax",
    "/fssai",
    "/license",
    "/licence",
    "/proprietor",
    "/owner",
    "/terms-and-conditions",
    "/terms",
    "/privacy-policy",
]
MAX_PRIORITY_PAGES = 15


class WebsiteParser:
    """Crawls company websites and extracts business and contact information."""

    def __init__(self, timeout: int = HTTP_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=self.timeout,
            follow_redirects=True,
            verify=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_html(self, url: str) -> Optional[str]:
        """Safely fetch HTML content of a webpage."""
        try:
            response = await self.client.get(url)
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code == 200 and (not content_type or "html" in content_type):
                return response.text
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
        return None

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        return urllib.parse.urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc,
                parsed.path or "/",
                "",
                "",
                "",
            )
        ).rstrip("/") or "/"

    def find_internal_priority_links(
        self, base_url: str, soup: BeautifulSoup
    ) -> List[str]:
        """Find useful same-domain contact/legal/business pages.

        The old implementation returned only four links. That caused fields that
        live on different legal/contact pages to be missed. We now rank matching
        links and return a bounded set of up to MAX_PRIORITY_PAGES pages.
        """
        parsed_base = urllib.parse.urlparse(base_url)
        base_host = parsed_base.netloc.lower().split(":", 1)[0].removeprefix("www.")
        candidates: Dict[str, int] = {}

        keywords = {
            "contact": 100,
            "contact-us": 100,
            "reach-us": 95,
            "about": 90,
            "company": 88,
            "profile": 85,
            "owner": 82,
            "proprietor": 82,
            "gst": 80,
            "tax": 78,
            "fssai": 78,
            "license": 76,
            "licence": 76,
            "registration": 74,
            "compliance": 72,
            "legal": 70,
            "terms": 65,
            "privacy": 60,
            "support": 55,
            "customer-care": 55,
        }

        for a_tag in soup.find_all("a", href=True):
            href = str(a_tag["href"]).strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            full_url = urllib.parse.urljoin(base_url, href)
            parsed_full = urllib.parse.urlparse(full_url)
            host = parsed_full.netloc.lower().split(":", 1)[0].removeprefix("www.")
            if not parsed_full.scheme.startswith("http") or host != base_host:
                continue

            path = (parsed_full.path or "/").lower()
            text = a_tag.get_text(separator=" ", strip=True).lower()
            haystack = f"{path} {text}"
            score = 0
            for keyword, weight in keywords.items():
                if keyword in haystack:
                    score = max(score, weight)
            if score <= 0:
                continue

            clean = self._canonical_url(full_url)
            candidates[clean] = max(score, candidates.get(clean, 0))

        ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
        return [url for url, _ in ranked[:MAX_PRIORITY_PAGES]]

    def _build_fallback_priority_urls(self, base_url: str) -> List[str]:
        """Build common legal/contact paths when a site does not expose link text."""
        root = base_url.rstrip("/")
        return [self._canonical_url(urllib.parse.urljoin(root + "/", path.lstrip("/"))) for path in PRIORITY_SUBPATHS if path]

    def extract_from_html(
        self, html_content: str, source_url: str
    ) -> Dict[str, Any]:
        """Extract structured business credentials, contacts, and addresses from HTML."""
        soup = BeautifulSoup(html_content, "lxml")

        email_candidates: List[str] = []
        jsonld_gst = None
        jsonld_phone = None
        jsonld_owner = None
        jsonld_company = None
        jsonld_address = None

        def inspect_jsonld_item(item: Dict[str, Any]) -> None:
            nonlocal jsonld_gst, jsonld_phone, jsonld_owner, jsonld_company, jsonld_address
            if item.get("name") and not jsonld_company:
                jsonld_company = str(item["name"]).strip()
            if item.get("email"):
                v = validate_email(str(item["email"]))
                if v and v not in email_candidates:
                    email_candidates.append(v)
            for phone_key in ("telephone", "phone"):
                if item.get(phone_key) and not jsonld_phone:
                    v = validate_phone(str(item[phone_key]))
                    if v:
                        jsonld_phone = v
                        break
            for gst_k in ("taxID", "vatID", "identifier", "gst", "gstin"):
                if item.get(gst_k) and not jsonld_gst:
                    v_g = validate_gst(str(item[gst_k]))
                    if v_g:
                        jsonld_gst = v_g
                        break
            address = item.get("address")
            if isinstance(address, dict):
                parts = [address.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")]
                candidate = ", ".join(str(p).strip() for p in parts if p)
                if candidate and not jsonld_address:
                    jsonld_address = candidate
            elif address and not jsonld_address:
                jsonld_address = str(address).strip()
            for person_key in ("founder", "founders", "employee", "member"):
                person = item.get(person_key)
                people = person if isinstance(person, list) else [person]
                for p in people:
                    if isinstance(p, dict) and p.get("name") and not jsonld_owner:
                        jsonld_owner = str(p["name"]).strip()
                    elif isinstance(p, str) and p.strip() and not jsonld_owner:
                        jsonld_owner = p.strip()

            contact_points = item.get("contactPoint") or item.get("contactPoints")
            cps = contact_points if isinstance(contact_points, list) else ([contact_points] if isinstance(contact_points, dict) else [])
            for cp in cps:
                if not isinstance(cp, dict):
                    continue
                if cp.get("email"):
                    v = validate_email(str(cp["email"]))
                    if v and v not in email_candidates:
                        email_candidates.append(v)
                if cp.get("telephone") and not jsonld_phone:
                    v = validate_phone(str(cp["telephone"]))
                    if v:
                        jsonld_phone = v

        for script in soup.find_all("script", type="application/ld+json"):
            script_text = script.get_text()
            if not script_text:
                continue
            try:
                data = json.loads(script_text)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict):
                        inspect_jsonld_item(item)
                        graph = item.get("@graph")
                        if isinstance(graph, list):
                            for graph_item in graph:
                                if isinstance(graph_item, dict):
                                    inspect_jsonld_item(graph_item)
            except Exception:
                continue

        for s in soup(["script", "style", "noscript", "svg"]):
            s.decompose()
        plain_text = soup.get_text(separator=" ", strip=True)

        extracted: Dict[str, Any] = {
            "company_name": jsonld_company,
            "owner_name": jsonld_owner,
            "contact_number": jsonld_phone,
            "email": None,
            "gst_number": jsonld_gst,
            "pan_number": None,
            "fssai_number": None,
            "address": jsonld_address,
            "website_url": source_url,
        }

        if not extracted["gst_number"]:
            for match in GST_REGEX.findall(plain_text):
                valid = validate_gst(match)
                if valid:
                    extracted["gst_number"] = valid
                    break

        if extracted["gst_number"]:
            extracted["pan_number"] = validate_pan(None, extracted["gst_number"])
        else:
            for match in PAN_REGEX.findall(plain_text):
                valid = validate_pan(match)
                if valid:
                    extracted["pan_number"] = valid
                    break

        fssai_matches = re.findall(r"(?:FSSAI|Lic(?:ense)?\s*(?:No\.?)?|Food\s+License)[:\s#-]*([1-2][0-9]{13})", plain_text, re.I)
        fssai_matches += FSSAI_REGEX.findall(plain_text)
        for match in fssai_matches:
            valid = validate_fssai(match)
            if valid:
                extracted["fssai_number"] = valid
                break

        for mailto in soup.select("a[href^='mailto:']"):
            candidate = str(mailto.get("href", "")).replace("mailto:", "").split("?")[0].strip()
            valid = validate_email(candidate)
            if valid and valid not in email_candidates:
                email_candidates.append(valid)
        for em in EMAIL_REGEX.findall(plain_text):
            valid = validate_email(em)
            if valid and valid not in email_candidates:
                email_candidates.append(valid)
        if email_candidates:
            def _email_rank(e: str) -> int:
                user = e.split("@")[0].lower()
                if user in ("sales", "contact", "info", "support", "care", "help", "order", "orders"):
                    return 3
                if user in ("admin", "office", "business", "service"):
                    return 2
                return 1
            extracted["email"] = sorted(email_candidates, key=_email_rank, reverse=True)[0]

        if not extracted["contact_number"]:
            for tel in soup.select("a[href^='tel:']"):
                candidate = str(tel.get("href", "")).replace("tel:", "").split("?")[0].strip()
                valid = validate_phone(candidate)
                if valid:
                    extracted["contact_number"] = valid
                    break
        if not extracted["contact_number"]:
            for ph in PHONE_REGEX.findall(plain_text):
                valid = validate_phone(ph)
                if valid:
                    extracted["contact_number"] = valid
                    break

        if not extracted["owner_name"]:
            owner_patterns = [
                r"(?:Proprietor(?:\s+Name)?|Director|Founder|Owner|Managing\s+Director|Key\s+Person|Partner)\s*[:\-]\s*([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})",
                r"(?:Mr\.|Mrs\.|Ms\.|Dr\.)\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\s*\((?:Proprietor|Director|Owner|Founder)\)",
            ]
            for pat in owner_patterns:
                owner_m = re.search(pat, plain_text)
                if owner_m:
                    cand = re.sub(r"\s+", " ", owner_m.group(1)).strip()
                    if len(cand) > 3 and not any(w in cand.lower() for w in ["contact", "about", "company", "service", "policy", "terms"]):
                        extracted["owner_name"] = cand
                        break

        if not extracted["address"]:
            address_patterns = [
                r"(?:Registered\s+Office|Corporate\s+Office|Billing\s+Address|Office\s+Address|Head\s+Office|Address)\s*[:\-]\s*(.{15,220}?\b[1-9][0-9]{5}\b)",
                r"((?:Plot\s+No|Shop\s+No|Flat\s+No|Building|Tower|Sector|Road|Street|Nagar|Marg)[^\n\r]{10,180}\b[1-9][0-9]{5}\b)",
            ]
            for pat in address_patterns:
                addr_m = re.search(pat, plain_text, re.IGNORECASE)
                if addr_m:
                    addr_cand = re.sub(r"\s+", " ", addr_m.group(1)).strip(" ,;:-")
                    if len(addr_cand) >= 15:
                        extracted["address"] = addr_cand
                        break

        if not extracted["address"]:
            for el in soup.select("footer, div[class*='footer'], div[class*='contact'], div[id*='contact'], div[class*='address'], div[id*='address'], address"):
                el_text = el.get_text(separator=" ", strip=True)
                pincode_m = PINCODE_REGEX.search(el_text)
                if pincode_m:
                    idx = pincode_m.start()
                    snippet = re.sub(r"\s+", " ", el_text[max(0, idx - 180):idx + 20]).strip()
                    if len(snippet) >= 20:
                        extracted["address"] = snippet
                        break

        return extracted

    async def inspect_website(self, base_url: str) -> Dict[str, Any]:
        """Crawl a bounded set of relevant same-domain pages and merge all fields."""
        logger.info(f"[WEBSITE CRAWL] start url={base_url}")
        parsed = urllib.parse.urlparse(base_url)
        if not parsed.scheme:
            base_url = f"https://{base_url}"
        base_url = self._canonical_url(base_url)

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

        home_html = await self.fetch_html(base_url)
        if not home_html:
            logger.info(f"[WEBSITE CRAWL] homepage unavailable url={base_url}")
            return aggregated_data

        home_soup = BeautifulSoup(home_html, "lxml")
        self._merge_data(aggregated_data, self.extract_from_html(home_html, base_url))

        # Crawl discovered links first, then common fallback paths not linked in nav/footer.
        discovered = self.find_internal_priority_links(base_url, home_soup)
        fallback = self._build_fallback_priority_urls(base_url)
        crawl_urls: List[str] = []
        seen: Set[str] = {base_url.rstrip("/")}
        for url in discovered + fallback:
            canonical = self._canonical_url(url)
            if canonical.rstrip("/") not in seen and canonical not in crawl_urls:
                crawl_urls.append(canonical)

        logger.info(f"[WEBSITE CRAWL] candidate_pages={min(len(crawl_urls), MAX_PRIORITY_PAGES)}")

        for sub_url in crawl_urls[:MAX_PRIORITY_PAGES]:
            sub_html = await self.fetch_html(sub_url)
            if not sub_html:
                continue
            sub_data = self.extract_from_html(sub_html, sub_url)
            before = {k: v for k, v in aggregated_data.items() if v}
            self._merge_data(aggregated_data, sub_data)
            newly_found = [k for k, v in aggregated_data.items() if v and not before.get(k)]
            if newly_found:
                logger.info(f"[WEBSITE FIELD EXTRACTION] url={sub_url} fields={','.join(newly_found)}")

            # Do not stop merely because GST/email/phone/address exist. PAN/FSSAI/owner
            # often live on separate legal pages. Stop only when all target fields are filled.
            targets = ("owner_name", "contact_number", "email", "gst_number", "pan_number", "fssai_number", "address")
            if all(aggregated_data.get(k) for k in targets):
                break

        logger.info(
            "[WEBSITE CRAWL] complete "
            f"fields={','.join(k for k, v in aggregated_data.items() if v)}"
        )
        return aggregated_data

    def _merge_data(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """Merge non-empty fields without allowing blank values to erase discoveries."""
        for key, val in source.items():
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            if not target.get(key):
                target[key] = val
