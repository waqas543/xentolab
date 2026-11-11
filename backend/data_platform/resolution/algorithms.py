"""Entity-resolution algorithms. Each is versioned; results record the version that produced them.

v1 links *location-level* duplicates: the same real-world business observed twice
(within one source, e.g. an OSM shop node plus its building way, or across sources).
Blocking keeps comparisons local; scoring combines independent signals; a pair
becomes `automatic` only with strong evidence, otherwise `suggested` for review.
"""

from __future__ import annotations

from dataclasses import dataclass

WEIGHTS = {"name": 0.45, "geo": 0.25, "phone": 0.15, "domain": 0.10, "address": 0.05}


@dataclass(frozen=True)
class ResolutionV1:
    version: str = "er-v1"
    max_distance_m: float = 300.0          # geo score reaches 0 here; farther pairs are not location matches
    fuzzy_cell_degrees: float = 0.001      # ~110 m grid for fuzzy-name blocking
    suggest_threshold: float = 0.70

    def candidates_sql(self, places: str) -> str:
        """Pairs (left_id < right_id) with signal scores. `places` is a table/view of named Silver places."""
        cell = self.fuzzy_cell_degrees
        haversine = """2 * 6371008.8 * asin(sqrt(
            pow(sin(radians(b.latitude - a.latitude) / 2), 2) +
            cos(radians(a.latitude)) * cos(radians(b.latitude)) * pow(sin(radians(b.longitude - a.longitude) / 2), 2)))"""
        near = "abs(a.latitude - b.latitude) < 0.01 AND abs(a.longitude - b.longitude) < 0.012"
        return f"""
        WITH p AS (
            SELECT silver_id, name_key, normalized_phone, normalized_domain, domain_is_platform, street, housenumber,
                   category_group, latitude, longitude,
                   CAST(floor(latitude / {cell}) AS BIGINT) AS cy, CAST(floor(longitude / {cell}) AS BIGINT) AS cx
            FROM {places}
        ),
        neighbours AS (
            SELECT p.*, cy + dy AS ny, cx + dx AS nx
            FROM p, (SELECT unnest([-1, 0, 1]) AS dy), (SELECT unnest([-1, 0, 1]) AS dx)
        ),
        pairs AS (
            SELECT a.silver_id AS l, b.silver_id AS r, 'name' AS block
            FROM p a JOIN p b ON a.name_key = b.name_key AND a.silver_id < b.silver_id AND {near}
            UNION
            SELECT a.silver_id, b.silver_id, 'phone'
            FROM p a JOIN p b ON a.normalized_phone = b.normalized_phone AND a.silver_id < b.silver_id AND {near}
            UNION
            SELECT a.silver_id, b.silver_id, 'domain'
            FROM p a JOIN p b ON a.normalized_domain = b.normalized_domain AND a.silver_id < b.silver_id AND {near}
             AND NOT coalesce(a.domain_is_platform, false)
            UNION
            SELECT a.silver_id, b.silver_id, 'fuzzy'
            FROM neighbours a JOIN p b ON a.ny = b.cy AND a.nx = b.cx AND a.silver_id < b.silver_id
            WHERE jaro_winkler_similarity(a.name_key, b.name_key) >= 0.88
        ),
        unique_pairs AS (
            SELECT l, r, list(DISTINCT block ORDER BY block) AS blocks FROM pairs GROUP BY l, r
        ),
        scored AS (
            SELECT u.l AS left_id, u.r AS right_id, u.blocks,
                   {haversine} AS distance_m,
                   jaro_winkler_similarity(a.name_key, b.name_key) AS name_score,
                   CASE WHEN a.normalized_phone IS NULL OR b.normalized_phone IS NULL THEN NULL
                        WHEN a.normalized_phone = b.normalized_phone THEN 1.0 ELSE 0.0 END AS phone_score,
                   CASE WHEN a.normalized_domain IS NULL OR b.normalized_domain IS NULL
                             OR coalesce(a.domain_is_platform, false) OR coalesce(b.domain_is_platform, false) THEN NULL
                        WHEN a.normalized_domain = b.normalized_domain THEN 1.0 ELSE 0.0 END AS domain_score,
                   CASE WHEN a.street IS NULL OR b.street IS NULL THEN NULL
                        WHEN lower(a.street) = lower(b.street)
                             AND coalesce(a.housenumber, '') = coalesce(b.housenumber, '') THEN 1.0 ELSE 0.0 END
                        AS address_score,
                   a.category_group AS left_group, b.category_group AS right_group
            FROM unique_pairs u JOIN p a ON a.silver_id = u.l JOIN p b ON b.silver_id = u.r
        ),
        weighted AS (
            SELECT *, greatest(0.0, 1.0 - distance_m / {self.max_distance_m}) AS geo_score FROM scored
            WHERE distance_m <= {self.max_distance_m}
        ),
        combined AS (
            SELECT *,
                (  {WEIGHTS['name']} * name_score + {WEIGHTS['geo']} * geo_score
                 + {WEIGHTS['phone']} * coalesce(phone_score, 0) + {WEIGHTS['domain']} * coalesce(domain_score, 0)
                 + {WEIGHTS['address']} * coalesce(address_score, 0))
                / ({WEIGHTS['name']} + {WEIGHTS['geo']}
                   + CASE WHEN phone_score IS NULL THEN 0 ELSE {WEIGHTS['phone']} END
                   + CASE WHEN domain_score IS NULL THEN 0 ELSE {WEIGHTS['domain']} END
                   + CASE WHEN address_score IS NULL THEN 0 ELSE {WEIGHTS['address']} END)
                * CASE WHEN left_group = right_group THEN 1.0 ELSE 0.85 END AS match_score
            FROM weighted
        )
        SELECT *,
            CASE
                WHEN coalesce(phone_score, 1) = 0 AND coalesce(domain_score, 1) = 0 THEN 'suggested'
                WHEN name_score >= 0.95 AND distance_m <= 75 AND coalesce(phone_score, 1) > 0 THEN 'automatic'
                WHEN phone_score = 1 AND name_score >= 0.85 AND distance_m <= 150 THEN 'automatic'
                WHEN domain_score = 1 AND name_score >= 0.85 AND distance_m <= 150 THEN 'automatic'
                ELSE 'suggested'
            END AS decision
        FROM combined
        WHERE match_score >= {self.suggest_threshold}
        """


@dataclass(frozen=True)
class ResolutionV2(ResolutionV1):
    """v2: what v1 missed or got wrong on real data.

    - Businesses list several numbers, so two same-name records 0-50 m apart are one place even when their phones
      differ ("Pet Village Animal Hospital" +92310... / +92318..., 0 m) - unless the name is generic ("Brick Kiln",
      "Medical Store": neighbouring kilns are different kilns) or their own websites disagree.
    - A head-office or toll-free number (UAN 111..., 0800..., or one number on many differently named listings) says
      nothing about which branch a record is: it is neither a blocking key nor evidence.
    - A shared direct number plus a name that contains the other ("Meezan Bank" / "Meezan Bank Gulberg") is one place.
    - The same brand (Wikidata id) within 60 m is one place, whatever script the name is in ("Shell" / "شیل").
    - Records on an approximate point (a provider's town-centre default) cannot be compared by distance: they match
      a record in the same city only on name plus phone or website.
    """

    version: str = "er-v2.1"     # 2.1: keys from display names; exact names bypass the score floor
    shared_phone_min_names: int = 3       # one number on this many different names = a shared line
    exact_name_max_m: float = 50.0
    brand_max_m: float = 60.0

    def candidates_sql(self, places: str) -> str:
        cell = self.fuzzy_cell_degrees
        haversine = """2 * 6371008.8 * asin(sqrt(
            pow(sin(radians(b.latitude - a.latitude) / 2), 2) +
            cos(radians(a.latitude)) * cos(radians(b.latitude)) * pow(sin(radians(b.longitude - a.longitude) / 2), 2)))"""
        near = "abs(a.latitude - b.latitude) < 0.01 AND abs(a.longitude - b.longitude) < 0.012"
        both_exact = "a.precise AND b.precise"
        return f"""
        WITH raw AS (
            -- The key of the display name: "شیل" tagged brand Shell and "KFC | E11 Islamabad" key as shell / kfc.
            SELECT silver_id, coalesce(name_key(display_name(name, brand_name)), name_key) AS name_key, normalized_phone, normalized_domain, domain_is_platform, street, housenumber,
                   category_group, latitude, longitude, city_id, country_code,
                   trim(split_part(coalesce(brand_wikidata, ''), ';', 1)) AS brand_qid,
                   coalesce(location_precision, 'exact') = 'exact' AS precise,
                   is_generic_name(name) AS generic_name
            FROM {places}
        ),
        shared_phones AS (
            SELECT normalized_phone FROM raw WHERE normalized_phone IS NOT NULL
            GROUP BY normalized_phone HAVING count(DISTINCT name_key) >= {self.shared_phone_min_names}
        ),
        p AS (
            SELECT r.* EXCLUDE (normalized_phone),
                   CASE WHEN s.normalized_phone IS NULL AND NOT is_hotline(r.normalized_phone, r.country_code)
                        THEN r.normalized_phone END AS normalized_phone,
                   CAST(floor(latitude / {cell}) AS BIGINT) AS cy, CAST(floor(longitude / {cell}) AS BIGINT) AS cx
            FROM raw r LEFT JOIN shared_phones s USING (normalized_phone)
        ),
        neighbours AS (
            SELECT p.*, cy + dy AS ny, cx + dx AS nx
            FROM p, (SELECT unnest([-1, 0, 1]) AS dy), (SELECT unnest([-1, 0, 1]) AS dx)
        ),
        pairs AS (
            SELECT a.silver_id AS l, b.silver_id AS r, 'name' AS block
            FROM p a JOIN p b ON a.name_key = b.name_key AND a.silver_id < b.silver_id AND {near}
            UNION
            SELECT a.silver_id, b.silver_id, 'phone'
            FROM p a JOIN p b ON a.normalized_phone = b.normalized_phone AND a.silver_id < b.silver_id AND {near}
            UNION
            SELECT a.silver_id, b.silver_id, 'domain'
            FROM p a JOIN p b ON a.normalized_domain = b.normalized_domain AND a.silver_id < b.silver_id AND {near}
             AND NOT coalesce(a.domain_is_platform, false)
            UNION
            SELECT a.silver_id, b.silver_id, 'brand'
            FROM p a JOIN p b ON a.brand_qid = b.brand_qid AND a.brand_qid <> '' AND a.silver_id < b.silver_id
             AND {near} AND {both_exact}
            UNION
            SELECT a.silver_id, b.silver_id, 'fuzzy'
            FROM neighbours a JOIN p b ON a.ny = b.cy AND a.nx = b.cx AND a.silver_id < b.silver_id
            WHERE jaro_winkler_similarity(a.name_key, b.name_key) >= 0.88
            UNION
            -- An approximate point is compared within its city, by name.
            SELECT a.silver_id, b.silver_id, 'approximate'
            FROM p a JOIN p b ON a.name_key = b.name_key AND a.city_id = b.city_id AND a.silver_id < b.silver_id
            WHERE NOT ({both_exact}) AND NOT a.generic_name
        ),
        unique_pairs AS (
            SELECT l, r, list(DISTINCT block ORDER BY block) AS blocks FROM pairs GROUP BY l, r
        ),
        scored AS (
            SELECT u.l AS left_id, u.r AS right_id, u.blocks,
                   {haversine} AS distance_m,
                   {both_exact} AS comparable,
                   a.generic_name OR b.generic_name AS generic_name,
                   a.brand_qid <> '' AND a.brand_qid = b.brand_qid AS same_brand,
                   jaro_winkler_similarity(a.name_key, b.name_key) AS name_score,
                   length(a.name_key) >= 4 AND length(b.name_key) >= 4
                       AND (contains(a.name_key, b.name_key) OR contains(b.name_key, a.name_key)) AS name_contained,
                   CASE WHEN a.normalized_phone IS NULL OR b.normalized_phone IS NULL THEN NULL
                        WHEN a.normalized_phone = b.normalized_phone THEN 1.0 ELSE 0.0 END AS phone_score,
                   CASE WHEN a.normalized_domain IS NULL OR b.normalized_domain IS NULL
                             OR coalesce(a.domain_is_platform, false) OR coalesce(b.domain_is_platform, false) THEN NULL
                        WHEN a.normalized_domain = b.normalized_domain THEN 1.0 ELSE 0.0 END AS domain_score,
                   CASE WHEN a.street IS NULL OR b.street IS NULL THEN NULL
                        WHEN lower(a.street) = lower(b.street)
                             AND coalesce(a.housenumber, '') = coalesce(b.housenumber, '') THEN 1.0 ELSE 0.0 END
                        AS address_score,
                   a.category_group AS left_group, b.category_group AS right_group
            FROM unique_pairs u JOIN p a ON a.silver_id = u.l JOIN p b ON b.silver_id = u.r
        ),
        weighted AS (
            SELECT *, CASE WHEN comparable THEN greatest(0.0, 1.0 - distance_m / {self.max_distance_m}) END AS geo_score
            FROM scored
            WHERE NOT comparable OR distance_m <= {self.max_distance_m}
        ),
        combined AS (
            SELECT *,
                (  {WEIGHTS['name']} * name_score + {WEIGHTS['geo']} * coalesce(geo_score, 0)
                 + {WEIGHTS['phone']} * coalesce(phone_score, 0) + {WEIGHTS['domain']} * coalesce(domain_score, 0)
                 + {WEIGHTS['address']} * coalesce(address_score, 0))
                / ({WEIGHTS['name']} + CASE WHEN geo_score IS NULL THEN 0 ELSE {WEIGHTS['geo']} END
                   + CASE WHEN phone_score IS NULL THEN 0 ELSE {WEIGHTS['phone']} END
                   + CASE WHEN domain_score IS NULL THEN 0 ELSE {WEIGHTS['domain']} END
                   + CASE WHEN address_score IS NULL THEN 0 ELSE {WEIGHTS['address']} END)
                * CASE WHEN left_group = right_group THEN 1.0 ELSE 0.85 END AS match_score
            FROM weighted
        )
        SELECT * EXCLUDE (comparable, generic_name, same_brand, name_contained),
            CASE
                WHEN NOT comparable THEN
                    CASE WHEN name_score >= 0.92 AND (phone_score = 1 OR domain_score = 1) THEN 'automatic'
                         ELSE 'suggested' END
                WHEN same_brand AND distance_m <= {self.brand_max_m} AND coalesce(domain_score, 1) > 0 THEN 'automatic'
                WHEN domain_score = 0 AND coalesce(phone_score, 0) < 1
                    THEN 'suggested'                             -- different own websites, nothing else shared
                WHEN name_score >= 0.97 AND distance_m <= {self.exact_name_max_m} AND NOT generic_name THEN 'automatic'
                WHEN name_score >= 0.95 AND distance_m <= 75 AND coalesce(phone_score, 1) > 0 AND NOT generic_name
                    THEN 'automatic'
                WHEN phone_score = 1 AND (name_score >= 0.85 OR name_contained) AND distance_m <= 150 THEN 'automatic'
                WHEN domain_score = 1 AND name_score >= 0.85 AND distance_m <= 150 THEN 'automatic'
                ELSE 'suggested'
            END AS decision
        FROM combined
        WHERE match_score >= {self.suggest_threshold} OR (same_brand AND distance_m <= {self.brand_max_m})
           -- Sources file the same shop under different groups, which lowers the score; an exact name this close
           -- is decided by the rules above, not by the score floor.
           OR (comparable AND name_score >= 0.97 AND distance_m <= {self.exact_name_max_m} AND NOT generic_name)
        """


ALGORITHMS = {"v1": ResolutionV1(), "v2": ResolutionV2()}


@dataclass(frozen=True)
class OrganizationLinkingV1:
    """Links organization records (silver.organizations: Wikidata, registries) to places and to each other.

    place <-> organization, strongest evidence first:
      wikidata_id   the place's brand / wikidata tag is the organization's QID         -> automatic
      domain        the place's own website domain is the organization's               -> automatic if names agree
      name_geo      the organization has coordinates; a same-name place is within 300 m -> automatic if close
      name          exact name (or alias) key, no other evidence, distinctive name      -> suggested
    organization <-> organization (different sources):
      identifier    Wikidata's OpenCorporates ID equals the registry record             -> automatic
      registry_search  the registry record was found by searching this organization's legal name, exact match
      domain        same own website domain                                            -> automatic
      name          same name key                                                      -> suggested
    """

    version: str = "org-v2"                # v2: domain links need agreeing names to be automatic
    max_distance_m: float = 300.0
    domain_name_threshold: float = 0.75
    cell_degrees: float = 0.003            # ~330 m grid for name+proximity blocking
    min_name_key_length: int = 8           # name-only links need a distinctive name ...
    max_places_per_name: int = 25          # ... that is not a common shop name

    def place_links_sql(self, places: str, orgs: str) -> str:
        cell = self.cell_degrees
        haversine = """2 * 6371008.8 * asin(sqrt(
            pow(sin(radians(o.latitude - p.latitude) / 2), 2) +
            cos(radians(p.latitude)) * cos(radians(o.latitude)) * pow(sin(radians(o.longitude - p.longitude) / 2), 2)))"""
        return f"""
        WITH o AS (SELECT * FROM {orgs} WHERE name_key IS NOT NULL),
        p AS (SELECT silver_id, name_key, brand_wikidata, wikidata, normalized_domain, domain_is_platform,
                     latitude, longitude, brand_name FROM {places}),
        place_qids AS (
            SELECT silver_id, trim(q) AS q FROM p, unnest(string_split(coalesce(brand_wikidata, ''), ';')) AS t(q)
            WHERE trim(q) <> ''
            UNION
            SELECT silver_id, trim(q) FROM p, unnest(string_split(coalesce(wikidata, ''), ';')) AS t(q)
            WHERE trim(q) <> ''
        ),
        by_qid AS (
            SELECT pq.silver_id AS place_id, o.silver_id AS org_id, 'wikidata_id' AS method, 1.0 AS match_score,
                   NULL::DOUBLE AS name_score, 1.0 AS domain_score, NULL::DOUBLE AS distance_m
            FROM place_qids pq JOIN o ON o.wikidata = pq.q
        ),
        by_domain AS (
            SELECT p.silver_id AS place_id, o.silver_id AS org_id, 'domain' AS method, 0.95 AS match_score,
                   jaro_winkler_similarity(p.name_key, o.name_key) AS name_score, 1.0 AS domain_score,
                   NULL::DOUBLE AS distance_m
            FROM p JOIN o ON p.normalized_domain = o.normalized_domain
            WHERE NOT coalesce(p.domain_is_platform, false) AND NOT coalesce(o.domain_is_platform, false)
        ),
        og AS (
            SELECT *, CAST(floor(latitude / {cell}) AS BIGINT) AS cy, CAST(floor(longitude / {cell}) AS BIGINT) AS cx
            FROM o WHERE latitude IS NOT NULL
        ),
        og_cells AS (
            SELECT og.*, cy + dy AS ny, cx + dx AS nx
            FROM og, (SELECT unnest([-1, 0, 1]) AS dy), (SELECT unnest([-1, 0, 1]) AS dx)
        ),
        pg AS (
            SELECT *, CAST(floor(latitude / {cell}) AS BIGINT) AS cy, CAST(floor(longitude / {cell}) AS BIGINT) AS cx
            FROM p
        ),
        by_geo AS (
            SELECT p.silver_id AS place_id, o.silver_id AS org_id, 'name_geo' AS method, NULL::DOUBLE AS match_score,
                   jaro_winkler_similarity(p.name_key, o.name_key) AS name_score, NULL::DOUBLE AS domain_score,
                   {haversine} AS distance_m
            FROM og_cells o JOIN pg p ON p.cy = o.ny AND p.cx = o.nx
            WHERE jaro_winkler_similarity(p.name_key, o.name_key) >= 0.88
        ),
        org_keys AS (
            SELECT silver_id, name_key AS key FROM o
            UNION SELECT silver_id, unnest(alias_keys) FROM o WHERE alias_keys IS NOT NULL
        ),
        name_counts AS (SELECT name_key, count(*) AS n FROM p GROUP BY name_key),
        by_name AS (
            SELECT p.silver_id AS place_id, k.silver_id AS org_id, 'name' AS method, 0.72 AS match_score,
                   1.0 AS name_score, NULL::DOUBLE AS domain_score, NULL::DOUBLE AS distance_m
            FROM org_keys k JOIN name_counts c ON c.name_key = k.key AND c.n <= {self.max_places_per_name}
            JOIN p ON p.name_key = k.key
            WHERE length(k.key) >= {self.min_name_key_length}
        ),
        candidates AS (
            SELECT * FROM by_qid UNION ALL SELECT * FROM by_domain UNION ALL
            SELECT * FROM by_geo WHERE distance_m <= {self.max_distance_m} UNION ALL SELECT * FROM by_name
        ),
        ranked AS (
            SELECT *, CASE method WHEN 'wikidata_id' THEN 1 WHEN 'domain' THEN 2 WHEN 'name_geo' THEN 3 ELSE 4 END
                      AS strength
            FROM candidates
        )
        SELECT place_id, org_id, method,
               coalesce(match_score, round(0.5 * name_score + 0.5 * (1 - distance_m / {self.max_distance_m}), 4))
                   AS match_score,
               name_score, domain_score, distance_m,
               CASE WHEN method = 'wikidata_id' THEN 'automatic'
                    -- Portal domains (punjab.gov.pk) are shared by hundreds of unrelated bodies: a domain match
                    -- is automatic only when the names agree too.
                    WHEN method = 'domain' AND name_score >= {self.domain_name_threshold} THEN 'automatic'
                    WHEN method = 'name_geo' AND name_score >= 0.95 AND distance_m <= 150 THEN 'automatic'
                    ELSE 'suggested' END AS decision
        FROM ranked
        QUALIFY row_number() OVER (PARTITION BY place_id, org_id ORDER BY strength, distance_m NULLS LAST) = 1
        """

    def org_links_sql(self, orgs: str) -> str:
        return f"""
        WITH o AS (SELECT * FROM {orgs} WHERE name_key IS NOT NULL),
        by_identifier AS (
            SELECT w.silver_id AS a, r.silver_id AS b, 'identifier' AS method, 1.0 AS match_score
            FROM o w JOIN o r ON r.registry_id IS NOT NULL
             AND json_extract_string(w.identifiers, '$.opencorporates') = r.registry_id AND w.source <> r.source
        ),
        by_search AS (
            SELECT w.silver_id AS a, r.silver_id AS b, 'registry_search' AS method, 0.95 AS match_score
            FROM o r JOIN o w ON json_extract_string(r.attributes, '$.query_origin') = 'wikidata:' || w.wikidata
            WHERE r.source_confidence >= 1.0 AND r.source <> w.source
        ),
        by_domain AS (
            SELECT a.silver_id AS a, b.silver_id AS b, 'domain' AS method, 0.9 AS match_score
            FROM o a JOIN o b ON a.normalized_domain = b.normalized_domain AND a.source < b.source
            WHERE NOT coalesce(a.domain_is_platform, false)
        ),
        by_name AS (
            SELECT a.silver_id AS a, b.silver_id AS b, 'name' AS method, 0.72 AS match_score
            FROM o a JOIN o b ON a.name_key = b.name_key AND a.source < b.source
            WHERE length(a.name_key) >= 6
        ),
        candidates AS (
            SELECT * FROM by_identifier UNION ALL SELECT * FROM by_search UNION ALL SELECT * FROM by_domain
            UNION ALL SELECT * FROM by_name
        )
        SELECT least(a, b) AS left_id, greatest(a, b) AS right_id, method, match_score,
               CASE WHEN method = 'name' THEN 'suggested' ELSE 'automatic' END AS decision
        FROM candidates
        QUALIFY row_number() OVER (PARTITION BY least(a, b), greatest(a, b) ORDER BY match_score DESC) = 1
        """


ORGANIZATION_LINKING = OrganizationLinkingV1()
