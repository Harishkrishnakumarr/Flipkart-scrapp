"""Configuration settings and constants for the Flipkart Seller Scraper."""

from pathlib import Path
from typing import Dict, List

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
DEBUG_DIR = BASE_DIR / "debug"

INPUT_FILE = BASE_DIR / "input.xlsx"
SELLERS_FILE = DATA_DIR / "sellers.json"
CACHE_FILE = DATA_DIR / "cache.json"
PROGRESS_FILE = DATA_DIR / "progress.json"
OUTPUT_FILE = OUTPUT_DIR / "flipkart_sellers.xlsx"
LOG_FILE = LOGS_DIR / "scraper.log"

# Ensure runtime directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

# Excel Column Definitions
REQUIRED_INPUT_COLUMNS: List[str] = [
    "category",
    "sub_category",
    "sub_sub_category",
    "sub_sub_subcategory",
]

OUTPUT_EXCEL_COLUMNS: List[str] = [
    "seller_name",
    "fulfillment_by",
    "marketplace",
    "status",
    "business_model",
    "business_category",
    "owner_name",
    "contact_number",
    "email",
    "gst_number",
    "pan_number",
    "fssai_number",
    "billing_address",
    "shipping_address",
    "city",
    "state",
    "pincode",
    "country",
    "website_url",
    "star_rating",
    "seller_rating",
    "product_rating",
    "product_url",
    "category",
    "sub_category",
    "sub_sub_category",
    "sub_sub_subcategory",
    "seller_source_url",
    "seller_source_type",
    "source",
]

# Verification Statuses
STATUS_VERIFIED = "VERIFIED"
STATUS_PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_ENRICHMENT_PENDING = "ENRICHMENT_PENDING"

# Scraping Settings
FLIPKART_BASE_URL = "https://www.flipkart.com"
MAX_PAGES_PER_QUERY = 2
MAX_PRODUCTS_PER_CATEGORY = 20
DEFAULT_TIMEOUT_MS = 30000
HTTP_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
MIN_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 5.0

# User-Agent list for rotation and stealth headers
USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

DEFAULT_HEADERS: Dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Indian GST State Code Mapping (01 - 38)
GST_STATE_CODES: Dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman and Diu",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh (New)",
    "38": "Ladakh",
    "97": "Other Territory",
}

# Indian States & Union Territories with Aliases
INDIAN_STATES: Dict[str, List[str]] = {
    "Andhra Pradesh": ["andhra pradesh", "andhra", "ap"],
    "Arunachal Pradesh": ["arunachal pradesh", "arunachal"],
    "Assam": ["assam", "as"],
    "Bihar": ["bihar", "br"],
    "Chhattisgarh": ["chhattisgarh", "chattisgarh", "cg"],
    "Goa": ["goa", "ga"],
    "Gujarat": ["gujarat", "gj"],
    "Haryana": ["haryana", "hr"],
    "Himachal Pradesh": ["himachal pradesh", "himachal", "hp"],
    "Jharkhand": ["jharkhand", "jh"],
    "Karnataka": ["karnataka", "ka"],
    "Kerala": ["kerala", "kl"],
    "Madhya Pradesh": ["madhya pradesh", "mp"],
    "Maharashtra": ["maharashtra", "mh"],
    "Manipur": ["manipur", "mn"],
    "Meghalaya": ["meghalaya", "ml"],
    "Mizoram": ["mizoram", "mz"],
    "Nagaland": ["nagaland", "nl"],
    "Odisha": ["odisha", "orissa", "or", "od"],
    "Punjab": ["punjab", "pb"],
    "Rajasthan": ["rajasthan", "rj"],
    "Sikkim": ["sikkim", "sk"],
    "Tamil Nadu": ["tamil nadu", "tamilnadu", "tn", "madras"],
    "Telangana": ["telangana", "ts", "tg"],
    "Tripura": ["tripura", "tr"],
    "Uttar Pradesh": ["uttar pradesh", "up"],
    "Uttarakhand": ["uttarakhand", "uttaranchal", "uk"],
    "West Bengal": ["west bengal", "bengal", "wb", "paschim banga"],
    "Andaman and Nicobar Islands": ["andaman and nicobar", "andaman", "nicobar", "an"],
    "Chandigarh": ["chandigarh", "ch"],
    "Dadra and Nagar Haveli and Daman and Diu": [
        "dadra and nagar haveli",
        "daman and diu",
        "dadra",
        "daman",
        "diu",
        "dn",
        "dd",
    ],
    "Delhi": ["delhi", "new delhi", "nct of delhi", "dl"],
    "Jammu and Kashmir": ["jammu and kashmir", "jammu", "kashmir", "jk"],
    "Ladakh": ["ladakh", "la"],
    "Lakshadweep": ["lakshadweep", "ld"],
    "Puducherry": ["puducherry", "pondicherry", "py"],
}

# Major Indian Cities Mapping to State for Fallback State Resolution
MAJOR_INDIAN_CITIES: Dict[str, str] = {
    "mumbai": "Maharashtra",
    "pune": "Maharashtra",
    "nagpur": "Maharashtra",
    "thane": "Maharashtra",
    "nashik": "Maharashtra",
    "aurangabad": "Maharashtra",
    "navi mumbai": "Maharashtra",
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "noida": "Uttar Pradesh",
    "greater noida": "Uttar Pradesh",
    "ghaziabad": "Uttar Pradesh",
    "lucknow": "Uttar Pradesh",
    "kanpur": "Uttar Pradesh",
    "agra": "Uttar Pradesh",
    "varanasi": "Uttar Pradesh",
    "meerut": "Uttar Pradesh",
    "bengaluru": "Karnataka",
    "bangalore": "Karnataka",
    "mysuru": "Karnataka",
    "mysore": "Karnataka",
    "hubballi": "Karnataka",
    "mangalore": "Karnataka",
    "hyderabad": "Telangana",
    "secunderabad": "Telangana",
    "warangal": "Telangana",
    "chennai": "Tamil Nadu",
    "madras": "Tamil Nadu",
    "coimbatore": "Tamil Nadu",
    "madurai": "Tamil Nadu",
    "tiruchirappalli": "Tamil Nadu",
    "salem": "Tamil Nadu",
    "tiruppur": "Tamil Nadu",
    "kolkata": "West Bengal",
    "calcutta": "West Bengal",
    "howrah": "West Bengal",
    "siliguri": "West Bengal",
    "ahmedabad": "Gujarat",
    "surat": "Gujarat",
    "vadodara": "Gujarat",
    "rajkot": "Gujarat",
    "bhavnagar": "Gujarat",
    "jaipur": "Rajasthan",
    "jodhpur": "Rajasthan",
    "udaipur": "Rajasthan",
    "kota": "Rajasthan",
    "bikaner": "Rajasthan",
    "gurugram": "Haryana",
    "gurgaon": "Haryana",
    "faridabad": "Haryana",
    "panipat": "Haryana",
    "ambala": "Haryana",
    "indore": "Madhya Pradesh",
    "bhopal": "Madhya Pradesh",
    "gwalior": "Madhya Pradesh",
    "jabalpur": "Madhya Pradesh",
    "patna": "Bihar",
    "gaya": "Bihar",
    "muzaffarpur": "Bihar",
    "ranchi": "Jharkhand",
    "jamshedpur": "Jharkhand",
    "dhanbad": "Jharkhand",
    "bhubaneswar": "Odisha",
    "cuttack": "Odisha",
    "rourkela": "Odisha",
    "chandigarh": "Chandigarh",
    "ludhiana": "Punjab",
    "amritsar": "Punjab",
    "jalandhar": "Punjab",
    "dehradun": "Uttarakhand",
    "haridwar": "Uttarakhand",
    "roorkee": "Uttarakhand",
    "guwahati": "Assam",
    "silchar": "Assam",
    "thiruvananthapuram": "Kerala",
    "trivandrum": "Kerala",
    "kochi": "Kerala",
    "cochin": "Kerala",
    "kozhikode": "Kerala",
    "calicut": "Kerala",
    "thrissur": "Kerala",
    "visakhapatnam": "Andhra Pradesh",
    "vijayawada": "Andhra Pradesh",
    "guntur": "Andhra Pradesh",
    "raipur": "Chhattisgarh",
    "bilaspur": "Chhattisgarh",
    "srinagar": "Jammu and Kashmir",
    "jammu": "Jammu and Kashmir",
    "panaji": "Goa",
    "margao": "Goa",
    "shimla": "Himachal Pradesh",
}
