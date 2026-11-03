"""Example plugin: marham.pk (healthcare marketplace).

Its homepage is huge (doctor listings), so instead of hoping the about/contact links are among the first
ones found, seed them directly and skip link discovery.
"""

from .base import Site


class Marham(Site):
    name = "marham"
    domains = ("marham.pk",)
    max_pages = 3

    def seeds(self, domain):
        return [(f"https://www.{domain}/", "home"), (f"https://www.{domain}/about", "about"),
                (f"https://www.{domain}/contact", "contact")]

    def follow(self, page_url, hrefs, domain, kind):
        return []
