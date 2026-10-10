"""Builds tiny but realistic .osm.pbf extracts for tests (no downloads in CI).

Testland (ISO PK) contains a province and a district, a city and a town, and a set
of features that exercise every Silver/Gold rule: a shop mapped twice (node + building
way), a two-branch bank chain, a platform-only website, an unnamed shop, excluded
amenities, a way whose nodes are missing (rejected) and a relation-based place.
"""

from datetime import datetime, timezone
from pathlib import Path

import osmium
from osmium.osm.mutable import Node, Relation, Way

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _square(first_id: int, west: float, south: float, east: float, north: float):
    corners = [(west, south), (east, south), (east, north), (west, north)]
    nodes = [Node(id=first_id + i, location=c, version=1, timestamp=T0, tags={}) for i, c in enumerate(corners)]
    refs = [first_id, first_id + 1, first_id + 2, first_id + 3, first_id]
    return nodes, refs


def write_fixture(path: Path, variant: int = 1) -> Path:
    header = osmium.io.Header()
    header.set("osmosis_replication_timestamp", "2026-10-01T00:00:00Z" if variant == 1 else "2026-10-07T00:00:00Z")
    header.set("osmosis_replication_sequence_number", "4900" if variant == 1 else "4918")
    header.set("osmosis_replication_base_url", "https://download.geofabrik.de/asia/pakistan-updates")

    nodes, ways, relations = [], [], []

    # Boundaries: country, province, district (each a closed way + relation).
    for rel_id, way_id, first_node, box, tags in [
        (1, 1, 1, (70, 28, 76, 34), {"boundary": "administrative", "admin_level": "2", "name": "Testland",
                                     "ISO3166-1:alpha2": "PK", "type": "boundary"}),
        (2, 2, 11, (72, 30, 76, 34), {"boundary": "administrative", "admin_level": "4", "name": "پنجاب",
                                      "name:en": "Punjab", "type": "boundary"}),
        (3, 3, 21, (73, 31, 75, 33), {"boundary": "administrative", "admin_level": "6", "name": "Lahore District",
                                      "type": "boundary"}),
    ]:
        square_nodes, refs = _square(first_node, *box)
        nodes += square_nodes
        ways.append(Way(id=way_id, nodes=refs, version=1, timestamp=T0, tags={}))
        relations.append(Relation(id=rel_id, members=[("w", way_id, "outer")], version=1, timestamp=T0, tags=tags))

    def node(node_id, lat, lon, tags, version=1):
        nodes.append(Node(id=node_id, location=(lon, lat), version=version, timestamp=T0, tags=tags))

    node(50, 32.0, 74.0, {"place": "city", "name": "Lahore", "population": "11126285"})
    node(51, 31.2, 74.5, {"place": "town", "name": "Kasur"})

    node(101, 32.0010, 74.0010, {"amenity": "restaurant", "name": "Cafe Aylanto", "website": "www.aylanto.com",
                                 "phone": "042 35781769", "cuisine": "italian"})
    node(102, 32.0100, 74.0100, {"amenity": "bank", "name": "HBL Bank", "brand": "HBL", "brand:wikidata": "Q1"})
    node(103, 32.2000, 74.3000, {"amenity": "bank", "name": "HBL", "brand": "HBL", "brand:wikidata": "Q1"})
    node(104, 32.0300, 74.0300, {"shop": "bakery"})                                  # unnamed
    node(105, 32.0310, 74.0310, {"shop": "vacant", "name": "Empty Unit"})             # excluded
    node(106, 32.0320, 74.0320, {"amenity": "place_of_worship", "name": "Masjid"})   # excluded
    phone = "042 111 945 227" if variant == 1 else "042 111 000 111"
    node(107, 32.0400, 74.0400, {"office": "company", "name": "Systems (Pvt.) Limited",
                                 "website": "https://www.systems-ltd.com", "email": "info@systems-ltd.com",
                                 "phone": phone, "addr:street": "Main Blvd", "addr:housenumber": "10"},
         version=1 if variant == 1 else 2)
    if variant == 1:
        node(108, 32.0500, 74.0500, {"shop": "clothes", "name": "Khaadi",
                                     "website": "https://www.facebook.com/khaadi"})  # platform url only
    node(109, 40.0, 80.0, {"shop": "supermarket", "name": "Outside Mart"})            # outside all boundaries
    node(110, 31.2010, 74.5010, {"amenity": "pharmacy", "name": "Fazal Din Pharma", "phone": "0300 1234567"})
    if variant == 2:
        node(111, 32.0600, 74.0600, {"amenity": "cafe", "name": "New Cafe"})

    # The restaurant's building, mapped again as a way (same business -> one Gold record).
    building_nodes, building_refs = _square(501, 74.00105, 32.00105, 74.00125, 32.00125)
    nodes += building_nodes
    ways.append(Way(id=201, nodes=building_refs, version=1, timestamp=T0,
                    tags={"building": "yes", "amenity": "restaurant", "name": "Cafe Aylanto"}))
    # A mall whose nodes are outside the extract -> rejected (missing coordinates).
    ways.append(Way(id=202, nodes=[9991, 9992, 9993], version=1, timestamp=T0, tags={"shop": "mall", "name": "Ghost Mall"}))
    # A university mapped as a multipolygon relation.
    campus_nodes, campus_refs = _square(511, 74.2, 32.1, 74.21, 32.11)
    nodes += campus_nodes
    ways.append(Way(id=203, nodes=campus_refs, version=1, timestamp=T0, tags={}))
    relations.append(Relation(id=301, members=[("w", 203, "outer")], version=1, timestamp=T0,
                              tags={"type": "multipolygon", "amenity": "university", "name": "Test University"}))

    path.unlink(missing_ok=True)
    writer = osmium.SimpleWriter(str(path), header=header)
    try:
        for item in sorted(nodes, key=lambda n: n.id):
            writer.add_node(item)
        for item in sorted(ways, key=lambda w: w.id):
            writer.add_way(item)
        for item in sorted(relations, key=lambda r: r.id):
            writer.add_relation(item)
    finally:
        writer.close()
    return path
