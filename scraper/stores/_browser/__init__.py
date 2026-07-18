"""
Private shared package: store-agnostic Playwright session.

Not a store — the leading `_` keeps registry discovery away (see
`scraper.stores._discover`). Store modules that need a warmed browser
context (Trendyol, Shein — the "Cloudflare/bot-managed" flow
class) build on `BrowserSession` from `.session`.
"""
from .session import BrowserSession, ChromiumDied, EdgeBlocked

__all__ = ["BrowserSession", "ChromiumDied", "EdgeBlocked"]
