"""Unit tests for seller extraction and deduplication."""

from pathlib import Path
from scraper.seller_extractor import SellerRepository, normalize_seller_name


def test_normalize_seller_name():
    k1, d1 = normalize_seller_name("ABC ENTERPRISES")
    k2, d2 = normalize_seller_name("abc enterprises")
    k3, d3 = normalize_seller_name("  ABC Enterprises! ")

    assert k1 == "abc enterprises"
    assert k2 == "abc enterprises"
    assert k3 == "abc enterprises"
    assert d1 == "Abc Enterprises"


def test_seller_repository_deduplication(tmp_path: Path):
    json_path = tmp_path / "test_sellers.json"
    repo = SellerRepository(json_path)

    # Add variant 1
    repo.add_or_update_seller(
        raw_seller_name="RETAIL HUB",
        product_url="https://www.flipkart.com/item-1/p/123",
        category_hierarchy=["Electronics", "Mobiles"],
        star_rating=4.2,
    )

    # Add variant 2 (different case, new product URL)
    repo.add_or_update_seller(
        raw_seller_name="Retail Hub",
        product_url="https://www.flipkart.com/item-2/p/456",
        category_hierarchy=["Electronics", "Audio"],
        star_rating=4.5,
    )

    all_sellers = repo.get_all_sellers()
    assert len(all_sellers) == 1
    seller = all_sellers[0]
    assert seller["seller_name"] == "Retail Hub"
    assert len(seller["product_urls"]) == 2
    assert len(seller["categories"]) == 2
    assert seller["star_rating"] == 4.5
