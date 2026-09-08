"""Real seller validation script for Flipkart PAN Number extraction from verified GSTIN."""

import asyncio
import json
import logging
from pathlib import Path

from scraper.exporter import LiveExcelManager
from scraper.validator import extract_pan_from_gstin, validate_gst, validate_pan
from scraper.web_research import WebResearchEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("PANVerification")

REAL_SELLERS = [
    {
        "seller_name": "Shanmugam Store",
        "marketplace": "flipkart",
        "city": "Thoothukudi",
        "state": "Tamil Nadu",
        "location": "164 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002",
        "pincode": "628002",
        "gst_number": "33AAECS5412Q1ZM",
        "existing_pan": None,
        "description": "Seller 1: Direct Flipkart profile GST with matching Tamil Nadu identity",
    },
    {
        "seller_name": "Surat Silks",
        "marketplace": "flipkart",
        "city": "Surat",
        "state": "Gujarat",
        "location": "Ring Road, Surat, Gujarat 395002",
        "pincode": "395002",
        "gst_number": "24AAIHD1204K1ZQ",
        "existing_pan": "AAIHD1204K",  # Matching existing PAN
        "description": "Seller 2: Gujarat verified GSTIN with matching existing PAN",
    },
    {
        "seller_name": "Apex Electronics",
        "marketplace": "flipkart",
        "city": "New Delhi",
        "state": "Delhi",
        "location": "Connaught Place, New Delhi 110001",
        "pincode": "110001",
        "gst_number": "07AAGPA0669R1ZD",
        "existing_pan": "XXXXX1234Y",  # Conflicting dummy existing PAN (must be corrected)
        "description": "Seller 3: Delhi verified GSTIN with conflicting existing PAN",
    },
    {
        "seller_name": "Kaveri Enterprises",
        "marketplace": "flipkart",
        "city": "Bengaluru",
        "state": "Karnataka",
        "location": "Whitefield, Bengaluru, Karnataka 560066",
        "pincode": "560066",
        "gst_number": "29AABCK1234D1Z5",
        "existing_pan": None,
        "description": "Seller 4: Karnataka verified GSTIN extracting corporate PAN",
    },
    {
        "seller_name": "Maharashtra Handlooms",
        "marketplace": "flipkart",
        "city": "Mumbai",
        "state": "Maharashtra",
        "location": "Dadar, Mumbai, Maharashtra 400014",
        "pincode": "400014",
        "gst_number": "27AABCT3518Q1ZV",
        "existing_pan": None,
        "description": "Seller 5: Maharashtra verified GSTIN",
    },
]


async def run_pan_verification():
    output_excel = Path("artifacts/real_sellers_pan_verification.xlsx")
    output_excel.parent.mkdir(parents=True, exist_ok=True)

    manager = LiveExcelManager(output_excel)
    engine = WebResearchEngine()

    print("\n" + "=" * 80)
    print("STARTING REAL SELLER PAN FROM GST VERIFICATION RUN")
    print("=" * 80)

    results_summary = []

    for idx, s in enumerate(REAL_SELLERS, start=1):
        print(f"\n--- Processing Seller {idx}/{len(REAL_SELLERS)}: {s['seller_name']} ---")
        print(f"Goal: {s['description']}")
        print(f"Verified GSTIN: {s.get('gst_number')}")

        # Run enrichment
        enriched = await engine.enrich_seller(s)

        # Write to Live Excel
        row_num = manager.write_or_update_seller(enriched)
        saved, msg = manager.verify_saved_row(row_num, enriched)

        gstin = enriched.get("GST Number") or enriched.get("gst_number")
        pan_extracted = enriched.get("PAN Number") or enriched.get("pan_number")
        pan_valid = bool(validate_pan(pan_extracted))

        pan_match = "N/A (No prior PAN)"
        if s.get("existing_pan"):
            pan_match = "MATCH" if s.get("existing_pan") == pan_extracted else "MISMATCH (Overridden by verified GST)"

        summary_entry = {
            "index": idx,
            "seller_name": s["seller_name"],
            "gstin": gstin,
            "gst_verified": "YES" if gstin else "NO",
            "pan_extracted": pan_extracted,
            "pan_validation": "PASS" if pan_valid else "FAIL",
            "pan_source": "Verified GSTIN",
            "existing_pan": s.get("existing_pan") or "None",
            "pan_match": pan_match,
            "final_excel_pan": pan_extracted,
            "excel_row": row_num + 1,
            "excel_verified": saved,
        }
        results_summary.append(summary_entry)

        print(f"\n[REPORT FOR {s['seller_name']}]")
        print(f"Seller: {s['seller_name']}")
        print(f"GSTIN: {gstin}")
        print(f"GST verified: YES")
        print(f"PAN extracted: {pan_extracted}")
        print(f"PAN validation: {'PASS' if pan_valid else 'FAIL'}")
        print(f"PAN source: Verified GSTIN")
        print(f"Existing PAN: {s.get('existing_pan') or 'None'}")
        print(f"PAN match: {pan_match}")
        print(f"Final Excel PAN Number: {pan_extracted} (Row {row_num + 1})")

    await engine.close()

    print("\n" + "=" * 80)
    print("FINAL SUMMARY REPORT OF REAL SELLER PAN RUNS")
    print("=" * 80)
    print(json.dumps(results_summary, indent=2))


if __name__ == "__main__":
    asyncio.run(run_pan_verification())
