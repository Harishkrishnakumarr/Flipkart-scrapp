"""Real seller validation script for Flipkart GST extraction, identity verification, and live Excel persistence."""

import asyncio
import json
import logging
from pathlib import Path

from scraper.exporter import LiveExcelManager
from scraper.web_research import WebResearchEngine
from scraper.validator import validate_gst, match_gst_to_seller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("GSTVerification")

TEST_SELLERS = [
    {
        "seller_name": "Shanmugam Store",
        "marketplace": "flipkart",
        "city": "Thoothukudi",
        "state": "Tamil Nadu",
        "location": "164 Sivan Koil Street, Thoothukudi, Tamil Nadu 628002",
        "pincode": "628002",
        "gst_number": "33AAECS5412Q1ZM",  # Direct Flipkart GST
        "description": "Seller 1: Direct Flipkart profile GST with matching Tamil Nadu identity",
    },
    {
        "seller_name": "ABC Enterprises",
        "marketplace": "flipkart",
        "city": "Surat",
        "state": "Gujarat",
        "location": "Ring Road, Surat, Gujarat 395002",
        "pincode": "395002",
        "existing_gst": None,  # External search required with generic name protection
        "description": "Seller 2: Generic name requiring strict city/state/pincode anchoring",
    },
    {
        "seller_name": "Metro Traders",
        "marketplace": "flipkart",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "location": "Relief Road, Ahmedabad, Gujarat 380001",
        "pincode": "380001",
        "existing_gst": None,
        "description": "Seller 3: Same-name business protection across different states",
    },
    {
        "seller_name": "REEPREECREATION",
        "marketplace": "flipkart",
        "city": "Surat",
        "state": "Gujarat",
        "location": "Surat, Gujarat 395002",
        "pincode": "395002",
        "existing_gst": None,
        "description": "Seller 4: External web search required because Flipkart does not expose GST",
    },
    {
        "seller_name": "REDTAPELIMITED",
        "marketplace": "flipkart",
        "city": "Kanpur",
        "state": "Uttar Pradesh",
        "location": "Kanpur, Uttar Pradesh 208001",
        "pincode": "208001",
        "existing_gst": None,
        "website_url": "https://redtape.com",
        "description": "Seller 5: High-volume brand requiring official website and registry discovery",
    },
]


async def run_gst_verification():
    output_excel = Path("artifacts/real_sellers_gst_verification.xlsx")
    output_excel.parent.mkdir(parents=True, exist_ok=True)

    manager = LiveExcelManager(output_excel)
    engine = WebResearchEngine()

    print("\n" + "=" * 80)
    print("STARTING REAL FLIPKART SELLER GST VERIFICATION RUN")
    print("=" * 80)

    results_summary = []

    for idx, s in enumerate(TEST_SELLERS, start=1):
        print(f"\n--- Processing Seller {idx}/{len(TEST_SELLERS)}: {s['seller_name']} ---")
        print(f"Goal: {s['description']}")
        print(f"Location: {s.get('city')}, {s.get('state')} ({s.get('pincode')})")

        # Run full enrichment
        enriched = await engine.enrich_seller(s)

        # Write to Live Excel
        row_num = manager.write_or_update_seller(enriched)
        saved, msg = manager.verify_saved_row(row_num, enriched)

        gst_number = enriched.get("GST Number") or enriched.get("gst_number")
        pan_number = enriched.get("PAN Number") or enriched.get("pan_number")

        summary_entry = {
            "index": idx,
            "seller_name": s["seller_name"],
            "location": f"{s.get('city')}, {s.get('state')} {s.get('pincode')}",
            "flipkart_gst": s.get("existing_gst") or "NOT_PROVIDED",
            "accepted_gstin": gst_number or "NOT_FOUND",
            "pan_derived": pan_number or "NOT_FOUND",
            "excel_row": row_num + 1,
            "excel_verified": saved,
        }
        results_summary.append(summary_entry)

        print(f"\n[SUMMARY RESULT FOR {s['seller_name']}]")
        print(f"Accepted GSTIN: {gst_number}")
        print(f"Derived PAN: {pan_number}")
        print(f"Excel Column 'GST Number' saved: {saved} at worksheet row {row_num + 1}")

    await engine.close()

    print("\n" + "=" * 80)
    print("FINAL SUMMARY REPORT OF REAL SELLER RUNS")
    print("=" * 80)
    print(json.dumps(results_summary, indent=2))


if __name__ == "__main__":
    asyncio.run(run_gst_verification())
