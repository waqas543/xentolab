"""Local headless-browser loader (Playwright). Optional: only for sites that need JS and when the hosted
load-page API is unavailable.

    pip install '.[js]' && playwright install chromium
"""

from __future__ import annotations

import time
from typing import Any

from app.core.exceptions import ConfigError, LoaderError, TransientLoaderError
from app.core.models import PageResponse
from app.loader.base import PageLoader, elapsed_ms


class JsLoader(PageLoader):
    name = "playwright"

    def __init__(self, *, user_agent: str, wait_until: str = "networkidle", **kwargs) -> None:
        super().__init__(**kwargs)
        self.user_agent = user_agent
        self.wait_until = wait_until
        self._playwright: Any = None
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise ConfigError("Playwright is not installed: pip install '.[js]' && playwright install chromium") \
                    from exc
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser

    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        browser = await self._ensure_browser()
        page = await browser.new_page(user_agent=self.user_agent, java_script_enabled=render_js)
        started = time.perf_counter()
        try:
            response = await page.goto(url, wait_until=self.wait_until, timeout=self.timeout * 1000)
            html = await page.content()
            status = response.status if response else 200
            final_url = page.url
        except PlaywrightTimeout as exc:
            raise TransientLoaderError(f"timeout: {exc}") from exc
        except PlaywrightError as exc:
            raise TransientLoaderError(f"browser error: {exc}") from exc
        finally:
            await page.close()
        if status in self.retry.retry_on_status:
            raise TransientLoaderError(f"HTTP {status}", status_code=status)
        if status >= 400:
            raise LoaderError(f"HTTP {status}", url=url, status_code=status)
        return PageResponse(url=url, final_url=final_url, status_code=status, html=html, content_type="text/html",
                            latency_ms=elapsed_ms(started), loader=self.name, render_js=render_js)

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = self._playwright = None
