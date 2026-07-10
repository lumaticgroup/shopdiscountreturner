from dataclasses import dataclass
from typing import Optional


@dataclass
class ScrapedProduct:
    product_id: str
    storefront: str
    name: str
    url: str
    brand: Optional[str] = None
    image_url: Optional[str] = None
    price: Optional[float] = None
    original_price: Optional[float] = None
    discount_pct: Optional[int] = None
    currency: str = ""
    category_id: Optional[int] = None
    is_outlet: bool = False


@dataclass
class ScrapedCategory:
    """Node of Trendyol's category tree during scrape (pre-DB insert)."""
    storefront: str
    name: str
    slug: str
    breadcrumb: str
    depth: int
    parent_breadcrumb: Optional[str] = None
    listing_path: Optional[str] = None
