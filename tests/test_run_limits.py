"""Unit and integration tests for batch execution run limits and pending row progression."""

import json
import pytest
from pathlib import Path
import openpyxl

from main import ScraperPipeline
from scraper.config import DEFAULT_MAX_ROWS_PER_RUN, MAX_ROWS_PER_RUN
from scraper.excel_reader import load_progress, save_progress
from scraper.seller_extractor import SellerRepository


def create_test_excel(file_path: Path, num_rows: int = 10) -> Path:
    """Helper to create a test Excel file with standard categories."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["category", "sub_category", "sub_sub_category", "sub_sub_subcategory"])
    for i in range(1, num_rows + 1):
        ws.append([f"Category_{i}", f"SubCategory_{i}", f"SubSub_{i}", f"Item_{i}"])
    wb.save(file_path)
    return file_path


@pytest.mark.asyncio
async def test_batch_run_limit_first_and_second_run_progression(tmp_path: Path, monkeypatch):
    """Verify Run 1 processes first 5 rows, and Run 2 skips them and processes the next 5 rows."""
    excel_file = create_test_excel(tmp_path / "input.xlsx", num_rows=10)
    output_file = tmp_path / "flipkart_sellers.xlsx"
    progress_file = tmp_path / "progress.json"
    sellers_file = tmp_path / "sellers.json"

    # Point PROGRESS_FILE and SELLERS_FILE to tmp_path
    monkeypatch.setattr("scraper.excel_reader.PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scraper.config.PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scraper.config.SELLERS_FILE", sellers_file)
    monkeypatch.setattr("main.SELLERS_FILE", sellers_file)

    async def mock_start():
        pass

    async def mock_close():
        pass

    async def mock_search(query: str, max_products: int = 20):
        return [f"https://www.flipkart.com/{query.replace(' ', '-')}/p/itm123"]

    async def mock_extract_seller(p_url: str, input_row=None):
        return {
            "seller_name": f"Seller_For_{input_row.get('query', 'item')}",
            "fulfillment_by": "Flipkart",
            "star_rating": "4.5",
            "product_rating": "4.2",
            "seller_source_type": "flipkart_product",
        }

    async def mock_enrich(generic_record):
        return {
            **generic_record,
            "status": "VERIFIED",
            "website_url": "https://example.com",
            "gst_number": "27AAPFU0939F1ZV",
        }

    # RUN 1: Process first 5 rows
    pipeline_run1 = ScraperPipeline(
        input_file=excel_file,
        output_file=output_file,
        sellers_file=sellers_file,
        max_rows_per_run=5,
        resume=True,
    )
    pipeline_run1.search_scraper.start = mock_start
    pipeline_run1.search_scraper.close = mock_close
    pipeline_run1.search_scraper.search_and_collect_product_urls = mock_search
    pipeline_run1.search_scraper.extract_seller_from_product_url = mock_extract_seller
    pipeline_run1.enrich_seller = mock_enrich

    await pipeline_run1.run()

    # Check progress after Run 1
    prog1 = load_progress()
    completed_rows_1 = prog1.get("completed_rows", [])
    assert len(completed_rows_1) == 5
    assert set(completed_rows_1) == {2, 3, 4, 5, 6}

    # RUN 2: Process next 5 rows
    pipeline_run2 = ScraperPipeline(
        input_file=excel_file,
        output_file=output_file,
        sellers_file=sellers_file,
        max_rows_per_run=5,
        resume=True,
    )
    pipeline_run2.search_scraper.start = mock_start
    pipeline_run2.search_scraper.close = mock_close
    pipeline_run2.search_scraper.search_and_collect_product_urls = mock_search
    pipeline_run2.search_scraper.extract_seller_from_product_url = mock_extract_seller
    pipeline_run2.enrich_seller = mock_enrich

    await pipeline_run2.run()

    # Check progress after Run 2
    prog2 = load_progress()
    completed_rows_2 = prog2.get("completed_rows", [])
    assert len(completed_rows_2) == 10
    assert set(completed_rows_2) == {2, 3, 4, 5, 6, 7, 8, 9, 10, 11}


@pytest.mark.asyncio
async def test_no_pending_rows_clean_exit(tmp_path: Path, monkeypatch):
    """Verify that when all rows are completed, pipeline finishes cleanly with NO_PENDING_ROWS."""
    excel_file = create_test_excel(tmp_path / "input.xlsx", num_rows=3)
    output_file = tmp_path / "flipkart_sellers.xlsx"
    progress_file = tmp_path / "progress.json"
    sellers_file = tmp_path / "sellers.json"

    monkeypatch.setattr("scraper.excel_reader.PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scraper.config.PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scraper.config.SELLERS_FILE", sellers_file)
    monkeypatch.setattr("main.SELLERS_FILE", sellers_file)

    # Pre-populate progress as 100% completed
    save_progress({
        "completed_queries": [
            "Category_1 SubCategory_1 SubSub_1 Item_1",
            "Category_2 SubCategory_2 SubSub_2 Item_2",
            "Category_3 SubCategory_3 SubSub_3 Item_3",
        ],
        "completed_rows": [2, 3, 4],
        "completed_sellers": [],
        "last_updated": None,
    })

    pipeline = ScraperPipeline(
        input_file=excel_file,
        output_file=output_file,
        sellers_file=sellers_file,
        max_rows_per_run=2000,
        resume=True,
    )

    called = False
    async def mock_start():
        nonlocal called
        called = True

    pipeline.search_scraper.start = mock_start
    await pipeline.run()

    assert called is False


@pytest.mark.asyncio
async def test_partial_failure_leaves_uncompleted_row_pending(tmp_path: Path, monkeypatch):
    """Verify that if a row fails during scraping, it is not marked as completed and remains pending."""
    excel_file = create_test_excel(tmp_path / "input.xlsx", num_rows=3)
    output_file = tmp_path / "flipkart_sellers.xlsx"
    progress_file = tmp_path / "progress.json"
    sellers_file = tmp_path / "sellers.json"

    monkeypatch.setattr("scraper.excel_reader.PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scraper.config.PROGRESS_FILE", progress_file)
    monkeypatch.setattr("scraper.config.SELLERS_FILE", sellers_file)
    monkeypatch.setattr("main.SELLERS_FILE", sellers_file)

    async def mock_start():
        pass

    async def mock_close():
        pass

    # Mock search to fail specifically on Row 3 (the 2nd data row)
    async def mock_search(query: str, max_products: int = 20):
        if "Category_2" in query:
            raise ConnectionError("Network failure on Row 3")
        return [f"https://www.flipkart.com/{query.replace(' ', '-')}/p/itm123"]

    async def mock_extract_seller(p_url: str, input_row=None):
        return {
            "seller_name": f"Seller_For_{input_row.get('query', 'item')}",
            "fulfillment_by": "Flipkart",
        }

    async def mock_enrich(generic_record):
        return {**generic_record, "status": "VERIFIED"}

    pipeline = ScraperPipeline(
        input_file=excel_file,
        output_file=output_file,
        sellers_file=sellers_file,
        max_rows_per_run=5,
        resume=True,
    )
    pipeline.search_scraper.start = mock_start
    pipeline.search_scraper.close = mock_close
    pipeline.search_scraper.search_and_collect_product_urls = mock_search
    pipeline.search_scraper.extract_seller_from_product_url = mock_extract_seller
    pipeline.enrich_seller = mock_enrich

    await pipeline.run()

    prog = load_progress()
    completed_rows = prog.get("completed_rows", [])

    # Row 2 (Category_1) succeeded -> completed
    # Row 3 (Category_2) failed -> NOT completed
    # Row 4 (Category_3) succeeded -> completed
    assert 2 in completed_rows
    assert 3 not in completed_rows
    assert 4 in completed_rows
    assert len(completed_rows) == 2
