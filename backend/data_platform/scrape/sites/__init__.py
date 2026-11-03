"""Site plugin registry. Every module in this package is imported and its Site subclasses registered.

    site_for("tribune.com.pk")  -> the plugin that lists the domain, else the default Site
    get("tribune")              -> a plugin by name (what messages carry)
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import cache

from .base import Site

__all__ = ["Site", "get", "site_for", "all_sites"]


@cache
def all_sites() -> dict[str, Site]:
    sites: dict[str, Site] = {"default": Site()}
    for module in pkgutil.iter_modules(__path__):
        if module.name == "base":
            continue
        mod = importlib.import_module(f"{__name__}.{module.name}")
        for value in vars(mod).values():
            if isinstance(value, type) and issubclass(value, Site) and value is not Site and value.domains:
                site = value()
                if site.name in sites:
                    raise ValueError(f"duplicate site plugin name {site.name!r}")
                sites[site.name] = site
    return sites


@cache
def _by_domain() -> dict[str, Site]:
    mapping: dict[str, Site] = {}
    for site in all_sites().values():
        for domain in site.domains:
            if domain in mapping:
                raise ValueError(f"{domain} is claimed by both {mapping[domain].name} and {site.name}")
            mapping[domain] = site
    return mapping


def site_for(domain: str) -> Site:
    return _by_domain().get(domain.lower(), all_sites()["default"])


def get(name: str) -> Site:
    return all_sites().get(name) or all_sites()["default"]
