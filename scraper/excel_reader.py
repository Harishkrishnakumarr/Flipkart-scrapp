"""Adaptive Excel and Google Sheets reader module for Flipkart and marketplace input sources."""

import csv
import io
import json
import logging
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
import openpyxl

from scraper.config import PROGRESS_FILE

logger = logging.getLogger("FlipkartScraper.ExcelReader")


def load_progress() -> Dict[str, Any]:
    """Load existing scraping progress from data/progress.json.

    Returns:
        Progress dictionary containing completed queries, processed rows, and timestamps.
    """
    if not PROGRESS_FILE.exists():
        return {
            "completed_queries": [],
            "completed_rows": [],
            "completed_sellers": [],
            "last_updated": None,
        }

    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read progress file, starting fresh: {e}")
        return {
            "completed_queries": [],
            "completed_rows": [],
            "completed_sellers": [],
            "last_updated": None,
        }


def save_progress(progress_data: Dict[str, Any]) -> None:
    """Persist current scraping progress to data/progress.json.

    Args:
        progress_data: Dictionary tracking scraper state.
    """
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress_data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving progress to {PROGRESS_FILE}: {e}")


def normalize_column_name(col_name: Any) -> str:
    """Normalize column name for flexible matching.

    Args:
        col_name: Raw column header value.

    Returns:
        Cleaned lowercase identifier.
    """
    if not col_name:
        return ""
    return str(col_name).strip().lower().replace(" ", "_").replace("-", "_")


def _parse_tabular_rows_into_tasks(
    header_row: List[Any],
    data_rows: List[List[Any]],
    source_name: str,
    resume: bool = True,
) -> List[Dict[str, Any]]:
    """Parse raw table rows (from Excel or Google Sheets) into scraper tasks.

    Args:
        header_row: List of column header names.
        data_rows: List of data rows (each row is a list of cell values).
        source_name: Descriptive name/URL of the input source.
        resume: If True, skips rows/queries already present in progress.json.

    Returns:
        List of task dicts.
    """
    if not header_row or all(v is None or str(v).strip() == "" for v in header_row):
        raise ValueError(f"Excel file has an empty header row.")

    header_map: Dict[str, int] = {}
    for idx, col_name in enumerate(header_row):
        if col_name is not None:
            clean_name = normalize_column_name(col_name)
            if clean_name:
                header_map[clean_name] = idx

    progress = load_progress() if resume else {"completed_queries": [], "completed_rows": []}
    completed_queries: Set[str] = set(progress.get("completed_queries", []))
    completed_rows: Set[int] = set(progress.get("completed_rows", []))

    query_tasks: List[Dict[str, Any]] = []

    # Category field aliases
    cat_keys = [
        ["category", "cat", "main_category"],
        ["sub_category", "subcategory", "sub_cat"],
        ["sub_sub_category", "subsubcategory", "sub_sub_cat"],
        ["sub_sub_sub_category", "sub_sub_subcategory", "subsubsubcategory", "sub_sub_sub_cat", "sub_category_4", "sub_subcategory_4"],
    ]

    # Product URL aliases
    url_keys = ["product_url", "flipkart_product_url", "url", "product_link", "link", "google_sheet_url"]

    # Search query / Product name aliases
    query_keys = ["search_query", "query", "search_term", "keyword", "search"]
    name_keys = ["product_name", "product", "title", "name", "item_name"]

    for row_idx, row in enumerate(data_rows, start=2):
        if resume and row_idx in completed_rows:
            continue

        # Helper to safely retrieve row cell by index
        def get_val(idx: int) -> Optional[str]:
            if idx < len(row) and row[idx] is not None:
                val_str = str(row[idx]).strip()
                return val_str if val_str else None
            return None

        # 1. Check for direct Product URL
        direct_url = None
        for k in url_keys:
            if k in header_map:
                val = get_val(header_map[k])
                if val and val.startswith("http"):
                    direct_url = val
                    break

        # 2. Check for explicit search query or product name
        explicit_query = None
        for k in query_keys:
            if k in header_map:
                val = get_val(header_map[k])
                if val:
                    explicit_query = val
                    break

        product_name = None
        for k in name_keys:
            if k in header_map:
                val = get_val(header_map[k])
                if val:
                    product_name = val
                    break

        # 3. Extract category hierarchy components
        components: List[str] = []
        cat_dict: Dict[str, str] = {}

        for tier_idx, aliases in enumerate(cat_keys, start=1):
            tier_key = f"category_level_{tier_idx}" if tier_idx > 1 else "category"
            for alias in aliases:
                if alias in header_map:
                    val = get_val(header_map[alias])
                    if val:
                        components.append(val)
                        cat_dict[alias] = val
                        break

        # If row is completely empty, skip it
        if not direct_url and not explicit_query and not product_name and not components:
            continue

        # Build search query if URL not given
        search_query = explicit_query or product_name or (" ".join(components) if components else None)

        if resume and search_query and search_query in completed_queries:
            continue

        query_tasks.append(
            {
                "row_index": row_idx,
                "product_url": direct_url,
                "query": search_query,
                "product_name": product_name,
                "category_hierarchy": components,
                "category_dict": cat_dict,
            }
        )

    logger.info(
        f"Successfully loaded {len(query_tasks)} tasks from {source_name} "
        f"(skipped {len(completed_queries)} previously completed queries)."
    )
    return query_tasks


def read_categories_from_excel(
    file_path: Path, resume: bool = True
) -> List[Dict[str, Any]]:
    """Adaptively read Flipkart input from Excel (.xlsx) file.

    Args:
        file_path: Path to the input Excel (.xlsx) file.
        resume: If True, skips rows/queries already present in progress.json.

    Returns:
        List of task dicts.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found at: {file_path}")

    logger.info(f"Loading input data from {file_path}")
    workbook = openpyxl.load_workbook(filename=file_path, data_only=True)
    sheet = workbook.active

    if sheet is None:
        raise ValueError("Excel file contains no active sheet.")

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise ValueError("Excel file has an empty header row.")

    header_row = list(rows[0])
    if not header_row or all(v is None for v in header_row):
        raise ValueError("Excel file has an empty header row.")

    data_rows = [list(r) for r in rows[1:]]

    return _parse_tabular_rows_into_tasks(
        header_row=header_row,
        data_rows=data_rows,
        source_name=str(file_path),
        resume=resume,
    )


def extract_google_sheet_details(sheet_url_or_id: str) -> Dict[str, str]:
    """Extract Spreadsheet ID and GID from a Google Sheets URL or ID string.

    Args:
        sheet_url_or_id: Full Google Sheets URL or raw sheet ID.

    Returns:
        Dict containing 'spreadsheet_id' and 'gid'.
    """
    url_str = str(sheet_url_or_id).strip()

    # Match /spreadsheets/d/<ID>
    id_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_str)
    spreadsheet_id = id_match.group(1) if id_match else url_str

    # Match gid=<GID>
    gid_match = re.search(r"[?&#]gid=([0-9]+)", url_str)
    gid = gid_match.group(1) if gid_match else "0"

    return {
        "spreadsheet_id": spreadsheet_id,
        "gid": gid,
    }


def read_categories_from_google_sheet(
    sheet_url_or_id: str, resume: bool = True
) -> List[Dict[str, Any]]:
    """Read product categories and queries directly from a Google Sheet.

    Supports public sheets via direct CSV export endpoint.

    Args:
        sheet_url_or_id: Google Sheets URL or Spreadsheet ID.
        resume: If True, skips rows/queries already present in progress.json.

    Returns:
        List of task dicts.
    """
    details = extract_google_sheet_details(sheet_url_or_id)
    spreadsheet_id = details["spreadsheet_id"]
    gid = details["gid"]

    logger.info(f"Loading input data from Google Sheet (ID: {spreadsheet_id}, GID: {gid})")

    export_url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"
    )

    csv_text = None
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            resp = client.get(export_url)
            resp.raise_for_status()
            csv_text = resp.text
    except Exception as e:
        logger.warning(f"httpx failed to fetch Google Sheet ({e}), attempting urllib fallback...")
        try:
            req = urllib.request.Request(
                export_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                csv_text = response.read().decode("utf-8")
        except Exception as url_err:
            raise RuntimeError(
                f"Failed to fetch Google Sheet from {export_url}. "
                f"Please ensure the sheet is accessible or publicly shared: {url_err}"
            ) from url_err

    if not csv_text or not csv_text.strip():
        raise ValueError(f"Google Sheet at {sheet_url_or_id} returned empty content.")

    reader = csv.reader(io.StringIO(csv_text))
    all_rows = list(reader)

    if not all_rows:
        raise ValueError("Google Sheet contains no data rows.")

    header_row = all_rows[0]
    data_rows = all_rows[1:]

    return _parse_tabular_rows_into_tasks(
        header_row=header_row,
        data_rows=data_rows,
        source_name=f"Google Sheet ({spreadsheet_id}, gid={gid})",
        resume=resume,
    )
