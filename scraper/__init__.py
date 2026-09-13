"""Flipkart Seller Scraper package.

Applies a compatibility layer around the existing parser so Flipkart's
label-only importer field is never treated as an address.  It also invalidates
legacy cached records containing that label so a subsequent scraper run can
re-enrich them with the corrected extraction logic.
"""

__version__ = "1.0.2"


def _install_address_extraction_patch() -> None:
    """Patch native product parsing and legacy seller-cache handling."""
    try:
        import re
        from bs4 import BeautifulSoup
        from . import product_parser as _product_parser
        from . import validator as _validator
        from . import seller_extractor as _seller_extractor
    except Exception:
        return

    if getattr(_product_parser, "_IMPORTER_ADDRESS_PATCHED", False):
        return

    original_extract = _product_parser.extract_seller_and_ratings
    original_parse = _product_parser.parse_product_page
    original_pending = _seller_extractor.SellerRepository.get_all_pending_sellers
    original_mark_enriched = _seller_extractor.SellerRepository.mark_enriched

    # Make the broken marketplace label invalid everywhere address candidates
    # are validated, including web-research extraction.
    if "name and address of the importer" not in _validator.ADDRESS_BLACKLIST_PHRASES:
        _validator.ADDRESS_BLACKLIST_PHRASES.append("name and address of the importer")
    if "name & address of the importer" not in _validator.ADDRESS_BLACKLIST_PHRASES:
        _validator.ADDRESS_BLACKLIST_PHRASES.append("name & address of the importer")

    IMPORTER_LABELS = {
        "name and address of the importer",
        "name & address of the importer",
    }
    MARKETPLACE_LOCATION_MARKERS = (
        "flipkart internet private limited",
        "flipkart internet pvt ltd",
        "buildings alyssa",
        "begonia",
    )

    def clean(value):
        if value is None:
            return ""
        return " ".join(str(value).replace("\xa0", " ").split()).strip(" :,-")

    def is_importer_label(value):
        return clean(value).lower() in IMPORTER_LABELS

    def looks_like_address(value):
        value = clean(value)
        if not value or is_importer_label(value):
            return False
        if len(value) < 12 or len(value) > 500:
            return False
        low = value.lower()
        if any(marker in low for marker in MARKETPLACE_LOCATION_MARKERS):
            return False
        return bool(
            re.search(
                r"(?:\b[1-9][0-9]{5}\b|\broad\b|\bstreet\b|\bnagar\b|\bplot\b|"
                r"\bbuilding\b|\bfloor\b|\bsector\b|\bestate\b|\bindustrial\b|"
                r"\bmarket\b|\blane\b|\bavenue\b|\bindia\b|,)",
                value,
                re.IGNORECASE,
            )
        )

    def find_importer_value(soup):
        """Find the value next to the importer label in common Flipkart DOM layouts."""
        if not soup:
            return None

        # Table: <tr><td>label</td><td>address</td></tr>
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            for idx, cell in enumerate(cells):
                if is_importer_label(cell.get_text(" ", strip=True)):
                    for candidate_cell in cells[idx + 1:]:
                        candidate = clean(candidate_cell.get_text(" ", strip=True))
                        if looks_like_address(candidate):
                            return candidate

        # Sibling / definition-list / key-value layouts.
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

                # Last resort: inspect direct children but never return the label.
                for candidate_el in children:
                    candidate = clean(candidate_el.get_text(" ", strip=True))
                    if candidate and not is_importer_label(candidate) and looks_like_address(candidate):
                        return candidate

        return None

    def repair_details(details, soup):
        details = dict(details or {})
        current = clean(details.get("billing_address"))

        # The old parser's main defect: it copied the importer label itself.
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

        location = clean(details.get("seller_location"))
        if location and any(marker in location.lower() for marker in MARKETPLACE_LOCATION_MARKERS):
            details["seller_location"] = None
            if not details.get("billing_address"):
                details["city"] = None
                details["state"] = None
                details["pincode"] = None

        return details

    def patched_extract(soup, page_html=None):
        details = original_extract(soup, page_html)
        if isinstance(soup, str):
            soup_obj = BeautifulSoup(soup, "lxml")
        else:
            soup_obj = soup
        return repair_details(details, soup_obj)

    def patched_parse(html_content, page_url="", http_status=200):
        result = original_parse(html_content, page_url=page_url, http_status=http_status)
        soup = BeautifulSoup(html_content or "", "lxml")
        result = repair_details(result, soup)
        result["raw_address"] = result.get("billing_address")
        result["shipping_address"] = result.get("billing_address")
        if result.get("billing_address"):
            result["seller_location"] = result["billing_address"]
        elif result.get("seller_location") and any(
            marker in clean(result["seller_location"]).lower()
            for marker in MARKETPLACE_LOCATION_MARKERS
        ):
            result["seller_location"] = None
        return result

    def is_bad_cached_address(value):
        value = clean(value)
        if not value:
            return False
        low = value.lower()
        return is_importer_label(value) or any(marker in low for marker in MARKETPLACE_LOCATION_MARKERS)

    def patched_pending(self):
        pending = list(original_pending(self))
        pending_keys = {key for key, _ in pending}
        for key, seller in self.sellers.items():
            enriched = seller.get("enriched_data", {}) or {}
            candidates = [
                seller.get("seller_location"),
                enriched.get("Billing Address"),
                enriched.get("billing_address"),
                enriched.get("Shipping Address"),
                enriched.get("shipping_address"),
            ]
            if any(is_bad_cached_address(v) for v in candidates) and key not in pending_keys:
                pending.append((key, seller))
        return pending

    def patched_mark_enriched(self, storage_key, enriched_data):
        # Remove invalid legacy address values before the original merge logic
        # sees them; otherwise the old label would be treated as a valid value
        # and preserved forever.
        seller = self.sellers.get(storage_key)
        if seller:
            existing = seller.get("enriched_data", {}) or {}
            if any(
                is_bad_cached_address(existing.get(k))
                for k in (
                    "Billing Address", "billing_address", "Shipping Address", "shipping_address"
                )
            ):
                for k in (
                    "Billing Address", "billing_address", "Shipping Address", "shipping_address"
                ):
                    if is_bad_cached_address(existing.get(k)):
                        existing.pop(k, None)
                seller["enriched_data"] = existing

            for k in ("seller_location", "city", "state", "pincode"):
                if is_bad_cached_address(seller.get(k)):
                    seller[k] = None

        original_mark_enriched(self, storage_key, enriched_data)

    _product_parser.extract_seller_and_ratings = patched_extract
    _product_parser.parse_product_page = patched_parse
    _seller_extractor.SellerRepository.get_all_pending_sellers = patched_pending
    _seller_extractor.SellerRepository.mark_enriched = patched_mark_enriched
    _product_parser._IMPORTER_ADDRESS_PATCHED = True


_install_address_extraction_patch()
