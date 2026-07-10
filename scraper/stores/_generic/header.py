"""
Generic header-manipulation flow. Same JSON extraction as `GenericAPIStore`
but injects the caller-supplied headers and cookies on every request. Use
this for sites that gate their JSON endpoints behind a `User-Agent`,
`Accept-Language`, or a session cookie you can grab from a browser DevTools
Network tab.

For sites protected by Cloudflare bot management (Trendyol-class), header
tweaks aren't enough — you need a warmed browser context. Those get their
own dedicated store module under `scraper/stores/<name>/`.
"""
from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

import httpx

from .api import GenericAPIStore

logger = logging.getLogger("scraper.generic.header")


class GenericHeaderStore(GenericAPIStore):
    flow_type: ClassVar[str] = "header"

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> Any:
        method = (self._config.get("method") or "GET").upper()
        headers = self._config.get("headers") or {}
        cookies = self._config.get("cookies") or {}

        resp = await client.request(
            method, url, headers=headers, cookies=cookies,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"non-JSON response from {url}: {e}") from e
