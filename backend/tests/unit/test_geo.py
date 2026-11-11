"""City assignment respects first-level boundaries; capital territories belong to their city."""

import shapely

from data_platform.geo import Boundary, GeoIndex, Place


def box(x0, y0, x1, y1):
    return shapely.box(x0, y0, x1, y1)


def index():
    # Two provinces side by side, and a small territory carved out of the east one around its capital.
    west = Boundary(4, "West", "XX", box(0.0, 0.0, 1.0, 1.0))
    east = Boundary(4, "East", "XX", shapely.difference(box(1.0, 0.0, 2.0, 1.0), box(1.0, 0.5, 1.2, 0.7)))
    capital = Boundary(4, "Capital City Territory", "XX", box(1.0, 0.5, 1.2, 0.7))
    country = Boundary(2, "Testland", "XX", box(0.0, 0.0, 2.0, 1.0))
    places = [
        Place("Bigtown", "city", 0.6, 0.98, 4_000_000, place_id="g:1"),     # west, reaches ~20 km
        Place("Capital", "city", 0.6, 1.1, 500_000, place_id="g:2"),        # in the territory, reaches ~7 km
        Place("Eastville", "city", 0.2, 1.8, 100_000, place_id="g:3"),
        Place("Smallvillage", "village", 0.55, 1.15, None, place_id="g:4"),
        Place("Capital", "village", 0.1, 0.1, None, place_id="g:5"),         # a village with the capital's name
    ]
    return GeoIndex([west, east, capital, country], places)


def test_a_city_never_reaches_across_a_first_level_boundary():
    geo = index()
    # 2 km east of Bigtown's centre, but across the border inside the territory: the capital, not Bigtown.
    out = geo.assign([0.6], [1.03])
    assert out["admin1"] == ["Capital City Territory"]
    assert out["city"] == ["Capital"] and out["city_id"] == ["g:2"]


def test_a_capital_territory_belongs_to_its_city_everywhere():
    geo = index()
    out = geo.assign([0.68], [1.19])                       # ~10 km from the centre, beyond its radius
    assert out["city"] == ["Capital"] and out["city_match"] == ["territory"]


def test_ids_keep_same_named_places_apart_and_stated_cities_resolve():
    geo = index()
    out = geo.assign([0.1], [0.1])
    assert out["city"] == ["Capital"] and out["city_id"] == ["g:5"] and out["city_match"] == ["locality"]
    assert geo.city_by_name("capital")["city_id"] == "g:2"
