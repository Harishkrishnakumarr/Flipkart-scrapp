"""Quick verification script to test pipeline with a sample seller."""

import asyncio
from pathlib import Path
from scraper.seller_extractor import SellerRepository
from scraper.web_research import WebResearchEngine
from scraper.exporter import export_sellers_to_excel

import pytest

@pytest.mark.asyncio
async def test_pipeline_integration():
    repo = SellerRepository(Path("data/sellers.json"))
    
    # Add a real Indian brand / seller for testing
    repo.add_or_update_seller(
        raw_seller_name="Boat Lifestyle",
        product_url="https://www.flipkart.com/boat-airdopes-131-bluetooth-headset/p/itm123",
        category_hierarchy=["Electronics", "Audio", "Headphones", "Wireless Earbuds"],
        star_rating=4.5
    )
    
    engine = WebResearchEngine()
    enriched = await engine.research_seller(
        seller_name="Boat Lifestyle",
        categories=["Electronics > Audio"],
        star_rating=4.5
    )
    await engine.close()
    
    out_file = export_sellers_to_excel([enriched], Path("output/flipkart_sellers.xlsx"))
    print("Integration test passed! Exported to:", out_file)
    print("Enriched record summary:", enriched["seller_name"] if "seller_name" in enriched else "", "Status:", enriched["status"])

if __name__ == "__main__":
    asyncio.run(test_pipeline_integration())
