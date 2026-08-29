"""Unit tests for the validator and scoring module."""

import pytest
from scraper.validator import (
    calculate_field_confidence,
    determine_seller_status,
    validate_email,
    validate_fssai,
    validate_gst,
    validate_pan,
    validate_phone,
    validate_pincode,
)
from scraper.config import (
    STATUS_VERIFIED,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_NEEDS_REVIEW,
    STATUS_NOT_FOUND,
)


def test_validate_gst_valid():
    valid_gst = "27AAPFU0939F1ZV"  # Maharashtra (27)
    assert validate_gst(valid_gst) == "27AAPFU0939F1ZV"
    assert validate_gst("29AAAAA0000A1Z5") == "29AAAAA0000A1Z5"  # Karnataka (29)
    assert validate_gst("07ABCDE1234F1Z5") == "07ABCDE1234F1Z5"  # Delhi (07)


def test_validate_gst_invalid():
    assert validate_gst("99AAPFU0939F1ZV") is None  # Invalid state code 99
    assert validate_gst("INVALID_GST") is None
    assert validate_gst("") is None
    assert validate_gst(None) is None


def test_validate_pan_valid():
    assert validate_pan("AAPFU0939F") == "AAPFU0939F"  # Firm (F)
    assert validate_pan("ABCPE1234F") == "ABCPE1234F"  # Person (P)
    assert validate_pan("ABCCE1234F") == "ABCCE1234F"  # Company (C)


def test_validate_pan_from_gst():
    # Chars 2:12 of 27AAPFU0939F1ZV is AAPFU0939F
    assert validate_pan(None, "27AAPFU0939F1ZV") == "AAPFU0939F"


def test_validate_pan_invalid():
    assert validate_pan("12345ABCDE") is None  # Invalid structure
    assert validate_pan("AAPXU0939F") is None  # 'X' is invalid entity type


def test_validate_email_valid():
    assert validate_email("contact@acmeretail.com") == "contact@acmeretail.com"
    assert validate_email("SUPPORT@RETAILSTORE.IN") == "support@retailstore.in"
    assert validate_email("info.sales@brand.co.in") == "info.sales@brand.co.in"


def test_validate_email_invalid_and_blacklisted():
    assert validate_email("user@example.com") is None  # Blacklisted domain
    assert validate_email("icon@2x.png") is None  # Image asset
    assert validate_email("not-an-email") is None
    assert validate_email("") is None


def test_validate_phone_valid():
    assert validate_phone("9876543210") == "9876543210"
    assert validate_phone("+91 9876543210") == "9876543210"
    assert validate_phone("09876543210") == "9876543210"
    assert validate_phone("91-9123456789") == "9123456789"


def test_validate_phone_invalid():
    assert validate_phone("1234567890") is None  # Doesn't start with 6-9
    assert validate_phone("98765") is None
    assert validate_phone(None) is None


def test_validate_pincode():
    assert validate_pincode("400001") == "400001"
    assert validate_pincode("560001") == "560001"
    assert validate_pincode("012345") is None  # Leading zero
    assert validate_pincode("40001") is None  # 5 digits


def test_validate_fssai():
    assert validate_fssai("10012011000123") == "10012011000123"
    assert validate_fssai("20014011000456") == "20014011000456"
    assert validate_fssai("30012011000123") is None  # Doesn't start with 1 or 2


def test_confidence_and_status():
    conf, src = calculate_field_confidence("gst_number", "27AAPFU0939F1ZV", "gst_portal", gst_validated=True)
    assert conf >= 0.95
    assert src == "gst_portal"

    verified_rec = {
        "gst_number": "27AAPFU0939F1ZV",
        "website_url": "https://acmeretail.in",
        "city": "Mumbai",
        "state": "Maharashtra",
    }
    assert determine_seller_status(verified_rec, {}) == STATUS_VERIFIED

    partial_rec = {
        "gst_number": None,
        "contact_number": "9876543210",
        "email": "contact@seller.in",
        "city": "Bengaluru",
    }
    assert determine_seller_status(partial_rec, {}) == STATUS_PARTIALLY_VERIFIED

    empty_rec = {"gst_number": None}
    assert determine_seller_status(empty_rec, {}) == STATUS_NOT_FOUND


def test_validate_seller_association():
    from scraper.validator import validate_seller_association

    # Exact name in snippet
    assert validate_seller_association(
        "REEPREECREATION",
        "Welcome to REEPREECREATION online store. Contact us for bulk orders.",
    )
    # Spaced name match
    assert validate_seller_association(
        "REEPREECREATION",
        "REEPREE CREATION GST number 24AABCS1234D1Z5 registered in Surat Gujarat",
    )
    # Brand token in URL
    assert validate_seller_association(
        "Boat Lifestyle",
        "Official store and accessories",
        source_url="https://www.boat-lifestyle.com/contact",
    )
    # Distinctive token match
    assert validate_seller_association(
        "CHENECLOTH",
        "Chene Clothings Surat Gujarat India",
    )
    # Completely unrelated content
    assert not validate_seller_association(
        "REEPREECREATION",
        "Python programming guide and dictionary tutorials for beginners",
        source_url="https://learnpython.org",
    )


def test_cross_check_seller_data():
    from scraper.validator import cross_check_seller_data

    raw_data = {
        "gst_number": "24AABCS1234D1Z5",
        "pan_number": "INVALID_PAN",  # Should be corrected to AABCS1234D from GSTIN
        "contact_number": "+91 9876543210",
        "email": "SALES@REEPREE.COM",
        "state": None,  # Should be resolved to Gujarat (24)
        "pincode": "395006",
        "owner_name": "Rajesh Kumar",
    }

    checked = cross_check_seller_data(raw_data)
    assert checked["gst_number"] == "24AABCS1234D1Z5"
    assert checked["pan_number"] == "AABCS1234D"
    assert checked["contact_number"] == "9876543210"
    assert checked["email"] == "sales@reepree.com"
    assert checked["state"] == "Gujarat"
    assert checked["pincode"] == "395006"
    assert checked["owner_name"] == "Rajesh Kumar"

