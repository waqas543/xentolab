"""Official-domain detection: score every domain seen in a company's search results.

Signals: how well the domain's name part matches the company name (exact, contains, token coverage, acronym,
fuzzy similarity), preferred TLDs (.pk first), result rank, title match and repetition. Social, directory,
news, marketplace and government domains are categorized and never chosen. The best candidate becomes the
`likely_domain` only if it relates to the name and clears `min_likely_domain_score`.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.core.config import ExtractionRules
from app.core.models import CompanyInput, DomainCandidate, SearchResult
from app.core.utils import domain_label, domain_suffix, host_of, registrable_domain, unique

LEGAL_WORDS = {"the", "and", "of", "pvt", "private", "ltd", "limited", "co", "company", "smc", "llp", "inc", "plc",
               "llc", "guarantee", "public", "m/s", "ms"}
WEAK_WORDS = {"corporation", "corp", "group", "industries", "industry", "international", "pakistan", "pak",
              "enterprises", "enterprise", "traders", "trading", "services", "service", "holdings", "global"}
TLD_BONUS = {"pk": 10.0, "com.pk": 10.0, "org.pk": 8.0, "net.pk": 8.0, "com": 6.0, "net": 3.0, "org": 3.0,
             "co": 3.0, "biz": 1.0}
MAX_NAME_SCORE = 70.0
_TOKEN = re.compile(r"[a-z0-9]+")


def name_tokens(name: str) -> tuple[list[str], list[str]]:
    """(all meaningful tokens, core tokens without generic business words)."""
    tokens = [t for t in _TOKEN.findall(name.lower().replace("&", " and ")) if t not in LEGAL_WORDS]
    core = [t for t in tokens if t not in WEAK_WORDS] or tokens
    return tokens, core


def name_match_score(name: str, domain: str) -> tuple[float, list[str]]:
    tokens, core = name_tokens(name)
    if not tokens:
        return 0.0, []
    label = domain_label(domain).lower().replace("-", "")
    compact_all, compact_core = "".join(tokens), "".join(core)
    score, reasons = 0.0, []

    if label in (compact_all, compact_core):
        score += 60
        reasons.append("exact_name")
    elif len(compact_core) >= 4 and compact_core in label:
        score += 45
        reasons.append("domain_contains_name")
    elif len(label) >= 4 and label in compact_core and len(label) / len(compact_core) >= 0.5:
        score += 35
        reasons.append("name_contains_domain")

    long_core = [t for t in core if len(t) >= 3]
    if long_core:
        matched = [t for t in long_core if t in label]
        if matched:
            score += 30 * len(matched) / len(long_core)
            reasons.append(f"tokens_{len(matched)}/{len(long_core)}")

    if len(tokens) >= 2:
        acronym = "".join(t[0] for t in tokens)
        if len(acronym) >= 2 and (label == acronym or (len(acronym) >= 3 and label.startswith(acronym))):
            score += 25
            reasons.append("acronym")

    ratio = SequenceMatcher(None, compact_core, label).ratio()
    if ratio >= 0.75 and "exact_name" not in reasons:
        score += 20 * ratio
        reasons.append(f"similar_{ratio:.2f}")
    return min(score, MAX_NAME_SCORE), reasons


class DomainDetector:
    def __init__(self, rules: ExtractionRules) -> None:
        self.rules = rules

    def detect(self, company: CompanyInput, results: list[SearchResult]) -> list[DomainCandidate]:
        grouped: dict[str, list[SearchResult]] = {}
        for result in results:
            domain = registrable_domain(result.url)
            if domain:
                grouped.setdefault(domain, []).append(result)

        compact_name = "".join(name_tokens(company.search_name)[1])
        candidates = [self._score(domain, hits, company.search_name, compact_name) for domain, hits in grouped.items()]
        candidates.sort(key=lambda c: (-c.score, c.best_rank))

        best = next((c for c in candidates if c.category == "candidate"), None)
        if best and best.score >= self.rules.min_likely_domain_score and any(
                r.startswith(("exact_name", "domain_contains", "name_contains", "tokens_", "acronym", "similar_"))
                for r in best.reasons):
            best.likely_domain = True
        return candidates

    def _score(self, domain: str, hits: list[SearchResult], name: str, compact_name: str) -> DomainCandidate:
        candidate = DomainCandidate(domain=domain, hosts=unique(host_of(r.url) for r in hits),
                                    result_urls=unique(r.url for r in hits), best_rank=min(r.rank for r in hits))
        if category := self.rules.generic_category(domain):
            candidate.category = category
            candidate.reasons = [f"generic_{category}"]
            return candidate

        score, reasons = name_match_score(name, domain)
        if (bonus := TLD_BONUS.get(domain_suffix(domain), 0.0)) > 0:
            score += bonus
            reasons.append(f"tld_{domain_suffix(domain)}")
        if (rank_bonus := max(0, 11 - candidate.best_rank)) > 0:
            score += rank_bonus
            reasons.append(f"rank_{candidate.best_rank}")
        if compact_name and any(compact_name in re.sub(r"[^a-z0-9]", "", r.title.lower()) for r in hits):
            score += 10
            reasons.append("title_match")
        if len(hits) > 1:
            score += min(9, 3 * (len(hits) - 1))
            reasons.append(f"hits_{len(hits)}")
        candidate.score = round(score, 2)
        candidate.reasons = reasons
        return candidate
