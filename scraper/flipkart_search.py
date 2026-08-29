"""Flipkart search automation and product extraction module using Playwright."""

import asyncio
import logging
import random
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from scraper.config import (
    DEBUG_DIR,
    DEFAULT_HEADERS,
    FLIPKART_BASE_URL,
    MAX_DELAY_SECONDS,
    MAX_PAGES_PER_QUERY,
    MAX_PRODUCTS_PER_CATEGORY,
    MAX_RETRIES,
    MIN_DELAY_SECONDS,
    USER_AGENTS,
)
from scraper.product_parser import parse_product_page

logger = logging.getLogger("FlipkartScraper.Search")

# Non-product URL keywords to reject
INVALID_URL_KEYWORDS = [
    "login",
    "account",
    "cart",
    "seller",
    "sell-online",
    "help",
    "search",
    "wishlist",
    "signup",
    "viewcart",
    "checkout",
    "myntra",
    "24x7",
]


def is_valid_flipkart_product_url(url: str) -> bool:
    """Validate that a URL represents a genuine Flipkart product page.

    Rejects login, account, cart, seller portal, search, and help URLs.

    Args:
        url: URL string to inspect.

    Returns:
        True if valid product URL, False otherwise.
    """
    if not url or not isinstance(url, str):
        return False

    url_lower = url.lower().strip()
    if not url_lower.startswith("http"):
        return False

    # Check for disallowed keywords in URL path
    for kw in INVALID_URL_KEYWORDS:
        if f"/{kw}" in url_lower or f"?{kw}" in url_lower or f"={kw}" in url_lower:
            return False

    # Standard Flipkart product URL must have /p/ or product slug
    if "/p/" in url_lower or re.search(r"flipkart\.com/[a-z0-9\-]+/p/", url_lower):
        return True

    return True


def clean_flipkart_product_url(raw_url: str) -> str:
    """Normalize and clean Flipkart product URL, stripping tracking query params.

    Args:
        raw_url: Raw product URL or relative href.

    Returns:
        Clean canonical product URL.
    """
    if not raw_url:
        return ""

    full_url = urllib.parse.urljoin(FLIPKART_BASE_URL, raw_url)
    parsed = urllib.parse.urlparse(full_url)

    # Match canonical product URL structure (e.g. /product-title/p/itme...)
    p_match = re.search(r"(/[a-zA-Z0-9\-]+/p/[a-zA-Z0-9]+)", parsed.path)
    if p_match:
        query_params = urllib.parse.parse_qs(parsed.query)
        pid = query_params.get("pid", [None])[0]
        if pid:
            return f"{FLIPKART_BASE_URL}{p_match.group(1)}?pid={pid}"
        return f"{FLIPKART_BASE_URL}{p_match.group(1)}"

    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


class FlipkartSearchScraper:
    """Automates Flipkart search and product extraction using Playwright."""

    def __init__(self, headless: bool = True) -> None:
        """Initialize the Playwright search scraper.

        Args:
            headless: Whether to run Playwright in headless mode.
        """
        self.headless = headless
        self.playwright: Optional[Any] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def _ensure_connected(self) -> None:
        """Ensure Playwright browser and context are active and connected."""
        if (
            not self.playwright
            or not self.browser
            or not self.browser.is_connected()
            or not self.context
        ):
            await self.start()

    async def start(self) -> None:
        """Launch the Playwright browser with stealth settings."""
        await self.stop()

        if not self.playwright:
            self.playwright = await async_playwright().start()

        user_agent = random.choice(USER_AGENTS)
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
                "--ignore-certificate-errors-spki-list",
            ],
        )

        self.context = await self.browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers=DEFAULT_HEADERS,
        )

        # Inject evasion script to mask webdriver
        await self.context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            """
        )
        logger.info("Playwright browser instance initialized successfully.")

    async def stop(self) -> None:
        """Close browser context and stop Playwright safely."""
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass

        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass

        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass

        self.context = None
        self.browser = None
        self.playwright = None

    async def _random_delay(self) -> None:
        """Sleep for a random humanized interval."""
        delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
        await asyncio.sleep(delay)

    async def search_and_collect_product_urls(
        self, query: str, max_products: int = MAX_PRODUCTS_PER_CATEGORY
    ) -> List[str]:
        """Search Flipkart for a query and extract unique product URLs across pages.

        Args:
            query: Category hierarchy search query string.
            max_products: Maximum number of product URLs to collect.

        Returns:
            List of unique, clean Flipkart product URLs.
        """
        await self._ensure_connected()

        encoded_query = urllib.parse.quote_plus(query)
        collected_urls: Set[str] = set()
        page_num = 1

        while len(collected_urls) < max_products and page_num <= MAX_PAGES_PER_QUERY:
            search_url = f"{FLIPKART_BASE_URL}/search?q={encoded_query}&page={page_num}"
            logger.info(f"Searching Flipkart (Page {page_num}): {search_url}")

            page: Optional[Page] = None
            attempts = 0
            success = False

            while attempts < MAX_RETRIES and not success:
                attempts += 1
                try:
                    await self._ensure_connected()
                    page = await self.context.new_page()
                    await page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    await self._random_delay()

                    # Scroll down to trigger lazy loading of product cards
                    await page.evaluate("window.scrollBy(0, window.innerHeight * 2);")
                    await asyncio.sleep(1.0)

                    # Extract all product links matching Flipkart product URL patterns
                    links = await page.eval_on_selector_all(
                        "a[href*='/p/']",
                        """(elements) => elements.map(el => {
                            const isAd = el.closest('div[class*="ad"], div[class*="Ad"], div[class*="_2rpwqI"]') !== null 
                                         || el.innerText.includes('Sponsored') 
                                         || el.innerText.includes('Ad');
                            return { href: el.getAttribute('href'), isAd: isAd };
                        })""",
                    )

                    new_count = 0
                    for item in links:
                        href = item.get("href", "")
                        is_ad = item.get("isAd", False)
                        if is_ad and len(collected_urls) > 0:
                            continue

                        clean_url = clean_flipkart_product_url(href)
                        if (
                            clean_url
                            and is_valid_flipkart_product_url(clean_url)
                            and clean_url not in collected_urls
                        ):
                            collected_urls.add(clean_url)
                            new_count += 1
                            if len(collected_urls) >= max_products:
                                break

                    logger.info(
                        f"Page {page_num}: Found {new_count} new product URLs. "
                        f"Total unique collected: {len(collected_urls)}/{max_products}"
                    )
                    success = True

                except Exception as e:
                    logger.warning(
                        f"Attempt {attempts}/{MAX_RETRIES} failed for search URL {search_url}: {e}"
                    )
                    await asyncio.sleep(2.0 * attempts)
                finally:
                    if page:
                        try:
                            await page.close()
                        except Exception:
                            pass

            if not success or new_count == 0:
                logger.info(f"No more products found for query: {query}")
                break

            page_num += 1

        return list(collected_urls)[:max_products]

    async def extract_seller_from_product_url(
        self, product_url: str, input_row: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Navigate to a product page and extract generic seller record.

        Args:
            product_url: Direct URL to the Flipkart product.
            input_row: Optional context from input row.

        Returns:
            Generic seller record dict.
        """
        await self._ensure_connected()

        page: Optional[Page] = None
        attempts = 0
        product_id = product_url.split("/p/")[-1].split("?")[0] if "/p/" in product_url else "unknown"

        while attempts < MAX_RETRIES:
            attempts += 1
            try:
                await self._ensure_connected()
                page = await self.context.new_page()
                response = await page.goto(
                    product_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await self._random_delay()

                http_status = response.status if response else 200
                final_url = page.url

                # Check if page was redirected away from a product page
                if final_url and "/p/" not in final_url and "/p/" in product_url:
                    logger.warning(f"Product page redirected from {product_url} to {final_url}")
                    return {
                        "marketplace": "flipkart",
                        "seller_name": "",
                        "fulfillment_by": None,
                        "product_url": product_url,
                        "seller_source_url": product_url,
                        "seller_source_type": "flipkart_product",
                        "star_rating": None,
                        "product_rating": None,
                        "seller_confidence": 0.0,
                        "extraction_status": "REDIRECTED",
                    }

                # Scroll down slightly to trigger seller widget rendering
                await page.evaluate("window.scrollBy(0, 500);")
                await asyncio.sleep(0.5)

                html_content = await page.content()
                parsed_data = parse_product_page(html_content, page_url=product_url, http_status=http_status)

                seller_name = parsed_data.get("seller_name", "")
                fulfillment_by = parsed_data.get("fulfilled_by_seller") or parsed_data.get("fulfillment_by")
                star_rating = parsed_data.get("star_rating")
                product_rating = parsed_data.get("product_rating")
                seller_confidence = parsed_data.get("seller_confidence", 0.95)
                seller_source_type = parsed_data.get("seller_source") or "flipkart_product"
                page_status = parsed_data.get("page_status", "PRODUCT_PAGE")

                # If seller extraction failed on a valid product page, capture screenshot for debugging
                if not seller_name and page_status == "PRODUCT_PAGE":
                    try:
                        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                        sanitized_id = re.sub(r"[^\w\-]", "_", product_id)[:50]
                        screenshot_path = DEBUG_DIR / f"product_{sanitized_id}.png"
                        await page.screenshot(path=str(screenshot_path), full_page=True)
                        logger.debug(f"Saved debug screenshot to {screenshot_path}")
                    except Exception as ss_err:
                        logger.debug(f"Failed to capture debug screenshot: {ss_err}")

                # Build generic seller record
                cat_hierarchy = input_row.get("category_hierarchy", []) if input_row else []
                category = cat_hierarchy[0] if len(cat_hierarchy) > 0 else None
                sub_cat = cat_hierarchy[1] if len(cat_hierarchy) > 1 else None
                sub_sub_cat = cat_hierarchy[2] if len(cat_hierarchy) > 2 else None
                sub_sub_sub_cat = cat_hierarchy[3] if len(cat_hierarchy) > 3 else None

                return {
                    "marketplace": "flipkart",
                    "seller_name": seller_name,
                    "fulfillment_by": fulfillment_by,
                    "fulfilled_by_seller": fulfillment_by,
                    "seller_values_found": parsed_data.get("seller_values_found", []),
                    "product_url": product_url,
                    "seller_source_url": product_url,
                    "seller_source_type": seller_source_type,
                    "category": category,
                    "sub_category": sub_cat,
                    "sub_sub_category": sub_sub_cat,
                    "sub_sub_subcategory": sub_sub_sub_cat,
                    "product_rating": product_rating,
                    "seller_rating": star_rating,
                    "star_rating": star_rating,
                    "seller_confidence": seller_confidence,
                    "rating_confidence": parsed_data.get("rating_confidence", 0.0),
                    "extraction_status": page_status,
                }

            except Exception as e:
                logger.warning(
                    f"Attempt {attempts}/{MAX_RETRIES} failed fetching product {product_url}: {e}"
                )
                await asyncio.sleep(2.0 * attempts)
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

        return {
            "marketplace": "flipkart",
            "seller_name": "",
            "fulfillment_by": None,
            "product_url": product_url,
            "seller_source_url": product_url,
            "seller_source_type": "flipkart_product",
            "star_rating": None,
            "product_rating": None,
            "seller_confidence": 0.0,
            "extraction_status": "REQUEST_FAILED",
        }
