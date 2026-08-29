"""Seller extraction, normalization, deduplication, and repository management."""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scraper.config import SELLERS_FILE
from scraper.product_parser import is_valid_seller_name

logger = logging.getLogger("FlipkartScraper.SellerExtractor")


def seller_key(name: str) -> str:
    """Generate canonical key for seller deduplication.

    Examples:
      'ABC ENTERPRISES' -> 'abc enterprises'
      'A & B Retail Pvt. Ltd.' -> 'a and b retail pvt ltd'

    Args:
        name: Seller name string.

    Returns:
        Canonical key string.
    """
    if not name:
        return ""
    val = str(name).lower().strip()
    val = val.replace("&", "and")
    val = re.sub(r"[^\w\s]", "", val)
    val = " ".join(val.split())
    return val


def normalize_seller_name(name: Optional[str]) -> Tuple[str, str]:
    """Normalize seller name for consistent deduplication and display.

    Args:
        name: Raw seller name extracted from page.

    Returns:
        Tuple of (canonical_key, display_name)
    """
    if not is_valid_seller_name(name):
        return "", ""

    raw = str(name).strip()
    cleaned = re.sub(r"^[\W_]+|[\W_]+$", "", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    display_name = cleaned.title()
    canonical = seller_key(cleaned)
    return canonical, display_name


class SellerRepository:
    """Manages unique sellers in data/sellers.json with deduplication, marketplace tracking, and persistence."""

    def __init__(self, file_path: Path = SELLERS_FILE) -> None:
        """Initialize the seller repository.

        Args:
            file_path: Path to the sellers.json storage file.
        """
        self.file_path = file_path
        self.sellers: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        """Load sellers from JSON storage and purge any invalid entries."""
        if not self.file_path.exists():
            self.sellers = {}
            return

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Filter out any legacy invalid seller names
            self.sellers = {
                k: v for k, v in loaded.items()
                if is_valid_seller_name(v.get("seller_name"))
            }
            logger.info(f"Loaded {len(self.sellers)} unique sellers from {self.file_path}")
        except Exception as e:
            logger.warning(f"Failed to load sellers from {self.file_path}: {e}")
            self.sellers = {}

    def save(self) -> None:
        """Save sellers to JSON storage."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.sellers, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved {len(self.sellers)} sellers to {self.file_path}")
        except Exception as e:
            logger.error(f"Error saving sellers to {self.file_path}: {e}")

    def add_or_update_seller(
        self,
        raw_seller_name: str,
        product_url: str,
        category_hierarchy: Optional[List[str]] = None,
        star_rating: Optional[float] = None,
        fulfillment_by: Optional[str] = None,
        marketplace: str = "flipkart",
        product_rating: Optional[float] = None,
        seller_source_type: Optional[str] = None,
        seller_confidence: float = 0.95,
    ) -> Optional[Dict[str, Any]]:
        """Add a new generic seller or update existing seller's products and categories.

        Args:
            raw_seller_name: Raw seller name.
            product_url: Marketplace product URL.
            category_hierarchy: Category list from input row.
            star_rating: Seller star rating if available.
            fulfillment_by: Fulfillment entity if present.
            marketplace: 'flipkart' | 'amazon'
            product_rating: Product rating if available.
            seller_source_type: Extraction source type.
            seller_confidence: Confidence score.

        Returns:
            The normalized generic seller record dictionary or None if invalid.
        """
        if not is_valid_seller_name(raw_seller_name):
            logger.debug(f"Rejected invalid seller in repository: '{raw_seller_name}'")
            return None

        canonical_key, display_name = normalize_seller_name(raw_seller_name)
        if not canonical_key:
            return None

        storage_key = f"{marketplace}::{canonical_key}"
        category_str = " > ".join(category_hierarchy) if category_hierarchy else "General"
        now_iso = datetime.now(timezone.utc).isoformat()

        if storage_key in self.sellers:
            seller = self.sellers[storage_key]
            if product_url and product_url not in seller["product_urls"]:
                seller["product_urls"].append(product_url)
            if category_str and category_str not in seller["categories"]:
                seller["categories"].append(category_str)
            if star_rating is not None and (
                seller.get("star_rating") is None or star_rating > seller["star_rating"]
            ):
                seller["star_rating"] = star_rating
            if fulfillment_by and not seller.get("fulfillment_by"):
                seller["fulfillment_by"] = fulfillment_by
            seller["last_seen"] = now_iso
        else:
            self.sellers[storage_key] = {
                "seller_name": display_name,
                "canonical_id": canonical_key,
                "marketplace": marketplace,
                "fulfillment_by": fulfillment_by,
                "product_urls": [product_url] if product_url else [],
                "categories": [category_str] if category_str else [],
                "star_rating": star_rating,
                "product_rating": product_rating,
                "seller_source_type": seller_source_type or f"{marketplace}_product",
                "seller_confidence": seller_confidence,
                "enrichment_status": "pending",
                "first_seen": now_iso,
                "last_seen": now_iso,
            }

        self.save()
        return self.sellers[storage_key]

    def mark_enriched(self, storage_key: str, enriched_data: Dict[str, Any]) -> None:
        """Mark seller as enriched and store timestamp."""
        if storage_key in self.sellers:
            self.sellers[storage_key]["enrichment_status"] = "completed"
            self.sellers[storage_key]["last_enriched"] = datetime.now(timezone.utc).isoformat()
            self.sellers[storage_key]["enriched_data"] = enriched_data
            self.save()

    def get_all_sellers(self) -> List[Dict[str, Any]]:
        """Get list of all seller dictionaries in repository."""
        return list(self.sellers.values())

    def get_all_pending_sellers(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Get all sellers with pending enrichment."""
        return [
            (k, v) for k, v in self.sellers.items()
            if v.get("enrichment_status") != "completed"
        ]
