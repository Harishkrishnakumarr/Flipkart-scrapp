"""Flipkart & Marketplace Seller Scraper — Integrated Real-Time Pipeline Orchestrator.

Complete End-to-End Flow:
  1. Reads existing input (from Excel file or Google Sheets URL)
  2. Searches marketplace only when product URLs are not already provided
  3. Extracts seller_name, fulfillment_by, star_rating, product_rating
  4. Deduplicates sellers and writes initial row to Excel (Status: ENRICHMENT_PENDING)
  5. IMMEDIATELY RUNS generic enrichment waterfall: enrich_seller(seller_record)
  6. Discovers official website, extracts GST, PAN, FSSAI, Owner, Phone, Email, Address
  7. IMMEDIATELY UPDATES the EXACT SAME Excel row with enriched credentials
  8. Saves progress and cache after every seller for full crash-resilience
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from scraper.config import (
    INPUT_FILE,
    LOG_FILE,
    LOGS_DIR,
    MAX_PRODUCTS_PER_CATEGORY,
    MAX_ROWS_PER_RUN,
    OUTPUT_FILE,
    SELLERS_FILE,
    STATUS_ENRICHMENT_PENDING,
)
from scraper.excel_reader import (
    load_progress,
    read_categories_from_excel,
    read_categories_from_google_sheet,
    save_progress,
)
from scraper.exporter import LiveExcelManager, export_sellers_to_excel
from scraper.flipkart_search import FlipkartSearchScraper
from scraper.seller_extractor import SellerRepository, seller_key
from scraper.web_research import WebResearchEngine


def configure_logging(verbose: bool = False) -> logging.Logger:
    """Configure structured logging to both file and console.

    Args:
        verbose: Enable DEBUG level logging on console.

    Returns:
        Configured root logger.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("FlipkartScraper")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    # File Handler (Detailed DEBUG logs)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    # Console Handler (Clean output with UTF-8 support)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_format = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    return logger


class ScraperPipeline:
    """Coordinates end-to-end execution of the seller scraper & live enrichment pipeline."""

    def __init__(
        self,
        input_file: Path = INPUT_FILE,
        google_sheet: Optional[str] = None,
        output_file: Path = OUTPUT_FILE,
        sellers_file: Path = SELLERS_FILE,
        max_products_per_cat: int = MAX_PRODUCTS_PER_CATEGORY,
        max_rows_per_run: int = MAX_ROWS_PER_RUN,
        headless: bool = True,
        resume: bool = True,
        force_enrich: bool = False,
        start_row: Optional[int] = None,
        end_row: Optional[int] = None,
    ) -> None:
        """Initialize pipeline with paths and configurations.

        Args:
            input_file: Path to input Excel hierarchy / URLs.
            google_sheet: Google Sheets URL for input categories / products.
            output_file: Path to output Excel report.
            sellers_file: Path to sellers JSON repository file.
            max_products_per_cat: Number of products to sample per search query.
            max_rows_per_run: Maximum number of pending rows to process per run.
            headless: Run browser in headless mode.
            resume: Resume from previous progress checkpoint if available.
            force_enrich: Re-enrich all sellers even if previously completed.
        """
        self.input_file = input_file
        self.google_sheet = google_sheet
        self.output_file = output_file
        self.sellers_file = sellers_file
        self.max_products_per_cat = max_products_per_cat
        self.max_rows_per_run = max_rows_per_run or MAX_ROWS_PER_RUN
        # Initialize start/end row from parameters or environment variables
        if start_row is not None:
            self.start_row = int(start_row)
        else:
            env_start = os.getenv('FLIPKART_START_ROW')
            self.start_row = int(env_start) if env_start and env_start.isdigit() else None
        if end_row is not None:
            self.end_row = int(end_row)
        else:
            env_end = os.getenv('FLIPKART_END_ROW')
            self.end_row = int(env_end) if env_end and env_end.isdigit() else None
        self.headless = headless
        self.resume = resume
        self.force_enrich = force_enrich

        self.seller_repo = SellerRepository(self.sellers_file)
        self.search_scraper = FlipkartSearchScraper(headless=self.headless)
        self.research_engine = WebResearchEngine()
        self.excel_manager = LiveExcelManager(output_path=self.output_file)

        self.progress = load_progress() if self.resume else {
            "completed_queries": [],
            "completed_rows": [],
            "completed_sellers": [],
            "last_updated": None,
        }

        # Merge completed sellers from Excel on restart
        if self.resume:
            excel_completed = self.excel_manager.get_completed_sellers()
            current_completed = set(self.progress.get("completed_sellers", []))
            self.progress["completed_sellers"] = list(current_completed | excel_completed)

        self.logger = logging.getLogger("FlipkartScraper.Pipeline")
        self._interrupted = False

    def handle_interrupt(self) -> None:
        """Handle SIGINT gracefully by saving state."""
        self._interrupted = True
        self.logger.warning("Interrupt signal received! Gracefully saving state and progress...")
        self.progress["last_updated"] = datetime.now(timezone.utc).isoformat()
        save_progress(self.progress)
        self.seller_repo.save()

    async def enrich_seller(self, seller_record: Dict[str, Any]) -> Dict[str, Any]:
        """Pass generic seller record to the generic enrichment engine.

        Works identically for Flipkart, Amazon, and other marketplace seller records.

        Args:
            seller_record: Generic seller dictionary.

        Returns:
            Enriched seller record dictionary.
        """
        return await self.research_engine.enrich_seller(seller_record)

    def _log_seller_summary(
        self,
        seller_name: str,
        fulfillment_by: Optional[str],
        product_url: str,
        enriched_data: Dict[str, Any],
        row_num: int,
        save_verified: bool = True,
        verify_msg: Optional[str] = None,
    ) -> None:
        """Format and print structured seller extraction and enrichment summary."""
        def _f(key: str) -> str:
            val = enriched_data.get(key)
            if val is None or val == "" or val == "N/A" or val == []:
                return "NOT FOUND"
            return f"FOUND ({val})"

        status = enriched_data.get("status", "UNKNOWN")
        excel_save_status = "SUCCESS" if save_verified else f"FAILED ({verify_msg or 'Verification Mismatch'})"

        summary_msg = (
            f"\n========================================\n"
            f"FLIPKART SELLER\n"
            f"========================================\n"
            f"Seller: {seller_name}\n"
            f"Fulfilled By: {fulfillment_by or 'N/A'}\n"
            f"Product: {product_url}\n"
            f"----------------------------------------\n"
            f"ENRICHMENT\n"
            f"----------------------------------------\n"
            f"Official Website: {_f('website_url')}\n"
            f"Business Model: {_f('business_model')}\n"
            f"Business Category: {_f('business_category')}\n"
            f"Owner: {_f('owner_name')}\n"
            f"Phone: {_f('contact_number')}\n"
            f"Email: {_f('email')}\n"
            f"GST: {_f('gst_number')}\n"
            f"PAN: {_f('pan_number')}\n"
            f"FSSAI: {_f('fssai_number')}\n"
            f"Billing Address: {_f('billing_address')}\n"
            f"Shipping Address: {_f('shipping_address')}\n"
            f"City: {_f('city')}\n"
            f"State: {_f('state')}\n"
            f"Pincode: {_f('pincode')}\n"
            f"Country: {_f('country')}\n"
            f"----------------------------------------\n"
            f"EXCEL\n"
            f"----------------------------------------\n"
            f"Excel Row: {row_num}\n"
            f"Excel Update: SUCCESS\n"
            f"Excel Save: {excel_save_status}\n"
            f"Status: {status}\n"
            f"========================================"
        )
        self.logger.info(summary_msg)

    async def run(
        self,
        research_only: bool = False,
        export_only: bool = False,
    ) -> None:
        """Execute the scraper pipeline.

        Args:
            research_only: Skip Flipkart collection, research existing sellers.
            export_only: Skip scraping and research, directly generate Excel.
        """
        self.logger.info("==================================================")
        self.logger.info("       FLIPKART SELLER SCRAPER PIPELINE           ")
        self.logger.info("==================================================")

        try:
            if export_only:
                await self._step_export_only()
                return

            if not research_only:
                # STEP 1: Read Input from Google Sheets or Excel File
                if self.google_sheet:
                    self.logger.info(f"\n--- STEP 1: Reading Input from Google Sheet ({self.google_sheet}) ---")
                    all_tasks = read_categories_from_google_sheet(self.google_sheet, resume=False)
                    pending_tasks = read_categories_from_google_sheet(self.google_sheet, resume=self.resume)
                else:
                    self.logger.info(f"\n--- STEP 1: Reading Existing Flipkart Input ({self.input_file}) ---")
                    all_tasks = read_categories_from_excel(self.input_file, resume=False)
                    pending_tasks = read_categories_from_excel(self.input_file, resume=self.resume)

                total_input_rows = len(all_tasks)
                pending_before_limit = len(pending_tasks)
                already_completed = total_input_rows - pending_before_limit
                # Apply manual row range filter if specified
                if self.start_row is not None or self.end_row is not None:
                    start = self.start_row if self.start_row is not None else 1
                    end = self.end_row if self.end_row is not None else total_input_rows
                    if start < 1:
                        raise ValueError('start_row must be >= 1')
                    if end < start:
                        raise ValueError('end_row must be >= start_row')
                    original_len = pending_before_limit
                    pending_tasks = [t for t in pending_tasks if start <= t["row_index"] <= end]
                    pending_before_limit = len(pending_tasks)
                    self.logger.info(f"\n--- Applying row range filter: start={start}, end={end} (filtered {original_len - pending_before_limit} tasks) ---")
                selected_for_this_run = min(pending_before_limit, self.max_rows_per_run)
                remaining_after_selection = pending_before_limit - selected_for_this_run
                tasks_for_this_run = pending_tasks[:selected_for_this_run]

                # Run Limit Startup Diagnostics
                limit_diag_msg = (
                    f"\n========================================\n"
                    f"FLIPKART RUN LIMIT\n"
                    f"========================================\n"
                    f"Configured maximum rows per run: {self.max_rows_per_run}\n"
                    f"Total input rows: {total_input_rows}\n"
                    f"Already completed: {already_completed}\n"
                    f"Pending before limit: {pending_before_limit}\n"
                    f"Selected for this run: {selected_for_this_run}\n"
                    f"Remaining after selection: {remaining_after_selection}\n"
                    f"========================================"
                )
                self.logger.info(limit_diag_msg)

                # Edge case: No pending rows
                if pending_before_limit == 0:
                    self.logger.info("NO_PENDING_ROWS: All input rows have already been completed.")
                    if not self.seller_repo.get_all_pending_sellers():
                        return

                rows_selected = len(tasks_for_this_run)
                rows_completed = 0
                rows_failed = 0

                # STEP 2-9: Collect Sellers, Save Initial Row, Run Enrichment & Update Excel Row Immediately
                if tasks_for_this_run:
                    self.logger.info(f"Processing rows 1–{selected_for_this_run} of pending queue")
                    self.logger.info("\n--- STEP 2-9: Real-Time Collection & Live Enrichment ---")
                    await self.search_scraper.start()

                    completed_sellers_set = set(self.progress.get("completed_sellers", []))

                    for task in tasks_for_this_run:
                        if self._interrupted:
                            break

                        row_idx = task["row_index"]
                        direct_url = task.get("product_url")
                        query = task.get("query")
                        hierarchy = task.get("category_hierarchy", [])

                        cat = hierarchy[0] if len(hierarchy) > 0 else None
                        sub_cat = hierarchy[1] if len(hierarchy) > 1 else None
                        sub_sub_cat = hierarchy[2] if len(hierarchy) > 2 else None
                        sub_sub_sub_cat = hierarchy[3] if len(hierarchy) > 3 else None

                        try:
                            # If product URL already provided, use it directly (skip search!)
                            if direct_url:
                                self.logger.info(f"\n[Row {row_idx}] Direct Product URL: {direct_url}")
                                product_urls = [direct_url]
                            elif query:
                                self.logger.info(f"\n[Row {row_idx}] Searching Query: {query}")
                                product_urls = await self.search_scraper.search_and_collect_product_urls(
                                    query, max_products=self.max_products_per_cat
                                )
                            else:
                                continue

                            # Process each product URL
                            for p_url in product_urls:
                                if self._interrupted:
                                    break

                                seller_info = await self.search_scraper.extract_seller_from_product_url(
                                    p_url, input_row=task
                                )
                                seller_name = seller_info.get("seller_name")
                                fulfillment_by = seller_info.get("fulfillment_by")
                                star_rating = seller_info.get("star_rating")
                                product_rating = seller_info.get("product_rating")
                                source_type = seller_info.get("seller_source_type")

                                if not seller_name:
                                    continue

                                canonical_id = seller_key(seller_name)
                                storage_key = f"flipkart::{canonical_id}"

                                # 1. Update Seller Repository
                                self.seller_repo.add_or_update_seller(
                                    raw_seller_name=seller_name,
                                    product_url=p_url,
                                    category_hierarchy=hierarchy,
                                    star_rating=star_rating,
                                    fulfillment_by=fulfillment_by,
                                    marketplace="flipkart",
                                    product_rating=product_rating,
                                    seller_source_type=source_type,
                                )

                                # 2. Check if this seller was already enriched in a previous run
                                if self.resume and canonical_id in completed_sellers_set:
                                    cached = self.seller_repo.sellers.get(storage_key, {}).get("enriched_data") or self.research_engine.cache.get(f"enriched::{storage_key}")
                                    if cached:
                                        self.logger.info(f"Seller '{seller_name}' already enriched (Skipping re-enrichment).")
                                        continue

                                # 3. Immediately write initial record to Excel (Status: ENRICHMENT_PENDING)
                                initial_excel_data = {
                                    "seller_name": seller_name,
                                    "fulfillment_by": fulfillment_by,
                                    "marketplace": "flipkart",
                                    "status": STATUS_ENRICHMENT_PENDING,
                                    "product_url": p_url,
                                    "category": cat,
                                    "sub_category": sub_cat,
                                    "sub_sub_category": sub_sub_cat,
                                    "sub_sub_subcategory": sub_sub_sub_cat,
                                    "product_rating": product_rating,
                                    "seller_rating": star_rating,
                                    "star_rating": star_rating,
                                    "seller_source_url": p_url,
                                    "seller_source_type": source_type or "flipkart_product",
                                }

                                row_num = self.excel_manager.write_or_update_seller(initial_excel_data)
                                init_verified, init_vmsg = self.excel_manager.verify_saved_row(row_num, initial_excel_data)
                                if init_verified:
                                    self.logger.info(f"Initial record saved to Excel | Seller: {seller_name} | Row: {row_num} | Status: {STATUS_ENRICHMENT_PENDING}")
                                else:
                                    self.logger.warning(f"Initial Excel save verification issue: {init_vmsg}")

                                # 4. IMMEDIATELY RUN GENERIC ENRICHMENT WATERFALL
                                self.logger.info(f"Starting enrichment for: '{seller_name}'...")
                                generic_record = {
                                    "marketplace": "flipkart",
                                    "seller_name": seller_name,
                                    "fulfillment_by": fulfillment_by,
                                    "product_url": p_url,
                                    "seller_source_url": p_url,
                                    "seller_source_type": source_type or "flipkart_product",
                                    "category": cat or "E-Commerce Retail",
                                    "sub_category": sub_cat,
                                    "sub_sub_category": sub_sub_cat,
                                    "sub_sub_subcategory": sub_sub_sub_cat,
                                    "star_rating": star_rating,
                                    "product_rating": product_rating,
                                    "seller_confidence": seller_info.get("seller_confidence", 0.95),
                                }

                                enriched_data = await self.enrich_seller(generic_record)

                                # Debug log enriched record JSON
                                self.logger.debug(
                                    "ENRICHED RECORD:\n%s",
                                    json.dumps(enriched_data, indent=2, default=str),
                                )

                                # 5. IMMEDIATELY UPDATE THE SAME ROW IN EXCEL AND VERIFY DISK PERSISTENCE
                                updated_row = self.excel_manager.write_or_update_seller(enriched_data)
                                save_verified, verify_msg = self.excel_manager.verify_saved_row(updated_row, enriched_data)

                                # 6. Log structured summary
                                self._log_seller_summary(
                                    seller_name=seller_name,
                                    fulfillment_by=fulfillment_by,
                                    product_url=p_url,
                                    enriched_data=enriched_data,
                                    row_num=updated_row,
                                    save_verified=save_verified,
                                    verify_msg=verify_msg,
                                    )

                                # 7. Update repository and progress cache
                                self.seller_repo.mark_enriched(storage_key, enriched_data)
                                self.research_engine.cache.set(f"enriched::{storage_key}", enriched_data)

                                if canonical_id not in self.progress["completed_sellers"]:
                                    self.progress["completed_sellers"].append(canonical_id)
                                    completed_sellers_set.add(canonical_id)

                                self.progress["last_updated"] = datetime.now(timezone.utc).isoformat()
                                save_progress(self.progress)

                            # Mark query task complete
                            if query and query not in self.progress["completed_queries"]:
                                self.progress["completed_queries"].append(query)
                            if row_idx not in self.progress["completed_rows"]:
                                self.progress["completed_rows"].append(row_idx)

                            self.progress["last_updated"] = datetime.now(timezone.utc).isoformat()
                            save_progress(self.progress)
                            rows_completed += 1

                        except Exception as row_err:
                            self.logger.error(f"Error processing row {row_idx}: {row_err}", exc_info=True)
                            rows_failed += 1

                # FLIPKART RUN SUMMARY DIAGNOSTICS
                rows_still_pending = max(0, pending_before_limit - rows_completed)
                summary_diag_msg = (
                    f"\n========================================\n"
                    f"FLIPKART RUN SUMMARY\n"
                    f"========================================\n"
                    f"Rows selected: {rows_selected}\n"
                    f"Rows successfully completed: {rows_completed}\n"
                    f"Rows failed: {rows_failed}\n"
                    f"Rows still pending: {rows_still_pending}\n"
                    f"Run limit: {self.max_rows_per_run}\n"
                    f"========================================"
                )
                self.logger.info(summary_diag_msg)
                self.search_scraper.log_product_fetch_summary()

            # Check if any remaining pending or all sellers exist in data/sellers.json
            if self.force_enrich:
                target_sellers = list(self.seller_repo.sellers.items())
            else:
                target_sellers = self.seller_repo.get_all_pending_sellers()

            if target_sellers:
                self.logger.info(f"\n--- Researching {len(target_sellers)} Sellers in Repository ---")
                for storage_key, seller in target_sellers:
                    if self._interrupted:
                        break

                    seller_name = seller["seller_name"]
                    canonical_id = seller.get("canonical_id") or seller_key(seller_name)
                    marketplace = seller.get("marketplace", "flipkart")
                    categories = seller.get("categories", [])
                    star_rating = seller.get("star_rating")
                    product_rating = seller.get("product_rating")
                    fulfillment_by = seller.get("fulfillment_by")
                    product_urls = seller.get("product_urls", [])
                    p_url = product_urls[0] if product_urls else ""

                    generic_record = {
                        "marketplace": marketplace,
                        "seller_name": seller_name,
                        "fulfillment_by": fulfillment_by,
                        "product_url": p_url,
                        "seller_source_url": p_url,
                        "seller_source_type": seller.get("seller_source_type", f"{marketplace}_product"),
                        "category": categories[0] if categories else None,
                        "star_rating": star_rating,
                        "product_rating": product_rating,
                        "seller_confidence": seller.get("seller_confidence", 0.95),
                    }

                    enriched_data = await self.enrich_seller(generic_record)
                    updated_row = self.excel_manager.write_or_update_seller(enriched_data)
                    save_verified, verify_msg = self.excel_manager.verify_saved_row(updated_row, enriched_data)

                    self._log_seller_summary(
                        seller_name=seller_name,
                        fulfillment_by=fulfillment_by,
                        product_url=p_url,
                        enriched_data=enriched_data,
                        row_num=updated_row,
                        save_verified=save_verified,
                        verify_msg=verify_msg,
                    )

                    self.seller_repo.mark_enriched(storage_key, enriched_data)
                    self.research_engine.cache.set(f"enriched::{storage_key}", enriched_data)

                    if canonical_id not in self.progress["completed_sellers"]:
                        self.progress["completed_sellers"].append(canonical_id)

                    self.progress["last_updated"] = datetime.now(timezone.utc).isoformat()
                    save_progress(self.progress)

            # Final check to commit all enriched sellers to the output workbook
            all_enriched: List[Dict[str, Any]] = []
            for storage_key, seller in self.seller_repo.sellers.items():
                if seller.get("enriched_data"):
                    all_enriched.append(seller["enriched_data"])
            if all_enriched:
                committed_count = self.excel_manager.commit_all_sellers(all_enriched)
                self.logger.info(f"Final commit: verified {committed_count} enriched records in {self.output_file.resolve()}")

            self.logger.info(f"SUCCESS: Pipeline completed. Excel file updated at {self.output_file.resolve()}")

        except Exception as e:
            self.logger.exception(f"Unexpected pipeline failure: {e}")
        finally:
            await self.search_scraper.stop()
            await self.research_engine.close()
            self.seller_repo.save()
            save_progress(self.progress)
            self.logger.info("Pipeline execution finished.")

    async def _step_export_only(self) -> None:
        """Export all cached and repository sellers directly to Excel."""
        self.logger.info("Generating Excel report from cached seller records...")
        all_records: List[Dict[str, Any]] = []
        for storage_key, seller in self.seller_repo.sellers.items():
            if seller.get("enriched_data"):
                all_records.append(seller["enriched_data"])
            else:
                cached = self.research_engine.cache.get(f"enriched::{storage_key}")
                if cached:
                    all_records.append(cached)
                else:
                    seller_name = seller.get("seller_name")
                    if seller_name:
                        p_urls = seller.get("product_urls", [])
                        cats = seller.get("categories", [])
                        all_records.append({
                            "seller_name": seller_name,
                            "fulfillment_by": seller.get("fulfillment_by"),
                            "marketplace": seller.get("marketplace", "flipkart"),
                            "status": STATUS_ENRICHMENT_PENDING,
                            "product_url": p_urls[0] if p_urls else None,
                            "category": cats[0] if cats else None,
                            "star_rating": seller.get("star_rating"),
                            "product_rating": seller.get("product_rating"),
                            "seller_source_type": seller.get("seller_source_type", "flipkart_product"),
                        })

        if all_records:
            out = export_sellers_to_excel(all_records, self.output_file)
            self.logger.info(f"Exported {len(all_records)} records to {out}")
        else:
            self.logger.warning("No cached records found to export.")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Flipkart & Marketplace Seller Scraper and Autonomous Enrichment Pipeline."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help=f"Path to input Excel file (default: {INPUT_FILE})",
    )
    parser.add_argument(
        "--google-sheet",
        type=str,
        default=None,
        help="Google Sheets URL to use as product input",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_FILE,
        help=f"Path to output Excel file (default: {OUTPUT_FILE})",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=MAX_PRODUCTS_PER_CATEGORY,
        help=f"Max products per category (default: {MAX_PRODUCTS_PER_CATEGORY})",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help=f"Maximum pending rows to process in this run (default: {MAX_ROWS_PER_RUN})",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=None,
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible mode (headless by default)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh without loading previous progress checkpoint",
    )
    parser.add_argument(
        "--research-only",
        action="store_true",
        help="Skip marketplace scraping, research existing sellers in data/sellers.json",
    )
    parser.add_argument(
        "--force-enrich",
        "--re-enrich",
        action="store_true",
        dest="force_enrich",
        help="Re-enrich all sellers even if previously completed or cached",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Directly export existing enriched data to Excel without scraping or research",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging on stdout",
    )
    args = parser.parse_args()

    if args.google_sheet and args.input:
        parser.error("Cannot provide both --google-sheet and --input simultaneously.")

    return args


def main() -> None:
    """Main CLI entry point."""
    args = parse_args()
    logger = configure_logging(verbose=args.verbose)

    # Determine headless mode:
    # If --no-headless was passed -> headless = False
    # Else if --headless was explicitly passed -> headless = True
    # Else default -> headless = True
    if args.no_headless:
        headless = False
    elif args.headless is not None:
        headless = args.headless
    else:
        headless = True

    input_file = args.input or INPUT_FILE
    google_sheet = args.google_sheet
    max_rows = args.max_rows if args.max_rows is not None else MAX_ROWS_PER_RUN

    pipeline = ScraperPipeline(
        input_file=input_file,
        google_sheet=google_sheet,
        output_file=args.output,
        max_products_per_cat=args.max_products,
        max_rows_per_run=max_rows,
        headless=headless,
        resume=not args.no_resume,
        force_enrich=args.force_enrich,
    )

    def sigint_handler(signum: int, frame: Any) -> None:
        pipeline.handle_interrupt()

    try:
        signal.signal(signal.SIGINT, sigint_handler)
    except Exception:
        pass

    try:
        asyncio.run(
            pipeline.run(
                research_only=args.research_only,
                export_only=args.export_only,
            )
        )
    except KeyboardInterrupt:
        logger.info("Terminated by user.")


if __name__ == "__main__":
    main()
