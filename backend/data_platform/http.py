"""HTTP helpers for adapters: error classification and resumable, hashed downloads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

import requests

from .config import get_settings
from .errors import PermanentError, TransientError

TIMEOUT = (15, 60)  # connect, read
CHUNK = 1024 * 1024


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = get_settings().http_user_agent
    return session


def _raise_for_status(response: requests.Response, url: str) -> None:
    if response.status_code < 400:
        return
    context = {"url": url, "status": response.status_code}
    if response.status_code in (408, 425, 429) or response.status_code >= 500:
        raise TransientError(f"HTTP {response.status_code} from {url}", context=context)
    raise PermanentError(f"HTTP {response.status_code} from {url}", context=context)


def _wrap_network(error: requests.RequestException, url: str):
    return TransientError(f"Network error for {url}: {type(error).__name__}: {error}", context={"url": url})


def get_text(url: str) -> str:
    try:
        response = _session().get(url, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise _wrap_network(error, url) from error
    _raise_for_status(response, url)
    return response.text


def get_json(url: str):
    try:
        response = _session().get(url, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise _wrap_network(error, url) from error
    _raise_for_status(response, url)
    return response.json()


@dataclass
class RemoteFileInfo:
    size_bytes: int | None
    last_modified: str | None
    etag: str | None


def head(url: str) -> RemoteFileInfo:
    try:
        response = _session().head(url, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as error:
        raise _wrap_network(error, url) from error
    _raise_for_status(response, url)
    modified = response.headers.get("Last-Modified")
    return RemoteFileInfo(
        size_bytes=int(response.headers["Content-Length"]) if "Content-Length" in response.headers else None,
        last_modified=parsedate_to_datetime(modified).isoformat() if modified else None,
        etag=response.headers.get("ETag"),
    )


@dataclass
class DownloadResult:
    size_bytes: int
    sha256: str
    md5: str
    resumed_from: int


def download_to(url: str, part_path: Path, *, on_progress: Callable[[int, int | None], None] | None = None) -> DownloadResult:
    """Download `url` into `part_path`, resuming a previous partial download when the server allows it.

    Hashes are computed over the complete file (including any resumed prefix).
    """
    part_path.parent.mkdir(parents=True, exist_ok=True)
    sha256, md5 = hashlib.sha256(), hashlib.md5()
    existing = part_path.stat().st_size if part_path.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}

    try:
        response = _session().get(url, stream=True, timeout=TIMEOUT, headers=headers)
    except requests.RequestException as error:
        raise _wrap_network(error, url) from error

    with response:
        if response.status_code == 416:      # already complete
            response.close()
            resumed = existing
        else:
            _raise_for_status(response, url)
            resumed = existing if response.status_code == 206 else 0
        total = None
        if response.status_code == 206 and "Content-Range" in response.headers:
            total = int(response.headers["Content-Range"].rsplit("/", 1)[-1])
        elif "Content-Length" in response.headers and response.status_code == 200:
            total = int(response.headers["Content-Length"])
        elif response.status_code == 416:
            total = existing

        if resumed:
            with open(part_path, "rb") as handle:
                while chunk := handle.read(CHUNK):
                    sha256.update(chunk)
                    md5.update(chunk)

        written = resumed
        if response.status_code != 416:
            mode = "ab" if resumed else "wb"
            try:
                with open(part_path, mode) as handle:
                    for chunk in response.iter_content(CHUNK):
                        handle.write(chunk)
                        sha256.update(chunk)
                        md5.update(chunk)
                        written += len(chunk)
                        if on_progress:
                            on_progress(written, total)
            except requests.RequestException as error:
                raise _wrap_network(error, url) from error

    if total is not None and written != total:
        raise TransientError(f"Incomplete download: {written} of {total} bytes", context={"url": url})
    return DownloadResult(size_bytes=written, sha256=sha256.hexdigest(), md5=md5.hexdigest(), resumed_from=resumed)


# ---------------------------------------------------------------- API-style access (many small requests)

class Pacer:
    """At most one request per `interval` seconds across threads (provider politeness)."""

    def __init__(self, interval: float):
        import threading

        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        import time

        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


def _redacted(url: str, secret_params: tuple[str, ...]) -> str:
    """URL safe for logs and errors: secret query parameters are masked."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    if not parts.query:
        return url
    query = [(k, "***" if k in secret_params else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def request(method: str, url: str, *, params: dict | None = None, data: dict | None = None,
            headers: dict | None = None, attempts: int = 4, pacer: Pacer | None = None,
            secret_params: tuple[str, ...] = (), timeout=TIMEOUT, session: requests.Session | None = None,
            should_cancel: Callable[[], None] | None = None, retry_rate_limited: bool = True) -> requests.Response:
    """One API call with in-request retries for transient failures (429/5xx/network), honouring Retry-After.

    Permanent failures (other 4xx) raise immediately. Secrets passed in `params` never appear in errors.
    `retry_rate_limited=False` raises on the first 429 (for quota-metered APIs: a 429 means stop).
    """
    import random
    import time

    session = session or _session()
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if should_cancel:
            should_cancel()
        if pacer:
            pacer.wait()
        try:
            response = session.request(method, url, params=params, data=data, headers=headers, timeout=timeout)
        except requests.RequestException as error:
            last_error = TransientError(f"Network error for {_redacted(url, secret_params)}: {type(error).__name__}",
                                        context={"url": _redacted(url, secret_params)})
            retry_after = None
        else:
            safe_url = _redacted(response.url, secret_params)
            if response.status_code < 400:
                return response
            context = {"url": safe_url, "status": response.status_code}
            if response.status_code in (408, 425, 429) or response.status_code >= 500:
                last_error = TransientError(f"HTTP {response.status_code} from {safe_url}", context=context)
                if response.status_code == 429 and not retry_rate_limited:
                    raise last_error
                header = response.headers.get("Retry-After", "")
                retry_after = float(header) if header.replace(".", "", 1).isdigit() else None
            else:
                raise PermanentError(f"HTTP {response.status_code} from {safe_url}", context=context)
        if attempt < attempts:
            delay = retry_after if retry_after is not None else min(60.0, 2 ** attempt) * random.uniform(0.5, 1.0)
            time.sleep(min(delay, 120.0))
    raise last_error  # type: ignore[misc]


def get_range(url: str, start: int, length: int, *, attempts: int = 4, pacer: Pacer | None = None,
              session: requests.Session | None = None) -> bytes:
    """Bytes [start, start + length) of a remote file (HTTP Range), verified to be complete."""
    response = request("GET", url, headers={"Range": f"bytes={start}-{start + length - 1}"}, attempts=attempts,
                       pacer=pacer, session=session)
    body = response.content
    if response.status_code == 200 and len(body) > length:     # server ignored Range: slice locally
        body = body[start:start + length]
    if len(body) != length:
        raise TransientError(f"Short range read from {url}: {len(body)} of {length} bytes", context={"url": url})
    return body


def new_session() -> requests.Session:
    """A session for one stage (connection reuse across many requests)."""
    return _session()
