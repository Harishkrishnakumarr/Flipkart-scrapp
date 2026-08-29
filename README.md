# Flipkart Seller Scraper & Public Web Research Engine

A production-ready, modular Python application designed to scrape Flipkart category hierarchies, extract sellers from product pages, enrich seller profiles using autonomous public web research, validate Indian business and tax identifiers (GSTIN, PAN, FSSAI, Phone, Email, Address), calculate confidence scores, and export structured Excel files.

---

## 🌟 Key Features

- **100% Local & Python-Only**: No external database (PostgreSQL/MySQL), Docker, or third-party paid API keys required.
- **Resilient Multi-Strategy Parsing**: Employs JSON-LD schema parsing, React initial state deserialization, modern CSS selectors, and regex heuristics to extract seller details reliably.
- **Autonomous Multi-Query Web Research**: Automatically searches public web sources for each unique seller across 9 progressive vectors (`GST`, `GSTIN`, `owner`, `contact`, `email`, `address`, `website`, `FSSAI`, `PAN`).
- **Indian Business Format Validation**: Strict validation against official formats for 15-character GSTIN (with state code lookup 01–38), 10-character PAN (with holder entity verification and GSTIN cross-checking), 14-digit FSSAI, 10-digit Indian mobile, and 6-digit Pincodes.
- **Address Decomposition**: Parses raw messy addresses into structured components (`city`, `state`, `pincode`, `country`), mapped across all 36 Indian States and Union Territories.
- **Confidence Scoring & Status**: Scores each attribute (0.0 to 1.0) and assigns an overall status (`VERIFIED`, `PARTIALLY_VERIFIED`, `NEEDS_REVIEW`, `NOT_FOUND`).
- **Graceful Progress & Resume**: Automatically checkpoints progress to `data/progress.json` and deduplicates sellers in `data/sellers.json`, allowing safe interruption (`Ctrl+C`) and instant resumption.
- **Styled Excel Export**: Formats `output/flipkart_sellers.xlsx` with frozen headers, autofit column widths, alternating fills, and color-coded status pills.

---

## 📂 Project Structure

```
Flipkart/
│
├── main.py                     # CLI Entry Point & Pipeline Orchestrator
├── input.xlsx                  # Category Hierarchy Input File
├── requirements.txt            # Python Dependencies
├── README.md                   # Documentation
│
├── scraper/
│   ├── __init__.py
│   ├── config.py               # Central Constants, State Codes, Aliases & Regex
│   ├── excel_reader.py         # Category Reader, Validator & Query Generator
│   ├── flipkart_search.py      # Playwright Flipkart Search & URL Extractor
│   ├── product_parser.py       # Multi-Strategy Seller & Rating Parser
│   ├── seller_extractor.py     # Seller Deduplication & Repository
│   ├── web_research.py         # Autonomous Public Web Research Engine
│   ├── website_parser.py       # Company Website Crawler (Contact/About/Footer)
│   ├── validator.py            # Indian Tax & Contact Validator + Scoring
│   ├── address_parser.py       # Indian Address Normalizer (36 States/UTs)
│   └── exporter.py             # Formatted Excel Exporter (openpyxl)
│
├── data/
│   ├── sellers.json            # Deduplicated Sellers & Metadata
│   ├── cache.json              # Local Web & Search HTTP Cache
│   └── progress.json           # Progress Checkpoints & Resumption Tracker
│
├── output/
│   └── flipkart_sellers.xlsx   # Final Structured Excel Output
│
├── logs/
│   └── scraper.log             # Rotating Detailed Execution Logs
│
└── tests/                      # Pytest Test Suite
    ├── test_address_parser.py
    ├── test_excel_reader.py
    ├── test_exporter.py
    ├── test_product_parser.py
    ├── test_seller_extractor.py
    ├── test_validator.py
    └── test_web_research.py
```

---

## 🚀 Installation & Setup

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.13)
- Windows / macOS / Linux

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Install Playwright Chromium Browser
```bash
playwright install chromium
```

---

## 📊 Input Excel Specification (`input.xlsx`)

The input Excel file must contain a header row with the following 4 columns:
| category | sub_category | sub_sub_category | sub_sub_subcategory |
| :--- | :--- | :--- | :--- |
| Electronics | Mobiles | Smartphones | Android Phones |
| Electronics | Audio | Headphones | Wireless Earbuds |
| Home & Kitchen | Kitchen Appliances | Small Appliances | Air Fryers |

To generate or reset the sample `input.xlsx`:
```bash
python create_sample_input.py
```

---

## 💻 Running the Application

### Full End-to-End Run
Run the full scraper pipeline reading `input.xlsx` and exporting to `output/flipkart_sellers.xlsx`:
```bash
python main.py
```

### Command-Line Arguments & Options

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--input <path>` | Path to custom input Excel file | `input.xlsx` |
| `--output <path>` | Target path for output Excel report | `output/flipkart_sellers.xlsx` |
| `--max-products <N>` | Maximum product URLs to extract per category | `20` |
| `--headless / --no-headless` | Run Playwright in headless vs visible browser mode | `--headless` |
| `--resume / --no-resume` | Resume progress from previous execution | `--resume` |
| `--research-only` | Skip Flipkart search, research existing sellers in `sellers.json` | `False` |
| `--export-only` | Re-generate Excel from existing cached seller records | `False` |
| `--verbose` | Print detailed DEBUG messages to console | `False` |

### Example CLI Usages:

1. **Quick Test (Headless, 5 products per query)**:
   ```bash
   python main.py --max-products 5
   ```

2. **Visible Browser for Debugging**:
   ```bash
   python main.py --no-headless --verbose
   ```

3. **Re-export Existing Data to Excel**:
   ```bash
   python main.py --export-only
   ```

---

## 📑 Output Excel Specification (`output/flipkart_sellers.xlsx`)

The output Excel contains all 18 structured columns:
1. `business_model` *(e.g., Proprietorship / Registered Business, Private Limited)*
2. `business_category` *(e.g., Electronics > Mobiles > Smartphones)*
3. `owner_name` *(e.g., Rajesh Kumar)*
4. `contact_number` *(Validated 10-digit Indian Mobile)*
5. `email` *(Validated RFC-compliant email)*
6. `gst_number` *(Validated 15-char Indian GSTIN)*
7. `pan_number` *(Validated 10-char PAN, verified with GST state/entity)*
8. `fssai_number` *(14-digit FSSAI License if applicable)*
9. `billing_address` *(Cleaned address string)*
10. `shipping_address` *(Shipping address)*
11. `city` *(Extracted Indian city)*
12. `state` *(Normalized Indian State / UT)*
13. `pincode` *(6-digit Indian Postal Code)*
14. `country` *(India)*
15. `website_url` *(Official company website)*
16. `status` *(VERIFIED / PARTIALLY_VERIFIED / NEEDS_REVIEW / NOT_FOUND)*
17. `source` *(Data extraction provenance: website, gst_portal, search_snippet)*
18. `star_rating` *(Flipkart seller rating, e.g., 4.5)*

---

## 🧪 Running Tests

Run the complete pytest test suite:
```bash
python -m pytest tests/ -v
```
