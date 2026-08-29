"""Unit tests for web research engine, query generation, relevance scoring, and generic enrichment."""

import pytest
from scraper.web_research import (
    WebResearchEngine,
    generate_seller_variations,
    generate_targeted_queries_for_field,
    score_search_result_relevance,
)


def test_generate_seller_variations():
    # Concatenated seller name
    vars_reepree = generate_seller_variations("REEPREECREATION")
    assert "REEPREECREATION" in vars_reepree
    assert "REEPREE CREATION" in vars_reepree
    assert "REEPREE" in vars_reepree

    # Name with numbers
    vars_ks = generate_seller_variations("KSCOLLECTION07")
    assert "KSCOLLECTION07" in vars_ks
    assert any("KS COLLECTION" in v for v in vars_ks)

    # CamelCase name
    vars_chene = generate_seller_variations("CheneCloth")
    assert "CheneCloth" in vars_chene
    assert "Chene Cloth" in vars_chene


def test_generate_targeted_queries_for_field():
    # GST queries
    gst_queries = generate_targeted_queries_for_field("REEPREECREATION", "gst_number")
    assert '"REEPREECREATION" GST' in gst_queries
    assert '"REEPREECREATION" GSTIN' in gst_queries
    assert '"REEPREECREATION" "GST number"' in gst_queries
    assert any("REEPREE CREATION" in q for q in gst_queries)

    # Pincode queries
    pin_queries = generate_targeted_queries_for_field("REEPREECREATION", "pincode")
    assert '"REEPREECREATION" pincode' in pin_queries
    assert '"REEPREECREATION" "postal code"' in pin_queries
    assert '"REEPREECREATION" address' in pin_queries

    # Address queries
    addr_queries = generate_targeted_queries_for_field("REEPREECREATION", "address")
    assert '"REEPREECREATION" address' in addr_queries
    assert '"REEPREECREATION" "registered address"' in addr_queries
    assert '"REEPREECREATION" "business address"' in addr_queries

    # Phone queries
    phone_queries = generate_targeted_queries_for_field("REEPREECREATION", "contact_number")
    assert '"REEPREECREATION" phone' in phone_queries
    assert '"REEPREECREATION" mobile' in phone_queries
    assert '"REEPREECREATION" "contact number"' in phone_queries

    # Email queries
    email_queries = generate_targeted_queries_for_field("REEPREECREATION", "email")
    assert '"REEPREECREATION" email' in email_queries
    assert '"REEPREECREATION" "email address"' in email_queries

    # Owner queries
    owner_queries = generate_targeted_queries_for_field("REEPREECREATION", "owner_name")
    assert '"REEPREECREATION" owner' in owner_queries
    assert '"REEPREECREATION" founder' in owner_queries
    assert '"REEPREECREATION" proprietor' in owner_queries

    # Official website queries
    web_queries = generate_targeted_queries_for_field("REEPREECREATION", "website_url")
    assert '"REEPREECREATION" official website' in web_queries
    assert '"REEPREECREATION" brand website' in web_queries


def test_identify_candidate_websites():
    engine = WebResearchEngine()
    search_results = [
        {"title": "OmniTech - Buy on Flipkart", "url": "https://www.flipkart.com/seller/omnitech", "snippet": "..."},
        {"title": "OmniTech Official Store", "url": "https://www.omnitechretail.in/about", "snippet": "..."},
        {"title": "OmniTech on Instagram", "url": "https://www.instagram.com/omnitech", "snippet": "..."},
        {"title": "OmniTech Corp Profile", "url": "https://omnitechcorp.com/contact-us", "snippet": "..."},
    ]

    candidates = engine._identify_candidate_websites("OmniTech", search_results)
    assert "https://www.omnitechretail.in" in candidates
    assert "https://omnitechcorp.com" in candidates
    # Ensure aggregators / social platforms are excluded
    assert not any("flipkart.com" in c or "instagram.com" in c for c in candidates)


def test_score_search_result_relevance():
    # Relevant result for 'ABC ENTERPRISES'
    rel_res = {
        "title": "ABC ENTERPRISES India - Official Portal",
        "url": "https://www.abcenterprises.co.in/contact",
        "snippet": "Contact ABC Enterprises GSTIN 27AAPFU0939F1ZV phone 9876543210 email info@abcenterprises.co.in",
    }
    score = score_search_result_relevance("ABC ENTERPRISES", rel_res)
    assert score >= 40

    # Irrelevant tutorial/spam result
    irrel_res = {
        "title": "Python Programming Tutorial and Dictionary Meaning",
        "url": "https://learnpython.org/tutorial",
        "snippet": "Free python tutorial on list comprehensions and dictionaries",
    }
    irrel_score = score_search_result_relevance("ABC ENTERPRISES", irrel_res)
    assert irrel_score < 40


def test_extract_from_snippets():
    engine = WebResearchEngine()
    results = [
        {
            "title": "SuperRetail India - GST & Contact",
            "snippet": "SuperRetail GSTIN: 27AAPFU0939F1ZV. Contact: 9876543210, Email: sales@superretail.co.in. Plot 10, MIDC, Andheri East, Mumbai 400069",
            "url": "https://gstsearch.in/view/27AAPFU0939F1ZV",
        }
    ]

    extracted = engine._extract_from_snippets(results, seller_name="SuperRetail")
    assert extracted["gst_number"] == "27AAPFU0939F1ZV"
    assert extracted["pan_number"] == "AAPFU0939F"
    assert extracted["contact_number"] == "9876543210"
    assert extracted["email"] == "sales@superretail.co.in"
    assert extracted["pincode"] == "400069"


def test_teamexports_mocked_bing_enrichment():
    """Verify field-specific extraction on mocked Bing results for TEAMEXPORTS."""
    from scraper.web_research import (
        extract_address,
        extract_email,
        extract_gst,
        extract_owner,
        extract_pan,
        extract_phone,
        extract_pincode,
    )

    mock_results = [
        {
            "title": "GST Number for TEAM EXPORT in Tirupur, Tamil Nadu",
            "url": "https://www.mastersindia.net/gst/team-export-33BNBPS7232R1ZG",
            "snippet": "The GST number for TEAMEXPORT is 33BNBPS7232R1ZG. Owner: Rathinam Senthilkumar. Address: Plot 12, Export Nagar, Tirupur, Tamil Nadu - 641604. Business type: Proprietorship. Phone: 9842100000. Email: info@teamexport.in",
        }
    ]

    # 1. GST Extraction
    gst_res = extract_gst(mock_results, "TEAMEXPORTS")
    assert gst_res is not None
    assert gst_res[0] == "33BNBPS7232R1ZG"

    # 2. Owner Extraction
    owner_res = extract_owner(mock_results, "TEAMEXPORTS")
    assert owner_res is not None
    assert owner_res[0] == "Rathinam Senthilkumar"

    # 3. Pincode Extraction
    pin_res = extract_pincode(mock_results, "TEAMEXPORTS")
    assert pin_res is not None
    assert pin_res[0] == "641604"

    # 4. PAN Extraction (derived from GST)
    pan_res = extract_pan(mock_results, "TEAMEXPORTS", gst_number=gst_res[0])
    assert pan_res is not None
    assert pan_res[0] == "BNBPS7232R"

    # 5. Address Extraction
    addr_res = extract_address(mock_results, "TEAMEXPORTS", gst_number=gst_res[0])
    assert addr_res is not None
    assert addr_res[0]["state"] == "Tamil Nadu"
    assert addr_res[0]["pincode"] == "641604"


def test_teamexports_association_normalization():
    """Verify controlled normalized seller matching between TEAMEXPORTS and TEAMEXPORT."""
    from scraper.validator import validate_seller_association

    snippet_singular = "The GST number for TEAMEXPORT is 33BNBPS7232R1ZG. Owner: Rathinam Senthilkumar."
    snippet_spaced = "TEAM EXPORT registered in Tirupur, Tamil Nadu. GSTIN 33BNBPS7232R1ZG."
    snippet_plural_spaced = "Official details for TEAM EXPORTS wholesale business."
    snippet_unrelated = "Cricket team exports sports goods to Australia."

    assert validate_seller_association("TEAMEXPORTS", snippet_singular) is True
    assert validate_seller_association("TEAMEXPORTS", snippet_spaced) is True
    assert validate_seller_association("TEAMEXPORTS", snippet_plural_spaced) is True
    assert validate_seller_association("TEAMEXPORTS", snippet_unrelated) is False


# ==============================================================================
# Requirement 19: Comprehensive Mocked Bing HTML / Snippet Test Scenarios
# ==============================================================================

def test_req19_1_relevant_seller_result_processed():
    """1. Relevant seller result -> processed."""
    engine = WebResearchEngine()
    mock_results = [
        {
            "title": "OVIDA SECRET Official Store - GST and Contact",
            "url": "https://www.zaubacorp.com/company/OVIDA-SECRET-RETAIL",
            "snippet": "OVIDA SECRET GSTIN: 27AABCU9603R1ZM. Proprietor: Anita Sharma. Address: Plot 44, Linking Road, Bandra West, Mumbai 400050",
        }
    ]
    rel, rej = engine._filter_search_results("OVIDASECRET", mock_results, min_score=20)
    assert len(rel) == 1
    assert len(rej) == 0
    extracted = engine._extract_from_snippets(rel, seller_name="OVIDASECRET")
    assert extracted["gst_number"] == "27AABCU9603R1ZM"
    assert extracted["owner_name"] == "Anita Sharma"


def test_req19_2_completely_irrelevant_result_discarded():
    """2. Completely irrelevant result (e.g. Microsoft Excel, Zhihu) -> discarded early."""
    engine = WebResearchEngine()
    mock_junk = [
        {
            "title": "Microsoft Community - Excel Formula Help",
            "url": "https://answers.microsoft.com/en-us/msoffice/forum/all/excel-help",
            "snippet": "Learn how to use VLOOKUP and INDEX MATCH in Microsoft Excel.",
        },
        {
            "title": "Zhihu - Question Discussion Forum",
            "url": "https://www.zhihu.com/question/123456",
            "snippet": "Discussion about generic computer science concepts and tools.",
        },
        {
            "title": "Free Fonts Download",
            "url": "https://www.dafont.com/font.php",
            "snippet": "Download free fonts for Windows and Mac.",
        }
    ]
    rel, rej = engine._filter_search_results("OVIDASECRET", mock_junk, min_score=20)
    assert len(rel) == 0
    assert len(rej) == 3


def test_req19_3_seller_name_with_spaces_match():
    """3. Seller name with spaces -> matches compact seller."""
    from scraper.validator import validate_seller_association

    text = "Buy latest nightwear from OVIDA SECRET at wholesale rates in Delhi."
    assert validate_seller_association("OVIDASECRET", text) is True


def test_req19_4_seller_name_without_spaces_match():
    """4. Seller name without spaces -> matches spaced seller."""
    from scraper.validator import validate_seller_association

    text = "Company Profile of OVIDASECRET Clothing and Textiles, Surat."
    assert validate_seller_association("OVIDA SECRET", text) is True


def test_req19_5_legal_entity_discovered_from_seller_name():
    """5. Legal entity discovered from seller name -> query generation and association."""
    from scraper.validator import validate_seller_association

    text = "OVIDA SECRET (Registered as OVIDA FASHIONS PRIVATE LIMITED) GSTIN 24AABCU9603R1ZM."
    assert validate_seller_association("OVIDASECRET", text) is True
    assert validate_seller_association("OVIDA FASHIONS PRIVATE LIMITED", text) is True


def test_req19_6_valid_gst_in_relevant_snippet():
    """6. Valid GST in relevant snippet -> correctly extracted."""
    from scraper.web_research import extract_gst

    results = [
        {
            "title": "Nightdoll Nightwear Company Profile",
            "url": "https://piceapp.com/gst-number-search/nightdoll-24AAACN1234F1Z5",
            "snippet": "Nightdoll GSTIN is 24AAACN1234F1Z5 located in Ahmedabad, Gujarat.",
        }
    ]
    gst_res = extract_gst(results, "Nightdoll")
    assert gst_res is not None
    assert gst_res[0] == "24AAACN1234F1Z5"


def test_req19_7_random_gst_in_unrelated_result_rejected():
    """7. Random GST in unrelated result -> rejected due to seller association failure."""
    from scraper.web_research import extract_gst

    results = [
        {
            "title": "Reliance Retail Limited Annual Report",
            "url": "https://www.ril.com/gst-details",
            "snippet": "Reliance Retail GSTIN is 27AAACR1234F1Z5 Mumbai.",
        }
    ]
    gst_res = extract_gst(results, "OVIDASECRET")
    assert gst_res is None


def test_req19_8_address_containing_pincode_parsed():
    """8. Address containing pincode -> parsed to city, state, pincode."""
    from scraper.web_research import extract_address

    results = [
        {
            "title": "Nightdoll Garments Factory Details",
            "url": "https://www.zaubacorp.com/company/NIGHTDOLL-APPARELS",
            "snippet": "Nightdoll Apparels Address: Shed 5, GIDC Industrial Estate, Surat, Gujarat - 395006, India.",
        }
    ]
    addr_res = extract_address(results, "Nightdoll")
    assert addr_res is not None
    parsed = addr_res[0]
    assert parsed["state"] == "Gujarat"
    assert parsed["pincode"] == "395006"
    assert parsed["city"] == "Surat"


def test_req19_9_random_six_digit_number_rejected():
    """9. Random six-digit number without address/pincode context -> rejected."""
    from scraper.web_research import extract_pincode

    results = [
        {
            "title": "Nightdoll Order Tracking Number",
            "url": "https://nightdoll.example/track",
            "snippet": "Your tracking code is 542198 and invoice 987654.",
        }
    ]
    pin_res = extract_pincode(results, "Nightdoll")
    assert pin_res is None


def test_req19_10_bing_spelling_correction_variations():
    """10. Query generator creates exact seller, spaced variation, and site-specific queries."""
    gst_queries = generate_targeted_queries_for_field("OVIDASECRET", "gst")
    
    # Priority 1: Exact seller name
    assert '"OVIDASECRET" GSTIN' in gst_queries
    assert '"OVIDASECRET" "GST number"' in gst_queries
    assert '"OVIDASECRET" GST' in gst_queries
    
    # Priority 2: Spaced variation
    assert any("OVIDA SECRET" in q for q in gst_queries)
    
    # Priority 3: Site-specific searches
    assert any("site:zaubacorp.com" in q for q in gst_queries)
    assert any("site:thecompanycheck.com" in q for q in gst_queries)


