"""Company profile from the crawled website: page text + search snippets -> local Qwen (Ollama) -> structured
profile with a summary.

Hallucination guard (the model runs at temperature 0 but still invents):
- every single-value fact (city, address, founded year, employees, legal form, ...) must come with an exact quote
  from the text, and the value must appear in that quote;
- every list item (products, brands, people, certifications, ...) must appear in the text;
- summary, industry and business type are the model's own wording and are not checked.
Facts that fail are dropped and listed in `dropped`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from app.core.config import SummaryConfig
from app.core.exceptions import ScrapingError
from app.core.logging import get_logger
from app.core.models import CompanyProfile, KeyPerson
from app.core.utils import normalize_whitespace

log = get_logger(__name__)

PROMPT_VERSION = "p1"
BUSINESS_TYPES = ["manufacturer", "trader", "distributor", "retailer", "service_provider", "software",
                  "construction", "agriculture", "ngo", "government", "other"]
EVIDENCED = ("city", "address", "founded_year", "employees", "legal_form", "parent_company", "stock_listing",
             "financial_info", "production_capacity")
LISTS = ("products_services", "brands", "certifications", "export_markets", "locations", "partners_clients")
# Values the model may reword inside its quote: checked by word overlap instead of an exact substring.
_LOOSE = {"address", "financial_info", "production_capacity", "parent_company"}
PAGE_ORDER = ("about", "linkedin", "products", "team", "contact", "home", "page")
_KINDS = (("about", r"about|company|profile|who-?we-?are|overview|history|introduction"),
          ("products", r"product|service|solution|brand|portfolio|capabilit"),
          ("team", r"team|management|leadership|board|director"),
          ("contact", r"contact|reach|location|branch|office"))
_WORD = re.compile(r"[\w&]+")
_SPACE = re.compile(r"\s+")


class SummaryError(ScrapingError):
    """The model could not be reached (retry the company later)."""

    stage = "summary"


def _evidenced(kind: str | list[str]) -> dict[str, Any]:
    kinds = kind if isinstance(kind, list) else [kind]
    return {"type": ["object", "null"],
            "properties": {"value": {"type": kinds}, "evidence": {"type": "string"}},
            "required": ["value", "evidence"]}


_LIST = {"type": "array", "items": {"type": "string"}}
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "industry": {"type": ["string", "null"]},
        "business_type": {"type": ["string", "null"], "enum": [*BUSINESS_TYPES, None]},
        **{name: _LIST for name in LISTS},
        "key_people": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"type": "string"}, "role": {"type": ["string", "null"]}},
            "required": ["name", "role"]}},
        **{name: _evidenced("integer" if name == "founded_year" else "string") for name in EVIDENCED},
    },
    "required": ["company_name", "summary", "industry", "business_type", *LISTS, "key_people", *EVIDENCED],
}

PROMPT = """You extract facts about the company "{name}" whose website is {domain}, using ONLY the text below
(pages of its website, its LinkedIn page and search-result snippets). Never use outside knowledge and never guess.

Return JSON:
- company_name: the company's name as the text writes it.
- summary: 2-3 neutral sentences on what the company does, its main products or services, and who it serves.
  Always fill this. No years or figures.
- industry: a short industry label, e.g. "Flour Milling", "Textile Manufacturing", "Fintech", "Pharmaceuticals".
- business_type: one of {types}.
- products_services: up to 15 products or services the text names.
- brands: brand names the company owns or sells.
- key_people: up to 10 people the text names with their role at this company, as {{"name", "role"}}.
- certifications: certifications, standards or memberships (e.g. ISO 9001, HACCP, Halal).
- export_markets: countries or regions it exports to or serves abroad.
- locations: cities where it has offices, plants, branches or outlets.
- partners_clients: named partners, principals or clients.
- city, address, founded_year, employees, legal_form, parent_company, stock_listing, financial_info,
  production_capacity: each as {{"value": ..., "evidence": "<exact words copied from the text>"}} ONLY if the text
  states it about this company, otherwise null. city = head-office city name only. legal_form e.g.
  "(Private) Limited", "Public Limited", "Partnership". stock_listing = exchange and symbol. financial_info =
  revenue, profit or funding. production_capacity e.g. "500 tons per day".
Use [] for lists the text does not support.

TEXT:
{text}"""


# ---------------------------------------------------------------- context


def page_kind(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.strip("/").lower()
    if not path:
        return "home"
    for kind, pattern in _KINDS:
        if re.search(pattern, path):
            return kind
    return "page"


def build_context(pages: list[tuple[str, str, str | None, str]], snippets: list[str], budget: int) -> str:
    """Website pages (about first, homepage late; a fair share each), then search snippets, within `budget`
    characters. `pages` are (kind, url, title, text)."""
    pages = sorted(pages, key=lambda p: PAGE_ORDER.index(p[0]) if p[0] in PAGE_ORDER else len(PAGE_ORDER))
    snippet_block = "\n".join(f"- {s}" for s in snippets)
    snippet_budget = min(len(snippet_block), budget // 4) if pages else budget
    remaining = budget - snippet_budget
    blocks: list[str] = []
    for i, (kind, url, title, text) in enumerate(pages):
        share = remaining // (len(pages) - i)
        head = f"--- {kind} page {url}\nTitle: {title or ''}\n"
        body = text[:max(share - len(head), 0)]
        blocks.append(head + body)
        remaining -= len(head) + len(body)
    if snippet_block:
        blocks.append("--- search results\n" + snippet_block[:snippet_budget + max(remaining, 0)])
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- validation


def _norm(text: str) -> str:
    return _SPACE.sub(" ", normalize_whitespace(text)).casefold()


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(_norm(text)) if len(w) > 1}


def _in_text(value: str, corpus: str) -> bool:
    """An item is supported when it appears verbatim, or all its words (2+ letters) appear in the text."""
    value = _norm(value)
    if not value:
        return False
    if value in corpus:
        return True
    words = _words(value)
    return bool(words) and all(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", corpus) for w in words)


def validate(answer: dict[str, Any], text: str, *, name: str, domain: str, model: str) -> CompanyProfile:
    corpus = _norm(text)
    dropped: list[str] = []
    data: dict[str, Any] = {}

    for field in ("company_name", "summary", "industry"):
        value = answer.get(field)
        if isinstance(value, str) and value.strip():
            data[field] = normalize_whitespace(value)[:1500]
    if answer.get("business_type") in BUSINESS_TYPES:
        data["business_type"] = answer["business_type"]

    for field in LISTS:
        kept: list[str] = []
        for item in answer.get(field) or []:
            if not isinstance(item, str) or not item.strip():
                continue
            item = normalize_whitespace(item)[:200]
            if _in_text(item, corpus):
                if item.casefold() not in (k.casefold() for k in kept):
                    kept.append(item)
            else:
                dropped.append(f"{field}:{item}")
        data[field] = kept[:25]

    people: list[KeyPerson] = []
    for person in answer.get("key_people") or []:
        if not isinstance(person, dict) or not isinstance(person.get("name"), str):
            continue
        person_name = normalize_whitespace(person["name"])[:120]
        if not person_name or not _in_text(person_name, corpus):
            dropped.append(f"key_people:{person_name}")
            continue
        role = person.get("role")
        role = normalize_whitespace(role)[:120] if isinstance(role, str) and _in_text(role, corpus) else None
        if all(p.name.casefold() != person_name.casefold() for p in people):
            people.append(KeyPerson(name=person_name, role=role))
    data["key_people"] = people[:15]

    evidence: dict[str, str] = {}
    for field in EVIDENCED:
        item = answer.get(field)
        if not isinstance(item, dict) or item.get("value") in (None, ""):
            continue
        value = normalize_whitespace(str(item["value"]))
        quote = normalize_whitespace(str(item.get("evidence") or ""))
        if not _quote_supports(field, value, quote, corpus):
            dropped.append(f"{field}:{value}")
            continue
        data[field] = int(value) if field == "founded_year" else value[:500]
        evidence[field] = quote[:500]

    return CompanyProfile(name=name, domain=domain, model=model, prompt_version=PROMPT_VERSION, evidence=evidence,
                          dropped=dropped, **data)


def _quote_supports(field: str, value: str, quote: str, corpus: str) -> bool:
    norm_quote = _norm(quote)
    if len(norm_quote) < 4 or norm_quote not in corpus:
        return False  # the quote is not in the text: invented
    if field == "founded_year":
        return value.isdigit() and 1800 <= int(value) <= date.today().year and value in norm_quote
    if field in _LOOSE:
        words = _words(value)
        return bool(words) and len(words & _words(quote)) / len(words) >= 0.7
    strip = re.compile(r"[,+]")
    return strip.sub("", _norm(value)) in strip.sub("", norm_quote)


# ---------------------------------------------------------------- model


class CompanySummarizer:
    """Asks Ollama for a structured profile. One request at a time by default (a 4B model on a laptop)."""

    transport: ClassVar[httpx.AsyncBaseTransport | None] = None  # tests swap in a mock

    def __init__(self, config: SummaryConfig, ollama_url: str) -> None:
        self.config = config
        self._client = httpx.AsyncClient(base_url=ollama_url, timeout=config.timeout_seconds,
                                         transport=self.transport)
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

    async def summarize(self, *, name: str, domain: str, text: str) -> CompanyProfile:
        """Raises SummaryError when Ollama is unreachable; an unusable answer after two tries gives ValueError."""
        prompt = PROMPT.format(name=name, domain=domain, types=", ".join(BUSINESS_TYPES), text=text)
        last: Exception | None = None
        for attempt in (1, 2):
            async with self._semaphore:
                started = time.perf_counter()
                answer = await self._ask(prompt)
                elapsed = int((time.perf_counter() - started) * 1000)
            try:
                data = json.loads(answer)
                if not isinstance(data, dict):
                    raise ValueError("answer is not a JSON object")
            except ValueError as exc:
                last = exc
                log.warning("summary.bad_answer", domain=domain, attempt=attempt, error=str(exc)[:200])
                continue
            profile = validate(data, text, name=name, domain=domain, model=self.config.model)
            log.info("summary.done", domain=domain, ms=elapsed, dropped=len(profile.dropped))
            return profile.model_copy(update={"llm_ms": elapsed})
        raise ValueError(f"model gave no usable JSON: {last}")

    async def _ask(self, prompt: str) -> str:
        try:
            response = await self._client.post("/api/chat", json={
                "model": self.config.model, "stream": False, "think": False, "keep_alive": "30m",
                "format": RESPONSE_SCHEMA,
                "options": {"temperature": 0, "num_ctx": self.config.num_ctx},
                "messages": [{"role": "user", "content": prompt}],
            })
            response.raise_for_status()
            return response.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise SummaryError(f"Ollama request failed ({self.config.model}): {type(exc).__name__}: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
