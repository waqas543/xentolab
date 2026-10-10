"""Build the analysis layer (schema osm_app) on top of the raw OSM import.

Creates:
    osm_app.admin_areas   country / province / district polygons (PostGIS)
    osm_app.companies     one row per named business feature (node, way or relation)
    osm_app.saved_queries / osm_app.query_history   storage for the query runner

Safe to re-run: companies are built into a staging table and swapped in.
"""

import argparse
import time

import psycopg

# Tag keys that mark a feature as a business. Amenity values listed in
# EXCLUDED_AMENITIES are public infrastructure rather than companies.
BUSINESS_KEYS = ["shop", "office", "craft", "healthcare", "industrial", "company"]

EXCLUDED_AMENITIES = [
    "place_of_worship", "grave_yard", "parking", "parking_space", "parking_entrance",
    "drinking_water", "toilets", "bench", "waste_basket", "waste_disposal", "recycling",
    "police", "townhall", "courthouse", "fire_station", "shelter", "bus_station",
    "community_centre", "social_facility", "library", "public_bath", "fountain", "atm",
    "water_point", "watering_place", "clock", "post_box", "telephone", "prison",
    "ranger_station", "border_control", "bicycle_parking", "motorcycle_parking",
    "taxi", "ferry_terminal", "public_building", "hunting_stand", "vending_machine",
    "shower", "mortuary", "refugee_site", "grit_bin", "letter_box", "loading_dock",
    "bus_stop", "polling_station", "dressing_room", "give_box", "funeral_hall",
]

BUSINESS_TOURISM = [
    "hotel", "motel", "guest_house", "hostel", "apartment", "resort", "chalet", "camp_site",
]

# The first key present decides the category (key, value) of a feature.
CATEGORY_KEYS = [
    "shop", "office", "craft", "healthcare", "amenity", "tourism", "industrial", "man_made", "company",
]

BUSINESS_FILTER = """
    tags ? 'name' AND (
        tags ?| %(business_keys)s
        OR (tags ? 'amenity' AND NOT (tags->>'amenity' = ANY(%(excluded_amenities)s)))
        OR tags->>'tourism' = ANY(%(business_tourism)s)
        OR tags->>'man_made' = 'works'
    )
    AND COALESCE(tags->>'shop', '') <> 'vacant'
"""

CATEGORY_GROUP_SQL = """
    CASE
        WHEN tags->>'shop' IN ('car', 'car_repair', 'car_parts', 'tyres', 'motorcycle',
                               'motorcycle_repair', 'truck', 'auto_parts')
          OR tags->>'amenity' IN ('fuel', 'car_wash', 'car_rental', 'charging_station',
                                  'vehicle_inspection', 'motorcycle_rental')
            THEN 'Automotive'
        WHEN tags->>'amenity' IN ('restaurant', 'fast_food', 'cafe', 'ice_cream', 'food_court',
                                  'bar', 'pub', 'biergarten')
            THEN 'Food & Drink'
        WHEN tags->>'amenity' IN ('bank', 'bureau_de_change', 'money_transfer', 'payment_centre')
          OR tags->>'office' IN ('financial', 'insurance', 'accountant', 'tax_advisor',
                                 'financial_advisor')
            THEN 'Finance'
        WHEN tags ? 'healthcare'
          OR tags->>'amenity' IN ('hospital', 'clinic', 'doctors', 'dentist', 'pharmacy',
                                  'veterinary', 'nursing_home')
          OR tags->>'shop' IN ('chemist', 'medical_supply', 'optician', 'hearing_aids')
            THEN 'Health'
        WHEN tags->>'amenity' IN ('school', 'college', 'university', 'kindergarten', 'childcare',
                                  'driving_school', 'language_school', 'training',
                                  'music_school', 'prep_school')
          OR tags->>'office' = 'educational_institution'
            THEN 'Education'
        WHEN tags ? 'tourism'
            THEN 'Hospitality'
        WHEN tags->>'office' IN ('government', 'ngo', 'diplomatic', 'association', 'religion',
                                 'political_party', 'union', 'foundation', 'charity')
            THEN 'Public & Non-profit'
        WHEN tags ? 'office' THEN 'Professional Services'
        WHEN tags ? 'shop' THEN 'Retail'
        WHEN tags ?| array['craft', 'industrial'] OR tags->>'man_made' = 'works'
            THEN 'Industry & Craft'
        ELSE 'Other Services'
    END
"""


def category_key_sql():
    cases = "\n".join(f"WHEN tags ? '{key}' THEN '{key}'" for key in CATEGORY_KEYS)
    return f"CASE {cases} END"


def run(connection, label, sql, params=None):
    started = time.monotonic()
    print(f"{label}...", end=" ", flush=True)

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rowcount = cursor.rowcount

    elapsed = time.monotonic() - started
    suffix = f" ({rowcount:,} rows)" if rowcount and rowcount > 0 else ""
    print(f"done in {elapsed:.1f}s{suffix}")


def build_admin_areas(connection):
    run(connection, "Creating admin_areas", """
        DROP TABLE IF EXISTS osm_app.admin_areas;
        CREATE TABLE osm_app.admin_areas (
            id          BIGINT PRIMARY KEY,
            admin_level SMALLINT NOT NULL,
            name        TEXT NOT NULL,
            name_local  TEXT,
            geom        geometry(MultiPolygon, 4326) NOT NULL
        );
    """)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT id
            FROM osm_raw.relations
            WHERE tags->>'boundary' = 'administrative'
              AND tags->>'admin_level' IN ('2', '4', '6')
        """)
        relation_ids = [row[0] for row in cursor.fetchall()]

    built = 0
    skipped = 0

    for relation_id in relation_ids:
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("""
                        WITH lines AS (
                            SELECT ST_MakeLine(
                                       ST_MakePoint(n.longitude, n.latitude)
                                       ORDER BY wn.sequence
                                   ) AS geom
                            FROM osm_raw.relation_members m
                            JOIN osm_raw.way_nodes wn ON wn.way_id = m.member_id
                            JOIN osm_raw.nodes n ON n.id = wn.node_id
                            WHERE m.relation_id = %(id)s AND m.member_type = 'w'
                            GROUP BY m.member_id
                        ), area AS (
                            SELECT ST_Multi(ST_CollectionExtract(
                                       ST_MakeValid(ST_BuildArea(ST_Collect(geom))), 3
                                   )) AS geom
                            FROM lines
                        )
                        INSERT INTO osm_app.admin_areas (id, admin_level, name, name_local, geom)
                        SELECT r.id,
                               (r.tags->>'admin_level')::smallint,
                               COALESCE(r.tags->>'name:en', r.tags->>'name'),
                               r.tags->>'name',
                               ST_SetSRID(area.geom, 4326)
                        FROM osm_raw.relations r, area
                        WHERE r.id = %(id)s
                          AND area.geom IS NOT NULL
                          AND NOT ST_IsEmpty(area.geom)
                    """, {"id": relation_id})

                    if cursor.rowcount:
                        built += 1
                    else:
                        skipped += 1
        except psycopg.Error:
            skipped += 1

    run(connection, "Indexing admin_areas", """
        CREATE INDEX admin_areas_geom_idx ON osm_app.admin_areas USING gist (geom);
        CREATE INDEX admin_areas_level_idx ON osm_app.admin_areas (admin_level);
        ANALYZE osm_app.admin_areas;
    """)

    connection.commit()
    print(f"Admin areas: {built} built, {skipped} skipped (incomplete outside the extract)")


def build_companies(connection):
    params = {
        "business_keys": BUSINESS_KEYS,
        "excluded_amenities": EXCLUDED_AMENITIES,
        "business_tourism": BUSINESS_TOURISM,
    }

    run(connection, "Dropping old candidates", "DROP TABLE IF EXISTS pg_temp.candidates")
    run(connection, "Collecting candidate features", f"""
        CREATE TEMP TABLE candidates AS
        SELECT 'n'::char(1) AS osm_type, id AS osm_id, version, changeset, user_name,
               timestamp, tags,
               ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) AS geom
        FROM osm_raw.nodes
        WHERE {BUSINESS_FILTER}

        UNION ALL

        SELECT 'w', w.id, w.version, w.changeset, w.user_name, w.timestamp, w.tags,
               (SELECT ST_SetSRID(ST_Centroid(ST_Collect(ST_MakePoint(n.longitude, n.latitude))), 4326)
                FROM osm_raw.way_nodes wn
                JOIN osm_raw.nodes n ON n.id = wn.node_id
                WHERE wn.way_id = w.id)
        FROM osm_raw.ways w
        WHERE {BUSINESS_FILTER.replace("tags", "w.tags")}

        UNION ALL

        SELECT 'r', r.id, r.version, r.changeset, r.user_name, r.timestamp, r.tags,
               (SELECT ST_SetSRID(ST_Centroid(ST_Collect(ST_MakePoint(n.longitude, n.latitude))), 4326)
                FROM osm_raw.relation_members m
                LEFT JOIN osm_raw.way_nodes wn ON m.member_type = 'w' AND wn.way_id = m.member_id
                JOIN osm_raw.nodes n ON n.id = CASE WHEN m.member_type = 'n' THEN m.member_id
                                                    ELSE wn.node_id END
                WHERE m.relation_id = r.id)
        FROM osm_raw.relations r
        WHERE {BUSINESS_FILTER.replace("tags", "r.tags")}
    """, params)

    run(connection, "Collecting cities and towns", """
        DROP TABLE IF EXISTS pg_temp.places;
        CREATE TEMP TABLE places AS
        SELECT COALESCE(tags->>'name:en', tags->>'name') AS name,
               tags->>'place' AS place,
               -- Reach of a city scales with its population (Karachi ~35 km, small cities 6 km)
               LEAST(35, GREATEST(6, SQRT(COALESCE(NULLIF(
                   REGEXP_REPLACE(tags->>'population', '[^0-9]', '', 'g'), '')::bigint, 0)) / 100)
               ) AS radius_km,
               ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) AS geom
        FROM osm_raw.nodes
        WHERE tags->>'place' IN ('city', 'town') AND tags ? 'name';
        CREATE INDEX ON places USING gist (geom);
        ANALYZE places;
    """)

    run(connection, "Building companies", f"""
        DROP TABLE IF EXISTS osm_app.companies_new;
        CREATE TABLE osm_app.companies_new AS
        WITH base AS (
            SELECT c.*,
                   {category_key_sql()} AS category_key,
                   {CATEGORY_GROUP_SQL} AS category_group,
                   NULLIF(TRIM(COALESCE(tags->>'phone', tags->>'contact:phone',
                                        tags->>'mobile', tags->>'contact:mobile')), '') AS phone,
                   NULLIF(TRIM(COALESCE(tags->>'website', tags->>'contact:website',
                                        tags->>'url')), '') AS website,
                   NULLIF(TRIM(COALESCE(tags->>'email', tags->>'contact:email')), '') AS email,
                   NULLIF(TRIM(tags->>'opening_hours'), '') AS opening_hours,
                   NULLIF(TRIM(tags->>'addr:street'), '') AS addr_street,
                   NULLIF(TRIM(tags->>'addr:housenumber'), '') AS addr_housenumber,
                   NULLIF(TRIM(tags->>'addr:postcode'), '') AS addr_postcode,
                   NULLIF(INITCAP(TRIM(tags->>'addr:city')), '') AS addr_city
            FROM candidates c
            WHERE c.geom IS NOT NULL
        )
        SELECT b.osm_type,
               b.osm_id,
               COALESCE(NULLIF(TRIM(tags->>'name:en'), ''), TRIM(tags->>'name')) AS name,
               TRIM(tags->>'name') AS name_local,
               b.category_group,
               b.category_key,
               -- e.g. shop=yes means "a shop of unknown type"
               CASE WHEN tags->>b.category_key = 'yes' THEN 'unspecified_' || b.category_key
                    ELSE tags->>b.category_key END AS category,
               NULLIF(TRIM(COALESCE(tags->>'brand:en', tags->>'brand')), '') AS brand,
               NULLIF(TRIM(tags->>'brand:wikidata'), '') AS brand_wikidata,
               NULLIF(TRIM(COALESCE(tags->>'operator:en', tags->>'operator')), '') AS operator,
               b.phone, b.website, b.email, b.opening_hours,
               b.addr_street, b.addr_housenumber, b.addr_postcode, b.addr_city,
               country.name AS country,
               province.name AS province,
               COALESCE(district.name, CASE WHEN province.name LIKE 'Islamabad%%' THEN 'Islamabad' END)
                   AS district,
               CASE WHEN province.name LIKE 'Islamabad%%' THEN 'Islamabad'
                    ELSE COALESCE(big_city.name, locality.name) END AS city,
               locality.name AS locality,
               ROUND((ST_DistanceSphere(b.geom, locality.geom) / 1000)::numeric, 2)
                   AS locality_distance_km,
               ST_Y(b.geom) AS latitude,
               ST_X(b.geom) AS longitude,
               b.phone IS NOT NULL AS has_phone,
               b.website IS NOT NULL AS has_website,
               b.email IS NOT NULL AS has_email,
               (b.addr_street IS NOT NULL OR b.addr_housenumber IS NOT NULL) AS has_address,
               b.opening_hours IS NOT NULL AS has_opening_hours,
               (  (b.phone IS NOT NULL)::int
                + (b.website IS NOT NULL)::int
                + (b.email IS NOT NULL)::int
                + (b.addr_street IS NOT NULL OR b.addr_housenumber IS NOT NULL)::int
                + (b.opening_hours IS NOT NULL)::int
               ) * 20 AS completeness,
               b.version,
               b.changeset,
               b.user_name AS last_editor,
               b.timestamp AS last_edited,
               b.tags,
               b.geom
        FROM base b
        LEFT JOIN LATERAL (
            SELECT name FROM osm_app.admin_areas a
            WHERE a.admin_level = 2 AND ST_Contains(a.geom, b.geom) LIMIT 1
        ) country ON true
        LEFT JOIN LATERAL (
            SELECT name FROM osm_app.admin_areas a
            WHERE a.admin_level = 4 AND ST_Contains(a.geom, b.geom) LIMIT 1
        ) province ON true
        LEFT JOIN LATERAL (
            SELECT name FROM osm_app.admin_areas a
            WHERE a.admin_level = 6 AND ST_Contains(a.geom, b.geom) LIMIT 1
        ) district ON true
        LEFT JOIN LATERAL (
            SELECT p.name, p.geom FROM places p ORDER BY p.geom <-> b.geom LIMIT 1
        ) locality ON true
        LEFT JOIN LATERAL (
            SELECT nearest.name
            FROM (
                SELECT p.name, p.radius_km, ST_DistanceSphere(p.geom, b.geom) / 1000 AS distance_km
                FROM places p
                WHERE p.place = 'city'
                ORDER BY p.geom <-> b.geom
                LIMIT 3
            ) nearest
            WHERE nearest.distance_km <= nearest.radius_km
            ORDER BY nearest.distance_km
            LIMIT 1
        ) big_city ON true;
    """)

    # The same brand is often tagged in several scripts (e.g. Urdu and English); use the
    # most common Latin-script spelling for every feature sharing a brand:wikidata id.
    run(connection, "Canonicalizing brand names", """
        UPDATE osm_app.companies_new c
        SET brand = canonical.brand
        FROM (
            SELECT DISTINCT ON (brand_wikidata) brand_wikidata, brand
            FROM osm_app.companies_new
            WHERE brand_wikidata IS NOT NULL AND brand IS NOT NULL
            GROUP BY brand_wikidata, brand
            ORDER BY brand_wikidata, (brand ~ '[A-Za-z]') DESC, count(*) DESC, brand
        ) canonical
        WHERE c.brand_wikidata = canonical.brand_wikidata
          AND c.brand IS DISTINCT FROM canonical.brand
    """)

    run(connection, "Indexing companies", """
        ALTER TABLE osm_app.companies_new ADD PRIMARY KEY (osm_type, osm_id);
        CREATE INDEX ON osm_app.companies_new USING gist (geom);
        CREATE INDEX ON osm_app.companies_new (category_group, category);
        CREATE INDEX ON osm_app.companies_new (province, district);
        CREATE INDEX ON osm_app.companies_new (city);
        CREATE INDEX ON osm_app.companies_new (locality);
        CREATE INDEX ON osm_app.companies_new (brand) WHERE brand IS NOT NULL;
        CREATE INDEX ON osm_app.companies_new USING gin (name gin_trgm_ops);
        CREATE INDEX ON osm_app.companies_new USING gin (name_local gin_trgm_ops);
        CREATE INDEX ON osm_app.companies_new USING gin (tags jsonb_path_ops);
        DROP TABLE IF EXISTS osm_app.companies;
        ALTER TABLE osm_app.companies_new RENAME TO companies;
        ANALYZE osm_app.companies;
    """)

    connection.commit()


def build_query_runner_tables(connection):
    run(connection, "Creating query runner tables", """
        CREATE TABLE IF NOT EXISTS osm_app.saved_queries (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            sql         TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS osm_app.query_history (
            id          BIGSERIAL PRIMARY KEY,
            sql         TEXT NOT NULL,
            read_only   BOOLEAN NOT NULL,
            status      TEXT NOT NULL,
            row_count   INTEGER,
            duration_ms INTEGER,
            error       TEXT,
            ran_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS query_history_ran_at_idx
            ON osm_app.query_history (ran_at DESC);
    """)

    connection.commit()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dsn",
        default="dbname=ds_companies",
    )
    parser.add_argument(
        "--skip-admin-areas",
        action="store_true",
        help="Reuse the existing osm_app.admin_areas table",
    )

    args = parser.parse_args()

    with psycopg.connect(args.dsn) as connection:
        run(connection, "Preparing schema", """
            CREATE EXTENSION IF NOT EXISTS postgis;
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            CREATE SCHEMA IF NOT EXISTS osm_app;
        """)
        connection.commit()

        if not args.skip_admin_areas:
            build_admin_areas(connection)

        build_companies(connection)
        build_query_runner_tables(connection)

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT category_group, count(*)
                FROM osm_app.companies
                GROUP BY 1
                ORDER BY 2 DESC
            """)

            print()
            print("Companies by group")

            for group, count in cursor.fetchall():
                print(f"  {group:<24} {count:>8,}")


if __name__ == "__main__":
    main()
