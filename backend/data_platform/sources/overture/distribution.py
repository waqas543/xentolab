"""Overture Maps release access via the official STAC catalog (https://stac.overturemaps.org).

Overture publishes GeoParquet in public, anonymous buckets. We only *read* from there
(HTTPS range requests); everything we keep is stored locally. A release is located
through the catalog, the country's bounding box through the divisions theme, and then
only the row groups whose bbox statistics intersect it are fetched — a country is a
small fraction of the 80M+ global places.
"""

from __future__ import annotations

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.parse import urljoin

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from ... import http
from ...errors import PermanentError, TransientError

DEFAULT_STAC_URL = "https://stac.overturemaps.org/catalog.json"
BBOX_MARGIN_DEGREES = 1.0
READ_THREADS = 8


def stac_url() -> str:
    return os.environ.get("OVERTURE_STAC_URL", DEFAULT_STAC_URL)


def _fetch_json(url: str) -> dict:
    if url.startswith("file://"):
        with urllib.request.urlopen(url) as response:
            return json.load(response)
    return http.get_json(url)


def _child(catalog: dict, base: str, predicate: Callable[[dict], bool]) -> tuple[dict, str] | None:
    for link in catalog.get("links", []):
        if link.get("rel") == "child" and predicate(link):
            url = urljoin(base, link["href"])
            return _fetch_json(url), url
    return None


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    catalog: dict


def get_release(version: str | None = None) -> Release:
    root_url = stac_url()
    root = _fetch_json(root_url)
    version = version or root.get("latest")
    if not version:
        raise PermanentError("The Overture catalog does not name a latest release")
    found = _child(root, root_url, lambda l: f"/{version}/" in l.get("href", "")
                   or (l.get("title") or "").split(" ")[0] == version)
    if found is None:
        raise PermanentError(f"Overture release {version} is not in the catalog")
    catalog, url = found
    return Release(version, url, catalog)


def collection(release: Release, theme: str, type_: str) -> tuple[dict, str]:
    theme_found = _child(release.catalog, release.url, lambda l: l.get("title") == theme or f"/{theme}/" in l["href"])
    if theme_found is None:
        raise PermanentError(f"Theme {theme} missing from release {release.version}")
    theme_catalog, theme_url = theme_found
    found = _child(theme_catalog, theme_url, lambda l: l.get("title") == type_ or f"/{type_}/" in l["href"])
    if found is None:
        raise PermanentError(f"Type {theme}/{type_} missing from release {release.version}")
    return found


def items(collection_json: dict, collection_url: str) -> list[dict]:
    urls = [urljoin(collection_url, l["href"]) for l in collection_json.get("links", []) if l.get("rel") == "item"]
    with ThreadPoolExecutor(READ_THREADS) as executor:
        return list(executor.map(_fetch_json, urls))


def file_location(item: dict) -> tuple[pafs.FileSystem, str]:
    """Where to read an item's Parquet: anonymous S3 for the public release, local files for fixtures."""
    for asset in item.get("assets", {}).values():
        href = asset.get("href", "")
        s3 = (asset.get("alternate") or {}).get("s3", {}).get("href")
        if s3 and s3.startswith("s3://"):
            region = (item.get("properties", {}).get("storage:schemes", {}).get("aws", {}) or {}).get("region", "us-west-2")
            return pafs.S3FileSystem(anonymous=True, region=region), s3[len("s3://"):]
        if href.startswith("file://"):
            return pafs.LocalFileSystem(), href[len("file://"):]
        if href.startswith("/"):
            return pafs.LocalFileSystem(), href
    raise PermanentError(f"No readable asset for item {item.get('id')}")


def _network(error: Exception) -> Exception:
    if isinstance(error, OSError):
        return TransientError(f"Reading Overture data failed: {error}")
    return error


def country_bbox(release: Release, country: str) -> list[float]:
    """[west, south, east, north] of a country from Overture's divisions (land + territorial areas)."""
    division_collection, url = collection(release, "divisions", "division_area")
    paths = []
    filesystem = None
    for item in items(division_collection, url):
        filesystem, path = file_location(item)
        paths.append(path)
    try:
        table = ds.dataset(paths, filesystem=filesystem, format="parquet").to_table(
            columns=["bbox"], filter=(ds.field("subtype") == "country") & (ds.field("country") == country.upper()))
    except OSError as error:
        raise _network(error) from error
    if table.num_rows == 0:
        raise PermanentError(f"Overture has no country division for {country}")
    boxes = table.column("bbox").to_pylist()
    return [min(b["xmin"] for b in boxes), min(b["ymin"] for b in boxes),
            max(b["xmax"] for b in boxes), max(b["ymax"] for b in boxes)]


def intersects(a: list[float], b: list[float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


@dataclass(frozen=True)
class RowGroupTask:
    item_id: str
    filesystem: pafs.FileSystem
    path: str
    row_group: int
    rows: int


def plan_reads(place_items: list[dict], bbox: list[float]) -> list[RowGroupTask]:
    """Row groups whose bbox statistics intersect the (padded) country bbox."""
    west, south, east, north = bbox
    tasks: list[RowGroupTask] = []
    for item in place_items:
        if item.get("bbox") and not intersects(item["bbox"], bbox):
            continue
        filesystem, path = file_location(item)
        try:
            metadata = pq.ParquetFile(path, filesystem=filesystem).metadata
        except OSError as error:
            raise _network(error) from error
        names = [metadata.schema.column(i).path for i in range(metadata.num_columns)]
        try:
            index = {n: names.index(f"bbox.{n}") for n in ("xmin", "xmax", "ymin", "ymax")}
        except ValueError:
            raise PermanentError("Overture places no longer carry bbox statistics; the adapter needs updating") from None
        for group in range(metadata.num_row_groups):
            stats = {n: metadata.row_group(group).column(i).statistics for n, i in index.items()}
            if any(s is None or not s.has_min_max for s in stats.values()):
                keep = True                                     # no statistics: read it to be safe
            else:
                keep = not (stats["xmax"].min > east or stats["xmin"].max < west
                            or stats["ymax"].min > north or stats["ymin"].max < south)
            if keep:
                tasks.append(RowGroupTask(item["id"], filesystem, path, group, metadata.row_group(group).num_rows))
    return tasks


def read_country(tasks: list[RowGroupTask], country: str,
                 on_progress: Callable[[int, int, int], None]) -> Iterator[pa.Table]:
    """Yield, per row group, the rows whose first address is in `country`. Reads run in parallel."""

    def read(task: RowGroupTask) -> tuple[pa.Table, int]:
        try:
            table = pq.ParquetFile(task.path, filesystem=task.filesystem).read_row_group(task.row_group)
        except OSError as error:
            raise _network(error) from error
        if "addresses" not in table.column_names:
            raise PermanentError("Overture places have no `addresses` column; the adapter needs updating")
        mask = pc.fill_null(pc.equal(_country_column(table), country.upper()), False)
        return table.filter(mask), table.num_rows

    done = scanned = 0
    executor = ThreadPoolExecutor(READ_THREADS)
    try:
        for table, rows in executor.map(read, tasks):
            done += 1
            scanned += rows
            on_progress(done, len(tasks), scanned)          # may raise RunCancelled
            yield table
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _country_column(table: pa.Table) -> pa.Array:
    addresses = table.column("addresses").combine_chunks()
    # Rows without addresses yield null (never matched); list_element would fail on empty lists.
    lengths = pc.fill_null(pc.list_value_length(addresses), 0)
    safe = pc.if_else(pc.greater(lengths, 0), addresses, pa.nulls(len(addresses), addresses.type))
    return pc.struct_field(pc.list_element(safe, 0), "country")
