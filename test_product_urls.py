"""Verification script for testing the 3 required Flipkart product URLs."""

import asyncio
import logging
import sys
import time
from scraper.flipkart_search import FlipkartSearchScraper

# Configure root logger to output to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

TEST_URLS = [
    "https://www.flipkart.com/ato-solid-single-breasted-casual-festive-festive-wedding-formal-party-wedding-men-blazer/p/itm274cd3b4cc222?pid=BZRHGDHBFKV7YBDM",
    "https://www.flipkart.com/nf-new-fashion-solid-tuxedo-style-formal-festive-wedding-party-men-blazer/p/itmc49bcce7ea363?pid=BZRHKZVXMFF23QKM",
    "https://www.flipkart.com/peter-england-self-design-tuxedo-style-formal-men-blazer/p/itm72fef9f0c5381?pid=BZRHCHEQ6HNYZ2AZ",
]


async def run_test():
    print("\n==================================================")
    print("TESTING 3 FLIPKART PRODUCT URLS")
    print("==================================================\n")

    scraper = FlipkartSearchScraper(headless=True)
    await scraper.start()

    results = []
    try:
        for idx, url in enumerate(TEST_URLS, 1):
            print(f"\n>>> [TEST {idx}/3] Processing: {url}")
            t0 = time.time()
            record = await scraper.extract_seller_from_product_url(
                url,
                input_row={"category_hierarchy": ["Clothing and Accessories", "Men's Clothing", "Blazers"]},
            )
            duration = time.time() - t0
            record["test_duration"] = duration
            results.append(record)

            print(f"\n--- Result for Test {idx} ---")
            print(f"Seller Name:        {record.get('seller_name')}")
            print(f"Fulfillment By:     {record.get('fulfillment_by')}")
            print(f"Star Rating:        {record.get('star_rating')}")
            print(f"Product Rating:     {record.get('product_rating')}")
            print(f"Extraction Status:  {record.get('extraction_status')}")
            print(f"Fetch Time:         {duration:.2f}s")

    finally:
        scraper.log_product_fetch_summary()
        await scraper.stop()

    print("\n==================================================")
    print("FINAL TEST SUMMARY")
    print("==================================================")
    for idx, res in enumerate(results, 1):
        print(
            f"Product {idx}: Seller='{res.get('seller_name')}' | "
            f"Status='{res.get('extraction_status')}' | "
            f"Duration={res.get('test_duration', 0):.2f}s"
        )
    print("==================================================\n")


if __name__ == "__main__":
    asyncio.run(run_test())
