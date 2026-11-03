"""Enricher agent: EnrichRequest -> page text from web.pages -> local LLM (Ollama) -> EnrichResult.

Stateless: reads the domain's parsed pages, asks the model, publishes facts. It writes nothing; the writer does.
Hallucination guard: every factual field (city, founded year, employees, market cap, financial info) must come
with an exact quote from the page text, and the value must appear in that quote. Facts that fail are dropped.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date

import httpx

from .. import db
from ..observability import get_logger
from ..taxonomy import GROUPS
from .messages import ENRICH_REQUESTS, EnrichRequest, EnrichResult
from .settings import get_scrape_settings
from .streams import Delivery, StreamWorker, connect, publish

log = get_logger(__name__)

GROUP = "enricher"
PROMPT_VERSION = "e2"
PAGE_ORDER = ("about", "company", "contact", "team", "home")
_SPACE = re.compile(r"\s+")


def _evidenced(kind: str) -> dict:
    return {"type": ["object", "null"], "properties": {"value": {"type": kind}, "evidence": {"type": "string"}},
            "required": ["value", "evidence"]}


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "industry": {"type": ["string", "null"]},
        "category_group": {"type": ["string", "null"], "enum": [*GROUPS, None]},
        "city": _evidenced("string"),
        "founded_year": _evidenced("integer"),
        "employees": _evidenced("string"),
        "market_cap": _evidenced("string"),
        "financial_info": _evidenced("string"),
    },
    "required": ["company_name", "summary", "industry", "category_group", "city", "founded_year", "employees",
                 "market_cap", "financial_info"],
}

PROMPT = """You extract facts about the company that owns the website {domain}, using ONLY the page text below.

Return JSON:
- company_name: the company or brand name.
- summary: 1-2 neutral sentences on what the company does and for whom. Always fill this. Do not put years,
  counts or other figures in the summary.
- industry: a short industry label, e.g. "News Media", "Online Pharmacy", "Healthcare Marketplace".
- category_group: the closest of {groups}, or null.
- city, founded_year, employees, market_cap, financial_info (revenue, funding, stock listing):
  each as {{"value": ..., "evidence": "<exact words copied from the text>"}} ONLY if the text states it
  about this company. Otherwise null. city is the headquarters/office city name only, not an address.
Never use outside knowledge and never guess.

PAGE TEXT:
{text}"""


def build_context(pages: list[dict], budget: int) -> str:
    """About/company/contact pages first (they describe the company), the homepage last; fair share per page."""
    pages = sorted(pages, key=lambda p: PAGE_ORDER.index(p["kind"]) if p["kind"] in PAGE_ORDER else 9)
    blocks, remaining = [], budget
    for i, page in enumerate(pages):
        share = remaining // (len(pages) - i)
        head = f"--- {page['kind']} page {page['url']}\nTitle: {page['title'] or ''}\n"
        body = (page["text"] or "")[:max(share - len(head), 0)]
        blocks.append(head + body)
        remaining -= len(head) + len(body)
    return "\n\n".join(blocks)


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip().casefold()


def validate(answer: dict, text: str) -> list[dict]:
    """Turn the model's answer into facts, dropping anything not supported by the text."""
    corpus = _norm(text)
    facts = []
    for field in ("company_name", "summary", "industry"):
        if isinstance(answer.get(field), str) and answer[field].strip():
            facts.append({"field": field, "value": answer[field].strip()[:1000], "source": "llm"})
    if answer.get("category_group") in GROUPS:
        facts.append({"field": "category_group", "value": answer["category_group"], "source": "llm"})

    for field in ("city", "founded_year", "employees", "market_cap", "financial_info"):
        item = answer.get(field)
        if not isinstance(item, dict) or item.get("value") in (None, ""):
            continue
        value, evidence = str(item["value"]).strip(), _norm(str(item.get("evidence") or ""))
        if len(evidence) < 4 or evidence not in corpus:
            continue                                   # quote not in the text: invented
        if field == "founded_year" and not (value.isdigit() and 1800 <= int(value) <= date.today().year
                                            and value in evidence):
            continue
        if field in ("city", "employees") and _norm(re.sub(r"[,+]", "", value)) not in re.sub(r"[,+]", "", evidence):
            continue
        facts.append({"field": field, "value": value[:500], "source": "llm", "evidence": item["evidence"][:500],
                      "confidence": 0.6})
    return facts


class Enricher:
    def __init__(self, r):
        self.r = r
        self.settings = get_scrape_settings()
        self.http = httpx.AsyncClient(base_url=self.settings.ollama_url, timeout=300)

    async def ask(self, domain: str, text: str) -> dict:
        response = await self.http.post("/api/chat", json={
            "model": self.settings.ollama_model, "stream": False, "think": False, "keep_alive": "30m",
            "format": RESPONSE_SCHEMA, "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "user", "content": PROMPT.format(domain=domain, groups=", ".join(GROUPS),
                                                                   text=text)}],
        })
        response.raise_for_status()
        return json.loads(response.json()["message"]["content"])

    async def handle(self, delivery: Delivery) -> None:
        request = EnrichRequest.from_json(delivery.raw)
        result = EnrichResult(campaign_id=request.campaign_id, domain=request.domain, country=request.country,
                              model=self.settings.ollama_model, prompt_version=PROMPT_VERSION)
        pages = await asyncio.to_thread(self._pages, request.domain)
        if not pages:
            result.error_class, result.error_message = "no_content", "no page could be fetched"
        else:
            text = build_context(pages, self.settings.llm_text_budget)
            try:
                answer = await self.ask(request.domain, text)
                result.facts = validate(answer, text)
            except (httpx.HTTPError, ValueError, KeyError) as error:
                if delivery.deliveries < self.settings.max_deliveries:
                    raise                               # redelivered later (e.g. Ollama restarting)
                # Out of retries: finish the domain without LLM facts rather than leaving it stuck.
                result.error_class, result.error_message = "llm_failed", f"{type(error).__name__}: {error}"[:500]
        await publish(self.r, result)

    @staticmethod
    def _pages(domain: str) -> list[dict]:
        with db.serving() as conn:
            return conn.execute("SELECT kind, url, title, text FROM web.pages WHERE domain = %s AND text <> ''",
                                [domain]).fetchall()

    async def close(self):
        await self.http.aclose()


async def run(stop: asyncio.Event, concurrency: int = 1) -> None:
    r = connect()
    enricher = Enricher(r)
    try:
        await StreamWorker(r, ENRICH_REQUESTS, GROUP, handle=enricher.handle, concurrency=concurrency).run(stop)
    finally:
        await enricher.close()
        await r.aclose()
