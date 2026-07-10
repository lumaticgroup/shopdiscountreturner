"""
Generic scraper implementations for stores declared at runtime (via `.env`
or the admin Mini App).

This package intentionally starts with an underscore so the registry
auto-discovery in `scraper/stores/__init__.py` skips it. The generic
stores are instantiated dynamically from `dynamic_stores` DB rows by
`scraper.stores.refresh_dynamic_stores()`, not statically imported.
"""
