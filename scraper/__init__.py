"""Flipkart Seller Scraper package.

The package applies a small compatibility patch to the native product parser so
Flipkart's label-only "Name and address of the Importer" node is never stored
as an address.  The patch also extracts the adjacent importer value from the
common table / sibling DOM layouts used by Flipkart product pages.
"""

__version__ = "1.0.1"


def _install_address_extraction_patch() -> None:
    """Patch native product parsing without changing the public parser API.

    Older Flipkart pages expose the importer label as a standalone text node.
    The native parser used to treat that label as the address.  This wrapper
    keeps the existing extraction strategies and only repairs invalid address
    values after extraction, with a direct DOM lookup as the primary recovery.
    """
    try:
        from bs4 import BeautifulSoup
        from . import product_parser as _product_parser
    except Exception:
        return

    if getattr(_product_parser, "_IMPORTER_ADDRESS_PATCHED", False):
        return

    original_extract = _product_parser.extract_seller_and_ratings
    original_parse = _product_parser.parse_product_page

    IMPORTER_LABELS = {
        "name and address of the importer",
        "name & address of the importer",
    }
    MARKETPLACE_LOCATION_MARKERS = (
        "flipkart internet private limited",
        "buildings alyssa",
        "begonia",
        "flipkart internet pvt ltd",
    )

    def clean(value):
        if value is None:
            return ""
        return " ".join(str(value).replace("\xa0", " ").split()).strip(" :,-")

    def is_importer_label(value):
        normalized = clean(value).lower()
        return normalized in IMPORTER_LABELS

    def looks_like_address(value):
        value = clean(value)
        if not value or is_importer_label(value):
            return False
        if len(value) < 12 or len(value) > 500:
            return False
        # Reject common marketplace-only location placeholders.
        low = value.lower()
        if any(marker in low for marker in MARKETPLACE_LOCATION_MARKERS):
            return False
        # An address normally has either a postal code or a location/address
        # token.  This deliberately accepts Indian addresses without a pin.
        return bool(
            __import__("re").search(
                r"(?:\b[1-9][0-9]{5}\b|\broad\b|\bstreet\b|\bnagar\b|\bplot\b|"
                r"\bbuilding\b|\bfloor\b|\bsector\b|\bestate\b|\bindustrial\b|"
                r"\bmarket\b|\blane\b|\bavenue\b|\bindia\b|,)",
                value,
                __import__("re").IGNORECASE,
            )
        )

    def find_importer_value(soup):
        """Find the value adjacent to the importer label in common DOM shapes."""
        if not soup:
            return None

        # 1. Table layout: <tr><td>label</td><td>value</td></tr>.
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            for idx, cell in enumerate(cells):
                if is_importer_label(cell.get_text(" ", strip=True)):
                    for candidate_cell in cells[idx + 1:]:
                        candidate = clean(candidate_cell.get_text(" ", strip=True))
                        if looks_like_address(candidate):
                            return candidate

        # 2. Label followed by sibling element(s).
        for element in soup.find_all(["div", "span", "p", "li", "td", "th", "dt"]):
            if not is_importer_label(element.get_text(" ", strip=True)):
                continue

            sibling = element.find_next_sibling()
            for _ in range(4):
                if not sibling:
                    break
                candidate = clean(sibling.get_text(" ", strip=True))
                if looks_like_address(candidate):
                    return candidate
                sibling = sibling.find_next_sibling()

            # 3. Definition-list layout: <dt>label</dt><dd>value</dd>.
            parent = element.parent
            if parent:
                children = [c for c in parent.find_all(recursive=False) if getattr(c, "name", None)]
                try:
                    idx = children.index(element)
                except ValueError:
                    idx = -1
                if idx >= 0:
                    for candidate_el in children[idx + 1:idx + 4]:
                        candidate = clean(candidate_el.get_text(" ", strip=True))
                        if looks_like_address(candidate):
                            return candidate

            # 4. Nearby parent container. Avoid returning the label itself.
            parent = element.parent
            if parent:
                texts = [clean(c.get_text(" ", strip=True)) for c in parent.find_all(recursive=False)]
                for candidate in texts:
                    if candidate and not is_importer_label(candidate) and looks_like_address(candidate):
                        return candidate

        return None

    def repair_details(details, soup):
        details = dict(details or {})
        current = clean(details.get("billing_address"))

        if is_importer_label(current) or not looks_like_address(current):
            recovered = find_importer_value(soup)
            if recovered:
                details["billing_address"] = recovered
                parsed = _product_parser.parse_address_fields(
                    recovered, gst_number=details.get("gst_number")
                )
                for key in ("city", "state", "pincode"):
                    if parsed.get(key):
                        details[key] = parsed[key]
            else:
                details["billing_address"] = None

        # The Flipkart corporate warehouse/location is not the seller's address.
        location = clean(details.get("seller_location"))
        if location and any(marker in location.lower() for marker in MARKETPLACE_LOCATION_MARKERS):
            details["seller_location"] = None
            # Do not leave marketplace-only city data behind.
            if not details.get("billing_address"):
                details["city"] = None
                details["state"] = None
                details["pincode"] = None

        return details

    def patched_extract(soup, page_html=None):
        details = original_extract(soup, page_html)
        return repair_details(details, soup if not isinstance(soup, str) else BeautifulSoup(soup, "lxml"))

    def patched_parse(html_content, page_url="", http_status=200):
        result = original_parse(html_content, page_url=page_url, http_status=http_status)
        soup = BeautifulSoup(html_content or "", "lxml")
        result = repair_details(result, soup)

        # Keep all address aliases synchronized after recovery / rejection.
        result["raw_address"] = result.get("billing_address")
        result["seller_location"] = result.get("billing_address") or result.get("seller_location")
        result["shipping_address"] = result.get("billing_address")
        return result

    _product_parser.extract_seller_and_ratings = patched_extract
    _product_parser.parse_product_page = patched_parse
    _product_parser._IMPORTER_ADDRESS_PATCHED = True


_install_address_extraction_patch()
