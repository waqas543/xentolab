"""Common Crawl URL indexes: crawl discovery, the ZipNum cluster index, CDX blocks and the CDX API.

Each crawl's CDX index is ~300 gzip files of lines `SURT timestamp {json}`, sorted by SURT
key and compressed in independent blocks of ~3,000 lines. `cluster.idx` (~100 MB) lists the
first key of every block with its file, offset and length. To read one country's ccTLD
we binary-search cluster.idx with HTTP range requests (~30 small reads) and then fetch only
the blocks whose keys fall under the TLD — never the whole index, never the WARC corpus.

Only the static files on data.commoncrawl.org are used: the CDX query server
(index.commoncrawl.org) is frequently overloaded and is not needed for this.
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass

import requests

from ... import http
from ...errors import PermanentError

PROBE_BYTES = 4096


CRAWL_ID = re.compile(r"CC-MAIN-\d{4}-\d{2}")


def data_url() -> str:
    return os.environ.get("COMMONCRAWL_DATA_URL", "https://data.commoncrawl.org").rstrip("/")


def crawls() -> list[str]:
    """Crawl ids with a URL index, newest first (from the public collections listing)."""
    listing = http.get_text(f"{data_url()}/cc-index/collections/index.html")
    return sorted(set(CRAWL_ID.findall(listing)), reverse=True)


def get_crawl(crawl_id: str | None) -> str:
    available = crawls()
    if not available:
        raise PermanentError("Common Crawl lists no crawls")
    if not crawl_id:
        return available[0]
    if crawl_id not in available:
        raise PermanentError(f"Unknown crawl {crawl_id!r}; newest is {available[0]}")
    return crawl_id


@dataclass(frozen=True)
class Block:
    key: str                # first SURT key in the block
    filename: str           # cdx-00253.gz
    offset: int
    length: int
    number: int


def _parse_cluster_line(raw: str) -> Block | None:
    parts = raw.split("\t")
    if len(parts) < 5:
        return None
    return Block(parts[0].split(" ")[0], parts[1], int(parts[2]), int(parts[3]), int(parts[4]))


class ClusterIndex:
    def __init__(self, crawl_id: str, session: requests.Session | None = None):
        self.crawl_id = crawl_id
        self.url = f"{data_url()}/cc-index/collections/{crawl_id}/indexes/cluster.idx"
        self.session = session or http.new_session()
        self.size = http.head(self.url).size_bytes
        if not self.size:
            raise PermanentError(f"cluster.idx for {crawl_id} has no size")
        self.reads = 0
        self._probes: dict[int, str] = {}          # bisections of nearby keys share their first probes

    def _read(self, start: int, length: int) -> str:
        self.reads += 1
        length = min(length, self.size - start)
        return http.get_range(self.url, start, length, session=self.session).decode("utf-8", "replace")

    def _key_at(self, offset: int) -> str:
        """First complete key at or after byte `offset`."""
        if offset not in self._probes:
            text = self._read(offset, PROBE_BYTES)
            if offset:
                text = text[text.index("\n") + 1:]
            self._probes[offset] = text.split("\t", 1)[0].split(" ", 1)[0]
        return self._probes[offset]

    def _bisect(self, key: str) -> int:
        """A byte offset at or before the first line whose key is >= `key` (within one probe window)."""
        low, high = 0, self.size
        while high - low > PROBE_BYTES:
            middle = (low + high) // 2
            if self._key_at(middle) < key:
                low = middle
            else:
                high = middle
        return low

    def blocks_for_prefix(self, prefix: str, upper: str | None = None) -> list[Block]:
        """Every block that can contain keys in [prefix, upper) — e.g. 'pk,' for the .pk ccTLD."""
        upper = upper or prefix[:-1] + chr(ord(prefix[-1]) + 1)
        start = max(0, self._bisect(prefix) - 2 * PROBE_BYTES)
        end = min(self.size, self._bisect(upper) + 2 * PROBE_BYTES)
        lines = self._read(start, end - start).split("\n")
        # Only complete lines: the first is partial unless we read from the file start, the last unless to its end.
        lines = lines[(1 if start else 0):(None if end >= self.size else -1)]
        blocks = [b for b in (_parse_cluster_line(line) for line in lines) if b]
        before = [b for b in blocks if b.key < prefix]
        inside = [b for b in blocks if prefix <= b.key < upper]
        # The block starting just before the prefix holds its first keys.
        return before[-1:] + inside

    def block_url(self, block: Block) -> str:
        return f"{data_url()}/cc-index/collections/{self.crawl_id}/indexes/{block.filename}"


def fetch_block(url: str, block: Block, session: requests.Session | None = None) -> bytes:
    """The raw gzip member of one block (kept verbatim in the artifact)."""
    raw = http.get_range(url, block.offset, block.length, session=session)
    if raw[:2] != b"\x1f\x8b":
        raise PermanentError(f"Block {block.filename}@{block.offset} is not gzip data")
    return raw


def cdx_lines(raw_block: bytes) -> list[str]:
    return gzip.decompress(raw_block).decode("utf-8", "replace").splitlines()


def domain_range(domain: str) -> tuple[str, str]:
    """SURT key range covering a domain and its subdomains: 'com,example)' .. 'com,example-' (exclusive).

    Keys look like 'com,example)/path' or 'com,example,www)/path'; ')' and ',' sort below '-',
    while longer names ('com,examples)', 'com,example-shop)') fall outside.
    """
    from ...normalize import surt_host_prefix

    host = surt_host_prefix(domain)
    return host + ")", host + "-"



def block_containing(blocks: list[Block], key: str) -> Block | None:
    """The block that holds `key`: the last one whose first key is not after it."""
    candidates = [b for b in blocks if b.key <= key]
    return candidates[-1] if candidates else None
