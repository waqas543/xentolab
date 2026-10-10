"""Geofabrik extract discovery (free, public OSM extracts; https://download.geofabrik.de)."""

from __future__ import annotations

from ... import http
from ..base import DiscoveredDataset

INDEX_URL = "https://download.geofabrik.de/index-v1-nogeom.json"


def find_region(index: dict, country: str | None, region: str | None) -> dict | None:
    features = [f["properties"] for f in index.get("features", [])]
    if region:
        return next((f for f in features if f["id"] == region or f.get("urls", {}).get("pbf", "").endswith(
            f"/{region}-latest.osm.pbf")), None)
    matches = [f for f in features if country and country.upper() in (f.get("iso3166-1:alpha2") or [])]
    # Prefer the region that is exactly this one country (not a group like "gcc-states").
    matches.sort(key=lambda f: (len(f.get("iso3166-1:alpha2") or []), "parent" not in f))
    return matches[0] if matches else None


def discover(country: str | None, region: str | None) -> DiscoveredDataset | None:
    index = http.get_json(INDEX_URL)
    feature = find_region(index, country, region)
    if feature is None:
        return None
    url = feature["urls"]["pbf"]
    info = http.head(url)
    md5 = None
    try:
        md5 = http.get_text(url + ".md5").split()[0].strip().lower()
    except Exception:  # checksum file is optional; sha256 is always computed locally
        md5 = None
    return DiscoveredDataset(
        dataset_name=f"geofabrik:{feature['id']}",
        url=url,
        source_version=info.last_modified,
        release_date=info.last_modified,
        size_bytes=info.size_bytes,
        checksum=f"md5:{md5}" if md5 else None,
        country=country,
        region=feature["id"],
        format="osm.pbf",
        metadata={"provider": "geofabrik", "name": feature.get("name"), "updates_url": feature["urls"].get("updates"),
                  "parent": feature.get("parent")},
    )
