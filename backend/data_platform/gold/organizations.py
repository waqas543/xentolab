"""Gold organizations: brands and companies, merging what locations imply with what registries and
knowledge bases state, plus news signals.

An organization key comes from, in order: the brand's Wikidata id; a linked organization record
(Wikidata / registry, via entity resolution); the brand name; a website domain shared by 2+
locations. Organization records that link to no location still become organizations
(location_count = 0): knowledge-base and registry discovery.
"""

from __future__ import annotations

import pyarrow as pa

from .. import db

ORG_SOURCES = ("wikidata", "opencorporates")
LINK_STRENGTH = {"wikidata_id": 1, "domain": 2, "name_geo": 3, "name": 4}


class UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:                 # path compression
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Smallest id as root: the anchor (and Gold id) does not depend on edge order.
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def load_organization_links(country: str) -> list[dict]:
    with db.serving() as conn:
        return conn.execute("""
            SELECT left_id, right_id, match_score, evidence->>'method' AS method
            FROM er.entity_matches
            WHERE country_code = %s AND match_type = 'organization' AND status IN ('automatic', 'accepted')
        """, [country]).fetchall()


def cluster_records(con, links: list[dict]) -> dict:
    """Cluster organization records (org<->org links) and attach places to clusters (place<->org links).

    Registers DuckDB tables `org_cluster` (silver_id, anchor, org_key, link_method) and
    `place_org` (place_id, org_key, method, strength). Returns counts.
    """
    records = con.execute("SELECT silver_id, wikidata FROM orgrec").fetchall()
    record_ids = {r[0] for r in records}
    qids = {r[0]: r[1] for r in records}
    union = UnionFind()
    for silver_id in record_ids:
        union.find(silver_id)
    org_methods: dict[str, str] = {}
    place_links = []
    for link in links:
        left_is_org, right_is_org = link["left_id"] in record_ids, link["right_id"] in record_ids
        if left_is_org and right_is_org:
            union.union(link["left_id"], link["right_id"])
            for side in ("left_id", "right_id"):
                org_methods.setdefault(link[side], link["method"])
        elif left_is_org or right_is_org:
            org, place = (link["left_id"], link["right_id"]) if left_is_org else (link["right_id"], link["left_id"])
            place_links.append((place, org, link["method"]))

    anchors = {silver_id: union.find(silver_id) for silver_id in record_ids}
    cluster_qid: dict[str, str] = {}
    for silver_id, anchor in anchors.items():
        if qids[silver_id] and (anchor not in cluster_qid or qids[silver_id] < cluster_qid[anchor]):
            cluster_qid[anchor] = qids[silver_id]
    key_of = {anchor: (f"wikidata:{cluster_qid[anchor]}" if anchor in cluster_qid else f"org:{anchor}")
              for anchor in set(anchors.values())}
    place_methods: dict[str, str] = {}
    for place, org, method in place_links:
        if LINK_STRENGTH.get(method, 9) < LINK_STRENGTH.get(place_methods.get(org, ""), 10):
            place_methods[org] = method
    con.register("org_cluster", pa.table({
        "silver_id": list(anchors), "anchor": list(anchors.values()),
        "org_key": [key_of[a] for a in anchors.values()],
        "link_method": [place_methods.get(s) or org_methods.get(s) or "standalone" for s in anchors],
    }, schema=pa.schema([("silver_id", pa.string()), ("anchor", pa.string()), ("org_key", pa.string()),
                         ("link_method", pa.string())])))
    con.register("place_org", pa.table({
        "place_id": [p for p, _, _ in place_links],
        "org_key": [key_of[anchors[o]] for _, o, _ in place_links],
        "method": [m for _, _, m in place_links],
        "strength": [LINK_STRENGTH.get(m, 9) for _, _, m in place_links],
    }, schema=pa.schema([("place_id", pa.string()), ("org_key", pa.string()), ("method", pa.string()),
                         ("strength", pa.int32())])))
    return {"organization_records": len(record_ids), "record_clusters": len(key_of),
            "place_links": len(place_links)}


def assign_business_organizations(con) -> None:
    """Set businesses.organization_id (and wikidata_id for locations that are Wikidata items themselves)."""
    con.execute("""
        CREATE TEMP TABLE business_links AS
        SELECT gold_id, best.k AS org_key, best.m AS method FROM (
            -- Strongest link wins; ties go to the smallest key, so the choice is deterministic.
            SELECT m.gold_id, list_sort(list(struct_pack(s := po.strength, k := po.org_key, m := po.method)))[1] AS best
            FROM members m JOIN place_org po ON po.place_id = m.silver_id
            GROUP BY m.gold_id
        )
    """)
    # Canonical keys: one brand is often tagged three ways (brand Wikidata id on some branches, brand name only
    # on others, a shared website on the rest). A brand name maps to the Wikidata id its other branches carry,
    # and a shared domain maps to the brand / organization that most of its locations belong to.
    con.execute("""
        CREATE TEMP TABLE brand_qids AS
        SELECT lower(trim(brand_name)) AS brand, mode(trim(split_part(brand_wikidata, ';', 1))) AS qid
        FROM businesses WHERE brand_name IS NOT NULL AND brand_wikidata IS NOT NULL GROUP BY 1
    """)
    con.execute("""
        CREATE TEMP TABLE first_keys AS
        SELECT b.gold_id, b.domain, lower(b.name) AS name,
               CASE WHEN b.brand_wikidata IS NOT NULL
                        THEN 'wikidata:' || trim(split_part(b.brand_wikidata, ';', 1))
                    WHEN l.org_key IS NOT NULL THEN l.org_key
                    WHEN q.qid IS NOT NULL THEN 'wikidata:' || q.qid
                    WHEN b.brand_name IS NOT NULL THEN 'brand:' || lower(trim(b.brand_name)) END AS org_key,
               l.method AS link_method, l.org_key AS linked_key
        FROM businesses b LEFT JOIN business_links l USING (gold_id)
        LEFT JOIN brand_qids q ON q.brand = lower(trim(b.brand_name))
    """)
    # A website shared by locations groups them only when their names agree with the domain's main name:
    # a chain's branches share bankalfalah.com, but hundreds of unrelated offices share punjab.gov.pk.
    con.execute("""
        CREATE TEMP TABLE org_keys AS
        WITH domain_owner AS (
                 SELECT domain, mode(org_key) AS org_key, mode(name) AS owner_name FROM first_keys
                 WHERE domain IS NOT NULL AND org_key IS NOT NULL GROUP BY domain
             ),
             domain_name AS (SELECT domain, mode(name) AS main_name FROM first_keys WHERE domain IS NOT NULL GROUP BY domain),
             agreeing AS (
                 SELECT k.gold_id, k.domain FROM first_keys k JOIN domain_name n USING (domain)
                 WHERE jaro_winkler_similarity(k.name, n.main_name) >= 0.75
             ),
             domain_use AS (SELECT domain, count(*) AS n FROM agreeing GROUP BY domain)
        SELECT k.gold_id,
               coalesce(k.org_key,
                        CASE WHEN jaro_winkler_similarity(k.name, o.owner_name) >= 0.75 THEN o.org_key END,
                        CASE WHEN a.gold_id IS NOT NULL AND d.n >= 2 THEN 'domain:' || k.domain END) AS org_key,
               k.link_method, k.linked_key
        FROM first_keys k LEFT JOIN domain_owner o USING (domain) LEFT JOIN agreeing a USING (gold_id)
        LEFT JOIN domain_use d ON d.domain = k.domain
    """)
    con.execute("""
        UPDATE businesses SET organization_id = 'org_' || substr(sha256(k.org_key), 1, 16)
        FROM org_keys k WHERE k.gold_id = businesses.gold_id AND k.org_key IS NOT NULL
    """)
    # A location linked to a Wikidata item by name and position *is* that item (e.g. a school).
    con.execute("""
        UPDATE businesses SET wikidata_id = coalesce(businesses.wikidata_id, substr(k.linked_key, 10))
        FROM org_keys k
        WHERE k.gold_id = businesses.gold_id AND k.link_method IN ('name_geo', 'name')
          AND k.linked_key LIKE 'wikidata:%'
    """)


def build_organizations(con, country: str) -> None:
    """Create temp tables `organizations` and `organization_members` from businesses + records + mentions."""
    # Registry records found by searching a Gold organization's own name join that organization.
    con.execute("""
        CREATE TEMP TABLE cluster_ids AS
        WITH searched AS (
            SELECT c.anchor, any_value(substr(json_extract_string(r.attributes, '$.query_origin'), 6)) AS origin_org
            FROM orgrec r JOIN org_cluster c USING (silver_id)
            WHERE r.source = 'opencorporates' AND r.source_confidence >= 1.0
              AND json_extract_string(r.attributes, '$.query_origin') LIKE 'gold:%'
            GROUP BY c.anchor
        ),
        existing AS (SELECT DISTINCT organization_id FROM businesses WHERE organization_id IS NOT NULL)
        SELECT DISTINCT c.anchor, c.org_key,
               CASE WHEN c.org_key LIKE 'org:%' AND e.organization_id IS NOT NULL THEN e.organization_id
                    ELSE 'org_' || substr(sha256(c.org_key), 1, 16) END AS organization_id
        FROM org_cluster c LEFT JOIN searched s USING (anchor)
        LEFT JOIN existing e ON e.organization_id = s.origin_org
    """)
    con.execute("""
        CREATE TEMP TABLE organization_members AS
        SELECT i.organization_id, c.link_method, r.*
        FROM orgrec r JOIN org_cluster c USING (silver_id) JOIN cluster_ids i USING (anchor)
    """)
    con.execute("""
        CREATE TEMP TABLE from_locations AS
        SELECT organization_id,
               -- Prefer a Latin-script spelling (brands are often tagged in several scripts).
               arg_max(coalesce(brand_name, name),
                       regexp_matches(coalesce(brand_name, name), '^[\\x20-\\x7E]+$')::INT * 1000 + completeness) AS name,
               any_value(brand_wikidata) AS brand_wikidata, mode(domain) AS domain,
               mode(category_group) AS category_group, count(*)::INTEGER AS location_count,
               count(DISTINCT city)::INTEGER AS city_count,
               list_sort(list_distinct(flatten(list(sources)))) AS location_sources,
               list(DISTINCT brand_name) FILTER (WHERE brand_name IS NOT NULL) AS brand_names
        FROM businesses WHERE organization_id IS NOT NULL GROUP BY organization_id
    """)
    con.execute("""
        CREATE TEMP TABLE from_records AS
        SELECT organization_id,
               arg_max(name, (source = 'wikidata')::INT * 10 + coalesce(employees, 0) / 1e9)
                   FILTER (WHERE source = 'wikidata') AS wikidata_name,
               arg_max(name, coalesce(source_confidence, 1)) AS any_name,
               arg_max(legal_name, (source = 'opencorporates')::INT * 10 + coalesce(source_confidence, 1))
                   FILTER (WHERE legal_name IS NOT NULL) AS legal_name,
               any_value(description) FILTER (WHERE description IS NOT NULL) AS description,
               list_distinct(flatten(list(coalesce(industries, []::VARCHAR[])))) AS industries,
               arg_max(website, (source = 'wikidata')::INT) FILTER (WHERE website IS NOT NULL) AS website,
               arg_max(normalized_domain, (source = 'wikidata')::INT)
                   FILTER (WHERE normalized_domain IS NOT NULL AND NOT coalesce(domain_is_platform, false))
                   AS domain,
               any_value(headquarters) FILTER (WHERE headquarters IS NOT NULL) AS headquarters,
               arg_max(inception, (source = 'opencorporates')::INT) FILTER (WHERE inception IS NOT NULL) AS inception,
               max(employees) AS employees, any_value(parent_name) AS parent_name,
               min(wikidata) AS wikidata_id,
               any_value(registry_id) FILTER (WHERE source = 'opencorporates') AS registry_id,
               any_value(jurisdiction) FILTER (WHERE source = 'opencorporates') AS jurisdiction,
               any_value(status) FILTER (WHERE source = 'opencorporates') AS registry_status,
               arg_max(identifiers, (source = 'wikidata')::INT) AS identifiers,
               list_distinct(flatten(list(coalesce(aliases, []::VARCHAR[]))))[1:10] AS aliases,
               mode(category_group) AS category_group, any_value(entity_type) AS entity_type,
               list(DISTINCT source ORDER BY source) AS record_sources, count(*)::INTEGER AS records,
               list_distinct(flatten(list(coalesce(alias_keys, []::VARCHAR[]))) || list(name_key)) AS keys
        FROM organization_members GROUP BY organization_id
    """)
    con.execute("""
        CREATE TEMP TABLE org_name_keys AS
        SELECT DISTINCT organization_id, key FROM (
            SELECT organization_id, name_key(name) AS key FROM from_locations
            UNION ALL SELECT organization_id, name_key(b) FROM from_locations, unnest(brand_names) AS t(b)
            UNION ALL SELECT organization_id, k FROM from_records, unnest(keys) AS t(k)
        ) WHERE key IS NOT NULL AND length(key) >= 4
    """)
    # News is matched by name, which is only safe for organizations with an identity beyond a local name:
    # a Wikidata item, a registry record, or a chain (3+ locations). A two-branch restaurant called
    # "White House" must not inherit the US White House's coverage.
    con.execute("""
        CREATE TEMP TABLE org_news AS
        WITH eligible AS (
            SELECT organization_id FROM from_locations WHERE brand_wikidata IS NOT NULL OR location_count >= 3
            UNION SELECT organization_id FROM from_records WHERE wikidata_id IS NOT NULL OR registry_id IS NOT NULL
        )
        SELECT k.organization_id, sum(m.mentions)::BIGINT AS news_mentions, sum(m.articles)::BIGINT AS news_articles,
               max(m.last_seen) AS news_last_seen,
               round(sum(m.avg_tone * m.mentions) / sum(m.mentions), 3) AS news_tone
        FROM org_name_keys k JOIN eligible USING (organization_id) JOIN mentions m ON m.name_key = k.key
        GROUP BY k.organization_id
    """)
    con.execute(f"""
        CREATE TEMP TABLE organizations AS
        SELECT organization_id, '{country}' AS country_code,
               coalesce(r.wikidata_name, l.name, r.any_name) AS name,
               r.legal_name, coalesce(l.brand_wikidata, r.wikidata_id) AS brand_wikidata, r.wikidata_id,
               coalesce(l.domain, r.domain) AS domain, r.website,
               coalesce(l.category_group, r.category_group) AS category_group,
               coalesce(l.location_count, 0) AS location_count, coalesce(l.city_count, 0) AS city_count,
               list_sort(list_distinct(coalesce(l.location_sources, []::VARCHAR[])
                                       || coalesce(r.record_sources, []::VARCHAR[])
                                       || CASE WHEN n.news_mentions > 0 THEN ['gdelt'] ELSE []::VARCHAR[] END))
                   AS sources,
               r.description, array_to_string(r.industries[1:3], ', ') AS industry, r.headquarters, r.inception,
               r.employees, r.parent_name, r.registry_id, r.jurisdiction, r.registry_status,
               coalesce(r.identifiers, '{{}}') AS identifiers, r.aliases,
               coalesce(r.records, 0) AS linked_records,
               coalesce(n.news_mentions, 0) AS news_mentions, coalesce(n.news_articles, 0) AS news_articles,
               n.news_last_seen, n.news_tone,
               json_object('name', CASE WHEN r.wikidata_name IS NOT NULL THEN 'wikidata'
                                        WHEN l.name IS NOT NULL THEN 'locations' ELSE r.record_sources[1] END,
                           'legal_name', CASE WHEN r.legal_name IS NOT NULL
                                              THEN CASE WHEN r.registry_id IS NOT NULL THEN 'opencorporates'
                                                        ELSE 'wikidata' END END,
                           'domain', CASE WHEN l.domain IS NOT NULL THEN 'locations'
                                          WHEN r.domain IS NOT NULL THEN 'wikidata' END,
                           'description', CASE WHEN r.description IS NOT NULL THEN 'wikidata' END,
                           'registry', CASE WHEN r.registry_id IS NOT NULL THEN 'opencorporates' END,
                           'news', CASE WHEN n.news_mentions > 0 THEN 'gdelt' END)::VARCHAR AS field_sources
        FROM from_locations l FULL OUTER JOIN from_records r USING (organization_id)
        LEFT JOIN org_news n USING (organization_id)
        WHERE coalesce(r.wikidata_name, l.name, r.any_name) IS NOT NULL
    """)
    # Which non-location sources describe each business (through its organization or its domain).
    con.execute("""
        UPDATE businesses SET enriched_by = e.sources
        FROM (
            SELECT b.gold_id,
                   list_sort(list_distinct(
                       coalesce(list_filter(o.sources, s -> s IN ('wikidata', 'opencorporates', 'gdelt')), []::VARCHAR[])
                       || CASE WHEN w.domain IS NOT NULL THEN ['commoncrawl'] ELSE []::VARCHAR[] END)) AS sources
            FROM businesses b LEFT JOIN organizations o USING (organization_id)
            LEFT JOIN web_domains w ON w.domain = b.domain
        ) e
        WHERE e.gold_id = businesses.gold_id
    """)
