"""Google search: result + AI Overview parsing, /goto link resolution, the scrape API loader, and engine
fallback / cooldown (mocked HTTP, no network)."""

from __future__ import annotations

import asyncio

import httpx
import orjson
import pytest
from conftest import fixture_html, make_company, make_page

from app.core.config import JobConfig, LoaderOptions, SearchEngineConfig, load_extraction_rules
from app.core.exceptions import BlockedError, ConfigError, LoaderError, ParseError
from app.core.models import PageResponse
from app.enrich.search_engines import LinkResolver, build_search_url
from app.enrich.search_enricher import SearchEnricher
from app.fetch.fetcher import Fetcher
from app.fetch.rate_limiter import RateLimiter
from app.fetch.retry import RetryPolicy
from app.loader import ScrapeApiClient
from app.loader.base import PageLoader
from app.parse import ParseContext
from app.parse.google_parser import GoogleSearchParser, breadcrumb_origin, google_link

GOOGLE_URL = "https://www.google.com/search?q=digikhata+pakistani+company&hl=en&gl=us"
CAPTCHA = ('<html><body><form id="captcha-form"><div class="g-recaptcha"></div></form>'
           "Our systems have detected unusual traffic from your computer network.</body></html>")
ACME = """<html><body><div id="search"><div id="rso">
  <div class="MjjYud"><div><a href="/goto?url=TOKEN1"><h3>Acme Traders | Home</h3></a>
    <cite>https://acme.pk<span> › about</span></cite><span class="VuuXrf">Acme</span></div>
    <div class="VwiC3b">Acme Traders Karachi. Call 021-111-222-333 Read more</div></div>
  <div class="MjjYud"><div><a href="/url?q=https://www.rozee.pk/company/acme&amp;sa=U"><h3>Acme jobs</h3></a>
  </div></div>
  <div class="MjjYud"><div><a href="/search?q=acme+images"><h3>Images for acme</h3></a></div></div>
</div></div>
<div id="m-x-content"><h3>AI Mode reply for acme</h3><div data-container-id="main-col">
  <div>Acme Traders is a <strong>Karachi-based</strong> distributor. Email info@acme.pk.
    <span><button data-icl-uuid="u1">Acme&nbsp;+1</button></span></div>
  <div role="heading">Products</div>
  <ul><li>Cement<ul><li>Bulk orders</li></ul></li><li>Steel</li></ul>
  <div>If you'd like, I can compare Acme with its competitors.</div>
</div><div data-container-id="rhs-col">Shared 0 files</div></div>
<!--TgQPHd|||[["u1",["Acme | About us","About Acme Traders","fav","https://acme.pk",["Acme"],"/goto?url=SRC1",null]]]-->
<!--TgQPHd|||[["u1",null,null,0,"fav","Acme",2,"https://acme.pk/about",null]]-->
</body></html>"""


def google_page(html: str, url: str = GOOGLE_URL) -> PageResponse:
    return make_page(html, url)


@pytest.fixture
def parser(rules) -> GoogleSearchParser:
    return GoogleSearchParser(ParseContext(rules))


# ---------------------------------------------------------------- parsing


def test_real_page_results_and_ai_overview(parser):
    page = parser.parse(google_page(fixture_html("google_search.html")))
    assert len(page.results) == 8
    first, second = page.results[0], page.results[1]
    assert first.site_name == "LinkedIn · DigiKhata"  # no breadcrumb: only the redirect link is known
    assert not first.resolved and first.url == first.redirect_url and "/goto?url=" in first.url
    assert second.url == "https://digikhata.pk/" and not second.resolved and second.redirect_url
    play = next(r for r in page.results if "play.google.com" in r.url)
    assert play.resolved and play.redirect_url is None
    assert all(r.engine == "google" for r in page.results)
    assert not any(r.snippet and r.snippet.endswith("Read more") for r in page.results)

    [overview] = page.summaries
    assert overview.kind == "ai_overview" and overview.engine == "google" and overview.page_url == GOOGLE_URL
    assert overview.text.startswith("DigiKhata is a Pakistan-based financial technology (fintech) company founded "
                                    "in 2020 by Adnan Aslam and Bilal Anjum.")
    assert "  - DigiPOS: Allows phones to accept contactless NFC" in overview.text  # nested bullet
    assert "+2" not in overview.text and "If you'd like" not in overview.text and "AI Mode" not in overview.text
    titles = [s.title for s in overview.sources]
    assert "DigiKhata Pvt Ltd is a Pakistan-based Fintech Company" in titles
    apple = next(s for s in overview.sources if s.site == "https://apps.apple.com")
    assert apple.url == "https://apps.apple.com/pk/app/digikhata-business-accounting/id1571599845"


def test_synthetic_page(parser):
    page = parser.parse(google_page(ACME))
    assert [r.title for r in page.results] == ["Acme Traders | Home", "Acme jobs"]
    acme, rozee = page.results
    assert acme.url == "https://acme.pk/" and acme.redirect_url == "https://www.google.com/goto?url=TOKEN1"
    assert acme.snippet == "Acme Traders Karachi. Call 021-111-222-333"
    assert rozee.url == "https://www.rozee.pk/company/acme" and rozee.resolved
    [overview] = page.summaries
    assert overview.text == ("Acme Traders is a Karachi-based distributor. Email info@acme.pk.\nProducts\n"
                             "- Cement\n  - Bulk orders\n- Steel")
    [source] = overview.sources
    assert source.title == "Acme | About us" and source.url == "https://acme.pk/about"
    assert source.redirect_url == "https://www.google.com/goto?url=SRC1"


def test_captcha_no_results_and_unknown_layout(parser):
    with pytest.raises(BlockedError):
        parser.parse(google_page(CAPTCHA))
    empty = parser.parse(google_page("<html><body>Your search - zzqx - did not match any documents.</body></html>"))
    assert empty.results == [] and empty.summaries == []
    with pytest.raises(ParseError):
        parser.parse(google_page("<html><body>something else</body></html>"))


def test_google_link_and_breadcrumb():
    assert google_link("/url?q=https://a.pk/x&sa=U", GOOGLE_URL) == ("https://a.pk/x", True)
    assert google_link("/goto?url=abc", GOOGLE_URL) == ("https://www.google.com/goto?url=abc", False)
    assert google_link("https://play.google.com/store/apps", GOOGLE_URL) == ("https://play.google.com/store/apps", True)
    assert google_link("/search?q=x&tbm=isch", GOOGLE_URL) is None
    assert breadcrumb_origin("https://tracxn.com › Discover › Companies") == "https://tracxn.com/"
    assert breadcrumb_origin("7.4K+ followers") is None


def test_search_url():
    assert build_search_url("https://www.google.com/search?q={query}&hl={language}&gl={region}", "a b", region="pk",
                            page=1, page_size=10, page_param="&start={offset}") == \
        "https://www.google.com/search?q=a+b&hl=en&gl=pk&start=10"


# ---------------------------------------------------------------- link resolution


def resolver_for(targets: dict[str, str]) -> LinkResolver:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        target = targets.get(str(request.url))
        return httpx.Response(302, headers={"location": target}) if target else httpx.Response(200, text="?")

    resolver = LinkResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    resolver.calls = calls  # type: ignore[attr-defined]
    return resolver


def test_link_resolver_strips_text_fragments_and_caches():
    resolver = resolver_for({"https://www.google.com/goto?url=A": "https://a.pk/about#:~:text=Founded%20in"})

    async def run():
        first = await resolver.resolve_all(["https://www.google.com/goto?url=A", "https://www.google.com/goto?url=B"])
        again = await resolver.resolve("https://www.google.com/goto?url=A")
        return first, again

    first, again = asyncio.run(run())
    assert first == {"https://www.google.com/goto?url=A": "https://a.pk/about"}
    assert again == "https://a.pk/about"
    assert resolver.calls.count("https://www.google.com/goto?url=A") == 1


# ---------------------------------------------------------------- engines: fallback, cooldown, merge


class RoutingLoader(PageLoader):
    """Serves Google and DuckDuckGo pages from fixtures; `google_html` may be swapped mid-test."""

    name = "routing"

    def __init__(self, google_html: str = ACME) -> None:
        super().__init__(retry=RetryPolicy(max_attempts=1, backoff_seconds=0, max_backoff_seconds=0))
        self.google_html = google_html
        self.calls: list[str] = []

    async def _fetch_once(self, url: str, *, render_js: bool) -> PageResponse:
        self.calls.append(url)
        html = self.google_html if "google.com/search" in url else fixture_html("ddg_html.html")
        return PageResponse(url=url, final_url=url, status_code=200, html=html, latency_ms=1, loader=self.name)


def search_config(tmp_path, mode: str = "fallback", **google) -> JobConfig:
    engines = [SearchEngineConfig(name="google", retry={"max_attempts": 1, "backoff_seconds": 0}, **google),
               SearchEngineConfig(name="duckduckgo")]
    return JobConfig(job_name="t", output_dir=tmp_path, state_dir=tmp_path,
                     search={"mode": mode, "engines": engines},
                     retry={"max_attempts": 1, "backoff_seconds": 0, "max_backoff_seconds": 0})


def enricher_for(config: JobConfig, loader: RoutingLoader, resolver: LinkResolver | None = None) -> SearchEnricher:
    return SearchEnricher(config, Fetcher(loader), load_extraction_rules(), resolver=resolver)


def test_fallback_uses_google_and_resolves_links(tmp_path):
    resolver = resolver_for({"https://www.google.com/goto?url=TOKEN1": "https://acme.pk/home",
                             "https://www.google.com/goto?url=SRC1": "https://acme.pk/about-us"})
    loader = RoutingLoader()
    outcome = asyncio.run(enricher_for(search_config(tmp_path), loader, resolver).search(make_company("Acme")))
    assert outcome.engines == ["google"] and not outcome.errors
    assert not any("duckduckgo" in url for url in loader.calls)
    assert outcome.results[0].url == "https://acme.pk/home" and outcome.results[0].resolved
    assert outcome.summaries[0].sources[0].url == "https://acme.pk/about"  # direct citation link wins


def test_blocked_google_falls_back_and_cools_down(tmp_path):
    loader = RoutingLoader(google_html=CAPTCHA)
    enricher = enricher_for(search_config(tmp_path, resolve_links=False), loader)

    async def run():
        first = await enricher.search(make_company("Engro Corporation"))
        google_calls = sum("google.com" in url for url in loader.calls)
        second = await enricher.search(make_company("Engro Corporation"))
        return first, google_calls, second

    first, google_calls, second = asyncio.run(run())
    assert first.engines == ["duckduckgo"] and first.results
    [(engine, error)] = first.errors
    assert engine == "google" and isinstance(error, BlockedError)
    assert google_calls == 1
    assert second.engines == ["duckduckgo"] and not second.errors  # google skipped while cooling down
    assert sum("google.com" in url for url in loader.calls) == 1


def test_every_engine_cooling_down_waits_for_the_first_to_recover(tmp_path):
    now = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds

    config = search_config(tmp_path, resolve_links=False)
    enricher = SearchEnricher(config, Fetcher(RoutingLoader()), load_extraction_rules(), sleep=fake_sleep)
    for engine in enricher.engines:
        engine._clock = lambda: now["t"]
    google, ddg = enricher.engines
    google.cooldown_until, ddg.cooldown_until = 50.0, 100.0
    outcome = asyncio.run(enricher.search(make_company("Acme")))
    assert sleeps == [50.0]
    assert outcome.engines == ["google"]


def test_google_only_waits_out_a_block_instead_of_failing_every_company(tmp_path):
    now = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds

    google = SearchEngineConfig(name="google", resolve_links=False, cooldown_seconds=300,
                                retry={"max_attempts": 1, "backoff_seconds": 0})
    config = JobConfig(job_name="t", output_dir=tmp_path, state_dir=tmp_path, search={"engines": [google]},
                       retry={"max_attempts": 1, "backoff_seconds": 0, "max_backoff_seconds": 0})
    loader = RoutingLoader(google_html=CAPTCHA)
    enricher = SearchEnricher(config, Fetcher(loader), load_extraction_rules(), sleep=fake_sleep)
    enricher.engines[0]._clock = lambda: now["t"]

    async def run():
        with pytest.raises(BlockedError):
            await enricher.search(make_company("Acme"))
        loader.google_html = ACME  # Google lets us back in after the cooldown
        return await enricher.search(make_company("Acme"))

    outcome = asyncio.run(run())
    assert sleeps == [300.0]
    assert outcome.engines == ["google"] and outcome.summaries
    assert sum("google.com" in url for url in loader.calls) == 2


def test_mode_all_merges_engines(tmp_path):
    loader = RoutingLoader()
    outcome = asyncio.run(enricher_for(search_config(tmp_path, mode="all", resolve_links=False), loader)
                          .search(make_company("Engro Corporation")))
    assert outcome.engines == ["google", "duckduckgo"]
    assert {r.engine for r in outcome.results} == {"google", "duckduckgo"}
    assert len(outcome.summaries) == 1


# ---------------------------------------------------------------- scrape API loader

ENDPOINT = "http://scrape.test/v1/scrape"
NO_WAIT = RetryPolicy(max_attempts=2, backoff_seconds=0, max_backoff_seconds=0)


def scrape_client(handler, **kwargs) -> ScrapeApiClient:
    return ScrapeApiClient(endpoint=ENDPOINT, api_key="sp_test", retry=kwargs.pop("retry", NO_WAIT),
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)


def success(html: str = "<html>ok</html>", **extra) -> httpx.Response:
    return httpx.Response(200, json={"outcome": "success", "content": html, "final_url": GOOGLE_URL,
                                     "status_code": 200, **extra})


def test_scrape_api_request_shape_and_page():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return success()

    options = LoaderOptions(wait_until="load", block_resources=["image", "font"], wait_for_selector="#rso")
    page = asyncio.run(scrape_client(handler, options=options, timeout=60).load(GOOGLE_URL))
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer sp_test"
    assert orjson.loads(request.content) == {
        "url": GOOGLE_URL, "render_js": True, "wait_until": "load", "timeout_ms": 60000, "response_format": "html",
        "block_resources": ["image", "font"], "wait_for": {"selector": "#rso", "state": "attached"}}
    assert page.html == "<html>ok</html>" and page.loader == "scrape-api" and page.final_url == GOOGLE_URL


def test_scrape_api_challenge_backs_off_then_gives_up_as_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, json={"error": {"code": "TARGET_CHALLENGE", "message": "captcha",
                                                   "retryable": False, "retry_after_s": 30}})

    async def run():
        limiter = RateLimiter(1000, 1)
        client = scrape_client(handler, limiter=limiter)
        with pytest.raises(LoaderError) as info:
            await client.load(GOOGLE_URL)
        return info.value, limiter

    error, limiter = asyncio.run(run())
    assert calls["n"] == 2 and error.status_code == 429
    assert limiter._paused_until > 0


def test_scrape_api_outcomes_and_errors():
    responses = iter([httpx.Response(200, json={"outcome": "target_rate_limited", "status_code": 429}), success()])
    assert asyncio.run(scrape_client(lambda r: next(responses)).load(GOOGLE_URL)).attempts == 2

    timeouts = iter([httpx.Response(504, json={"error": {"code": "WAIT_CONDITION_TIMEOUT", "retryable": False}}),
                     success()])
    assert asyncio.run(scrape_client(lambda r: next(timeouts)).load(GOOGLE_URL)).attempts == 2

    calls = {"n": 0}

    def unauthorized(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "message": "bad key"}})

    with pytest.raises(LoaderError) as info:
        asyncio.run(scrape_client(unauthorized).load(GOOGLE_URL))
    assert calls["n"] == 1 and info.value.status_code == 401

    with pytest.raises(LoaderError):
        asyncio.run(scrape_client(lambda r: httpx.Response(200, json={"outcome": "target_access_denied",
                                                                      "status_code": 403})).load(GOOGLE_URL))


def test_scrape_api_missing_key():
    with pytest.raises(ConfigError):
        ScrapeApiClient(endpoint=ENDPOINT, api_key=None)
