"""Geographic normalization shared by all sources.

`GeoIndex` assigns country / admin1 / admin2 from boundary polygons and city /
locality from populated places. Boundaries and places are Silver datasets
(silver.geo_boundaries from OSM; silver.geo_places from GeoNames, else OSM). Where no
boundary polygon covers a point, the nearest gazetteer place supplies the admin names.
Work is vectorized (Shapely 2 STRtree, NumPy), and callers feed it in batches.

A city "reaches" as far as its population suggests, but never across a first-level boundary: a shop in
Islamabad Capital Territory is not in Rawalpindi (Punjab), however close Rawalpindi's centre is, and Hub
(Balochistan) is not Karachi (Sindh). A small first-level unit named after its city (a capital territory)
belongs to that city entirely.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely

EARTH_RADIUS_KM = 6371.0088
ADMIN_LEVELS = {2: "country", 4: "admin1", 6: "admin2"}
GAZETTEER_ADMIN_MAX_KM = 25.0     # nearest gazetteer place must be this close to lend its admin names
MATRIX_CELLS = 4_000_000          # points x cities per chunk (bounds memory)
TERRITORY_MAX_KM2 = 3_000.0       # a first-level unit this small, named after a city inside it, is that city
KM_PER_DEGREE = 111.32


@dataclass(frozen=True)
class Boundary:
    level: int
    name: str
    country_code: str | None
    geometry: shapely.Geometry


@dataclass(frozen=True)
class Place:
    name: str
    kind: str                       # city | town | village | suburb
    latitude: float
    longitude: float
    population: int | None
    country_code: str | None = None  # gazetteer admin names (GeoNames), used when no boundary covers a point
    admin1: str | None = None
    admin2: str | None = None
    place_id: str | None = None      # stable gazetteer id: same-named towns stay apart

    @property
    def radius_km(self) -> float:
        """How far a city 'reaches': scales with population (Karachi ~35 km, small cities 6 km)."""
        return float(min(35.0, max(6.0, np.sqrt(self.population or 0) / 100.0)))


def polygon_from_rings(lines: list[list[tuple[float, float]]]) -> shapely.Geometry | None:
    """Assemble an area from (possibly unordered, multi-part) boundary ways. Holes come from inner rings."""
    linestrings = [shapely.linestrings(coords) for coords in lines if len(coords) >= 2]
    if not linestrings:
        return None
    area = shapely.build_area(shapely.GeometryCollection(linestrings))
    if area is None or area.is_empty:
        return None
    area = shapely.make_valid(area)
    polygons = shapely.get_parts(area)
    polygons = [p for p in polygons if p.geom_type in ("Polygon", "MultiPolygon")]
    if not polygons:
        return None
    return shapely.multipolygons(polygons) if len(polygons) > 1 else polygons[0]


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


class GeoIndex:
    def __init__(self, boundaries: list[Boundary], places: list[Place]):
        self.levels: dict[int, tuple[shapely.STRtree, list[Boundary]]] = {}
        for level in ADMIN_LEVELS:
            items = [b for b in boundaries if b.level == level]
            if items:
                self.levels[level] = (shapely.STRtree([b.geometry for b in items]), items)
        self.places = places
        self.cities = [p for p in places if p.kind == "city"]
        # Nearest-place lookups use a spatial tree: gazetteers have 100k+ villages, far too many for a matrix.
        self._place_tree = shapely.STRtree(shapely.points([p.longitude for p in places], [p.latitude for p in places])) \
            if places else None
        self._place_lat = np.array([p.latitude for p in places])
        self._place_lon = np.array([p.longitude for p in places])
        self._city_lat = np.array([p.latitude for p in self.cities])
        self._city_lon = np.array([p.longitude for p in self.cities])
        self._city_radius = np.array([p.radius_km for p in self.cities])
        # First-level unit of every city centre, by polygon (consistent naming with the points it is compared to).
        city_points = shapely.points(self._city_lon, self._city_lat) if self.cities else None
        city_admin1 = self._containing(4, city_points) if self.cities else []
        self.city_admin1 = [b.name if b else p.admin1 for b, p in zip(city_admin1, self.cities)]
        self._admin1_codes: dict[str, int] = {}
        self._city_admin1_code = np.array([self._admin1_code(name) for name in self.city_admin1], dtype=np.int64)
        self.territories = self._territories(city_admin1)
        self._cities_by_name: dict[str, int] = {}
        for index, place in sorted(enumerate(self.places), key=lambda item: (
                {"city": 0, "town": 1}.get(item[1].kind, 2), -(item[1].population or 0))):
            if place.kind in ("city", "town"):
                self._cities_by_name.setdefault(place.name.casefold(), index)

    def _admin1_code(self, name: str | None) -> int:
        if not name:
            return -1
        return self._admin1_codes.setdefault(name, len(self._admin1_codes))

    def _territories(self, city_admin1: list[Boundary | None]) -> dict[str, int]:
        """admin1 name -> city index, for small first-level units named after a city inside them."""
        result: dict[str, int] = {}
        for index, boundary in enumerate(city_admin1):
            if boundary is None:
                continue
            centroid = boundary.geometry.centroid
            area_km2 = boundary.geometry.area * KM_PER_DEGREE ** 2 * float(np.cos(np.radians(centroid.y)))
            city = self.cities[index]
            if area_km2 <= TERRITORY_MAX_KM2 and city.name.casefold() in boundary.name.casefold().split(" ") \
                    and (boundary.name not in result or (city.population or 0) > (self.cities[result[boundary.name]].population or 0)):
                result[boundary.name] = index
        return result

    def city_by_name(self, name: str | None) -> dict | None:
        """The most important city or town with this name (a source's stated city), with its admin names."""
        if not name:
            return None
        index = self._cities_by_name.get(name.strip().casefold())
        if index is None:
            return None
        place = self.places[index]
        city_index = next((i for i, c in enumerate(self.cities) if c is place), None)
        admin1 = self.city_admin1[city_index] if city_index is not None else place.admin1
        return {"city": place.name, "city_id": place.place_id, "admin1": admin1, "admin2": place.admin2,
                "country_code": place.country_code}

    def _containing(self, level: int, points) -> list[Boundary | None]:
        result: list[Boundary | None] = [None] * len(points)
        if level not in self.levels:
            return result
        tree, items = self.levels[level]
        point_idx, tree_idx = tree.query(points, predicate="within")
        for p, t in zip(point_idx, tree_idx):
            current = result[p]
            # Overlapping polygons (disputed areas): prefer the smaller, more specific one.
            if current is None or items[t].geometry.area < current.geometry.area:
                result[p] = items[t]
        return result

    def _nearest_places(self, points, lat, lon) -> tuple[np.ndarray, np.ndarray]:
        """Index of the nearest populated place for every point, and its distance in km."""
        point_idx, tree_idx = self._place_tree.query_nearest(points, all_matches=False)
        nearest = np.zeros(len(points), dtype=np.int64)
        nearest[point_idx] = tree_idx
        distance = haversine_km(lat, lon, self._place_lat[nearest], self._place_lon[nearest])
        return nearest, distance

    def assign(self, latitudes, longitudes) -> dict[str, list]:
        lat = np.asarray(latitudes, dtype=float)
        lon = np.asarray(longitudes, dtype=float)
        n = len(lat)
        out: dict[str, list] = {"country_code": [None] * n, "admin1": [None] * n, "admin2": [None] * n,
                                "city": [None] * n, "city_id": [None] * n, "city_match": [None] * n,
                                "locality": [None] * n, "locality_id": [None] * n, "locality_distance_km": [None] * n}
        if n == 0:
            return out
        points = shapely.points(lon, lat)

        countries = self._containing(2, points)
        admin1 = self._containing(4, points)
        admin2 = self._containing(6, points)
        for i in range(n):
            if countries[i]:
                out["country_code"][i] = countries[i].country_code
            if admin1[i]:
                out["admin1"][i] = admin1[i].name
                out["country_code"][i] = out["country_code"][i] or admin1[i].country_code
            if admin2[i]:
                out["admin2"][i] = admin2[i].name

        if not self.places:
            return out
        nearest, distance = self._nearest_places(points, lat, lon)
        for i in range(n):
            place = self.places[nearest[i]]
            out["locality"][i] = place.name
            out["locality_id"][i] = place.place_id
            out["locality_distance_km"][i] = round(float(distance[i]), 3)
            if distance[i] <= GAZETTEER_ADMIN_MAX_KM:
                # Boundary polygons win; the gazetteer fills what they do not cover.
                if out["admin1"][i] is None and place.admin1:
                    out["admin1"][i], out["admin2"][i] = place.admin1, out["admin2"][i] or place.admin2
                if out["country_code"][i] is None and place.country_code:
                    out["country_code"][i] = place.country_code

        if self.cities:
            point_admin1 = np.array([self._admin1_codes.get(a, -2) if a else -1 for a in out["admin1"]],
                                    dtype=np.int64)
            chunk = max(1, MATRIX_CELLS // len(self.cities))
            for start in range(0, n, chunk):
                stop = min(n, start + chunk)
                dc = haversine_km(lat[start:stop, None], lon[start:stop, None], self._city_lat[None, :],
                                  self._city_lon[None, :])
                relative = dc / self._city_radius[None, :]
                # A city never reaches across a first-level boundary (when both sides are known).
                rows = point_admin1[start:stop, None]
                cols = self._city_admin1_code[None, :]
                relative[(rows != -1) & (cols != -1) & (rows != cols)] = np.inf
                best = relative.argmin(axis=1)
                for offset, city_index in enumerate(best):
                    if relative[offset, city_index] <= 1.0:
                        i = start + offset
                        out["city"][i] = self.cities[city_index].name
                        out["city_id"][i] = self.cities[city_index].place_id
                        out["city_match"][i] = "city"
        for i in range(n):
            territory = self.territories.get(out["admin1"][i]) if out["admin1"][i] else None
            if territory is not None:
                out["city"][i] = self.cities[territory].name
                out["city_id"][i] = self.cities[territory].place_id
                out["city_match"][i] = "territory"
            elif out["city"][i] is None and out["locality"][i] is not None:
                out["city"][i] = out["locality"][i]
                out["city_id"][i] = out["locality_id"][i]
                out["city_match"][i] = "locality"
        return out
