"""Wikidata access through its public endpoints: the SPARQL query service and the wbgetentities API.

Requests follow Wikimedia's API etiquette: a descriptive User-Agent (DATA_PLATFORM_USER_AGENT),
gzip, at most a couple of requests in flight, and Retry-After honoured on 429/503.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from ... import http
from ...errors import PermanentError, TransientError

BATCH = 50                          # wbgetentities limit for anonymous clients
LARGE_CLASS = 500                   # classes with this many items in the country are listed one by one
REVISION_BATCH = 400
ORGANIZATION = "Q43229"
BUSINESS = "Q4830453"
SPARQL_TIMEOUT = (15, 75)           # the query service stops queries after 60 s


def sparql_url() -> str:
    return os.environ.get("WIKIDATA_SPARQL_URL", "https://query.wikidata.org/sparql")


def api_url() -> str:
    return os.environ.get("WIKIDATA_API_URL", "https://www.wikidata.org/w/api.php")


def qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


class WikidataClient:
    def __init__(self, parallel: int = 2, should_cancel: Callable[[], None] | None = None):
        self.parallel = max(1, min(parallel, 4))
        self.session = http.new_session()
        self.session.headers["Accept-Encoding"] = "gzip"
        self.should_cancel = should_cancel
        self.requests = 0

    # -- SPARQL ---------------------------------------------------------------------------

    def sparql(self, query: str, attempts: int = 3) -> list[dict]:
        """Run a query. The service answers timeouts with a non-JSON body; those are retried, then raised."""
        for attempt in range(1, attempts + 1):
            self.requests += 1
            response = http.request("POST", sparql_url(), data={"query": query, "format": "json"},
                                    headers={"Accept": "application/sparql-results+json"}, session=self.session,
                                    timeout=SPARQL_TIMEOUT, should_cancel=self.should_cancel)
            try:
                body = response.json()
            except ValueError:
                if attempt == attempts:
                    raise TransientError("Wikidata query service returned a non-JSON response (query timeout?)",
                                         context={"body": response.text[:300]}) from None
                time.sleep(10 * attempt)
                continue
            return [{k: v["value"] for k, v in row.items()} for row in body["results"]["bindings"]]
        return []

    def country_qid(self, iso2: str) -> str:
        rows = self.sparql(f'SELECT ?c WHERE {{ ?c wdt:P297 "{iso2.upper()}" . }}')
        if not rows:
            raise PermanentError(f"No Wikidata country with ISO code {iso2}")
        return qid(rows[0]["c"])

    def organization_classes(self, country: str, root: str) -> dict[str, int]:
        """Classes used by the country's items that are (subclasses of) `root`, with item counts.

        Walking the class tree upward from the ~2k classes actually used is fast; walking it
        downward from `organization` times out.
        """
        used = {qid(r["class"]): int(r["n"]) for r in self.sparql(f"""
            SELECT ?class (COUNT(?item) AS ?n) WHERE {{ ?item wdt:P17 wd:{country} ; wdt:P31 ?class . }}
            GROUP BY ?class""") if r["class"].rsplit("/", 1)[-1].startswith("Q")}
        matched: dict[str, int] = {}
        ids = sorted(used)
        for start in range(0, len(ids), 150):
            values = " ".join(f"wd:{c}" for c in ids[start:start + 150])
            for row in self.sparql(f"""
                SELECT DISTINCT ?class WHERE {{
                  VALUES ?class {{ {values} }}
                  ?class wdt:P279* wd:{root} . hint:Prior hint:gearing "forward" .
                }}"""):
                matched[qid(row["class"])] = used[qid(row["class"])]
        return matched

    def items(self, country: str, classes: dict[str, int]) -> dict[str, list[str]]:
        """QID -> classes, for the country's items of these classes (no revisions: see `revisions`).

        Query plans matter on the public service: a large class that only exists in this country (e.g. 26k
        government schools) is listed class-first with the optimizer off; the many small classes share one
        query that starts from the country's items and filters by class. Generic classes ("organization")
        must never be listed class-first: they have millions of instances worldwide.
        """
        items: dict[str, list[str]] = {}
        large = sorted(c for c, n in classes.items() if n >= LARGE_CLASS)
        small = sorted(c for c, n in classes.items() if n < LARGE_CLASS)
        for cls in large:
            for row in self.sparql(f"""
                SELECT ?item WHERE {{ hint:Query hint:optimizer "None" .
                  ?item wdt:P31 wd:{cls} . ?item wdt:P17 wd:{country} . }}"""):
                items.setdefault(qid(row["item"]), []).append(cls)
        if small:
            listed = ", ".join(f"wd:{c}" for c in small)
            for row in self.sparql(f"""
                SELECT ?item ?class WHERE {{
                  ?item wdt:P17 wd:{country} . ?item wdt:P31 ?class . FILTER(?class IN ({listed})) }}"""):
                items.setdefault(qid(row["item"]), []).append(qid(row["class"]))
        return items

    def revisions(self, ids: Iterable[str], on_progress: Callable[[int, int], None] | None = None) -> dict[str, int]:
        """Current revision id of each item (direct lookups: fast and predictable)."""
        result: dict[str, int] = {}
        ids = sorted(set(ids))
        for start in range(0, len(ids), REVISION_BATCH):
            values = " ".join(f"wd:{i}" for i in ids[start:start + REVISION_BATCH])
            for row in self.sparql(f"SELECT ?item ?rev WHERE {{ VALUES ?item {{ {values} }} ?item schema:version ?rev . }}"):
                result[qid(row["item"])] = int(row["rev"])
            if on_progress:
                on_progress(min(start + REVISION_BATCH, len(ids)), len(ids))
        return result

    # -- entities -------------------------------------------------------------------------

    def _get(self, ids: list[str], props: str, languages: str) -> dict[str, dict]:
        # No `maxlag`: on Wikidata it includes the query-service updater lag (often minutes), and it exists to
        # pace bots that *edit*. Reads stay polite through low concurrency and Retry-After instead.
        params = {"action": "wbgetentities", "ids": "|".join(ids), "props": props, "languages": languages,
                  "sitefilter": "enwiki|urwiki", "format": "json"}
        for attempt in range(6):
            self.requests += 1
            body = http.request("GET", api_url(), params=params, session=self.session,
                                should_cancel=self.should_cancel).json()
            error = body.get("error")
            if not error:
                break
            if error.get("code") == "maxlag":         # the API asks clients to back off while replicas lag
                time.sleep(min(30, 5 * (attempt + 1)))
                continue
            raise PermanentError(f"Wikidata API error: {error.get('code')}: {error.get('info')}")
        else:
            raise TransientError("Wikidata API stayed lagged (maxlag) for too long")
        entities = {}
        for key, entity in body.get("entities", {}).items():
            if "missing" in entity:
                continue
            entities[entity.get("id", key)] = entity
            if entity.get("id") and entity["id"] != key:          # redirected (merged) item
                entities[key] = entity
        return entities

    def entities(self, ids: list[str], *, labels_only: bool = False,
                 on_progress: Callable[[int, int], None] | None = None) -> dict[str, dict]:
        props = "labels" if labels_only else "info|labels|descriptions|aliases|claims|sitelinks/urls"
        languages = "en" if labels_only else "en|ur"
        batches = [ids[i:i + BATCH] for i in range(0, len(ids), BATCH)]
        result: dict[str, dict] = {}
        done = 0
        with ThreadPoolExecutor(self.parallel) as pool:
            for entities in pool.map(lambda batch: self._get(batch, props, languages), batches):
                result.update(entities)
                done += 1
                if on_progress:
                    on_progress(done, len(batches))
        return result
