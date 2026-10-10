"""Explorable datasets and their column metadata (drives filters, sorting, search, columns in the UI)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .dsl import DUCKDB, OPERATORS_BY_TYPE, Dialect

UI_FILTER_TYPES = {"text": "text", "select": "multi_select", "number": "range", "boolean": "boolean",
                   "date": "date", "datetime": "datetime", "list": "multi_select", "map": "text", "json": "text",
                   "geo": "geo"}


@dataclass(frozen=True)
class Column:
    name: str
    label: str
    type: str = "text"
    expression: str | None = None            # filter/sort expression (defaults to the quoted column)
    display: dict | None = None              # {"duckdb": expr, "postgres": expr} when display differs
    decimal: bool = False
    filterable: bool = True
    sortable: bool = True
    searchable: bool = False
    visible: bool = True                     # shown by default in the explorer
    facet: bool = False                      # offer value suggestions
    geo: tuple[str, str] | None = None       # (lat, lon) for the virtual geo field
    description: str | None = None

    def sql(self, dialect: Dialect) -> str:
        return self.expression or f'"{self.name}"'

    def select_sql(self, dialect: Dialect) -> str | None:
        if self.type == "geo":
            return None
        if self.display:
            return f'{self.display[dialect.name]} AS "{self.name}"'
        return f'{self.sql(dialect)} AS "{self.name}"'

    def describe(self) -> dict:
        return {
            "field": self.name, "label": self.label, "type": UI_FILTER_TYPES[self.type], "data_type": self.type,
            "operators": OPERATORS_BY_TYPE[self.type] if self.filterable else [], "sortable": self.sortable,
            "searchable": self.searchable, "visible": self.visible, "facet": self.facet,
            "description": self.description,
        }


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    label: str
    description: str
    engine: str                              # parquet | postgres
    columns: list[Column]
    table: str | None = None                 # postgres table
    default_sort: tuple[str, str] = ("name", "asc")
    permission: str = "data_platform.view"
    primary_key: str | None = None
    partition_filters: dict[str, str] = field(default_factory=dict)   # filter field -> partition column

    @property
    def layer(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def column_map(self) -> dict[str, Column]:
        return {c.name: c for c in self.columns}

    def dialect(self) -> Dialect:
        from .dsl import POSTGRES

        return DUCKDB if self.engine == "parquet" else POSTGRES


def _lineage(prefix_visible=False) -> list[Column]:
    return [Column("artifact_id", "Artifact", "number", visible=prefix_visible),
            Column("pipeline_run_id", "Pipeline run", "number", visible=False)]


def _tags(name="tags") -> Column:
    return Column(name, "Tags", "map", display={"duckdb": f'to_json("{name}")::VARCHAR', "postgres": f'"{name}"'},
                  sortable=False, searchable=False, description="Filter with has_key (e.g. shop)")


_osm_common = [
    Column("id", "OSM id", "number"), Column("version", "Version", "number"),
    Column("timestamp", "Edited", "datetime"), Column("user", "Editor", "text", searchable=True),
    Column("changeset", "Changeset", "number", visible=False),
]

PLACES_COLUMNS = [
    Column("silver_id", "Silver id", "text", visible=False, searchable=True),
    Column("name", "Name", "text", searchable=True),
    Column("name_local", "Local name", "text", searchable=True, visible=False),
    Column("source", "Source", "select", facet=True),
    Column("source_record_id", "Source record", "text", searchable=True),
    Column("source_entity_type", "Source type", "select", facet=True, visible=False),
    Column("has_name", "Has name", "boolean", visible=False),
    Column("entity_type", "Entity type", "select", facet=True, visible=False),
    Column("category_group", "Category group", "select", facet=True),
    Column("category", "Category", "select", facet=True),
    Column("subcategory", "Subcategory", "select", facet=True, visible=False),
    Column("brand_name", "Brand", "select", facet=True, searchable=True, visible=False),
    Column("country_code", "Country", "select", facet=True),
    Column("admin1", "Region", "select", facet=True),
    Column("admin2", "District", "select", facet=True, visible=False),
    Column("city", "City", "select", facet=True),
    Column("locality", "Locality", "select", facet=True, visible=False),
    Column("address", "Address", "text", searchable=True, visible=False),
    Column("latitude", "Latitude", "number", decimal=True, visible=False),
    Column("longitude", "Longitude", "number", decimal=True, visible=False),
    Column("location", "Location", "geo", geo=('"latitude"', '"longitude"'), sortable=False),
    Column("website", "Website", "text", searchable=True),
    Column("normalized_domain", "Domain", "select", facet=True),
    Column("tld", "TLD", "select", facet=True, visible=False),
    Column("domain_is_platform", "Platform URL", "boolean", visible=False),
    Column("phone", "Phone (raw)", "text", visible=False),
    Column("normalized_phone", "Phone", "text", searchable=True),
    Column("email", "Email", "text", visible=False),
    Column("social_links", "Social links", "list", visible=False),
    Column("source_confidence", "Provider confidence", "number", decimal=True, visible=False),
    Column("opening_hours", "Opening hours", "text", visible=False),
    Column("source_updated_at", "Source updated", "datetime", visible=False),
    Column("source_version", "Source version", "select", facet=True, visible=False),
    Column("transformation_version", "Transformation", "select", facet=True, visible=False),
    Column("bronze_version_id", "Bronze version", "number", visible=False),
    *_lineage(),
    Column("attributes", "Raw attributes", "json", sortable=False, visible=False),
]

GOLD_COLUMNS = [
    Column("gold_id", "Gold id", "text", visible=False, searchable=True),
    Column("name", "Name", "text", searchable=True),
    Column("entity_type", "Entity type", "select", facet=True, visible=False),
    Column("category_group", "Category group", "select", facet=True),
    Column("category", "Category", "select", facet=True),
    Column("country_code", "Country", "select", facet=True, visible=False),
    Column("admin1", "Region", "select", facet=True),
    Column("admin2", "District", "select", facet=True, visible=False),
    Column("city", "City", "select", facet=True),
    Column("locality", "Locality", "select", facet=True, visible=False),
    Column("address", "Address", "text", searchable=True, visible=False),
    Column("latitude", "Latitude", "number", decimal=True, visible=False),
    Column("longitude", "Longitude", "number", decimal=True, visible=False),
    Column("location", "Location", "geo", geo=("latitude", "longitude"), sortable=False),
    Column("website", "Website", "text", searchable=True),
    Column("domain", "Domain", "select", facet=True),
    Column("tld", "TLD", "select", facet=True, visible=False),
    Column("phone", "Phone", "text", searchable=True),
    Column("email", "Email", "text", visible=False),
    Column("brand_name", "Brand", "select", facet=True, searchable=True, visible=False),
    Column("organization_id", "Organization", "text", visible=False),
    Column("sources", "Sources", "list", facet=True),
    Column("source_count", "Source count", "number"),
    Column("member_count", "Records merged", "number", visible=False),
    Column("has_website", "Has website", "boolean", visible=False),
    Column("has_phone", "Has phone", "boolean", visible=False),
    Column("has_email", "Has email", "boolean", visible=False),
    Column("has_address", "Has address", "boolean", visible=False),
    Column("completeness", "Completeness", "number"),
    Column("conflicts", "Conflicts", "list", facet=True, visible=False),
    Column("source_updated_at", "Source updated", "datetime", visible=False),
    Column("gold_version_id", "Gold version", "number", visible=False, facet=True),
    Column("source_confidence", "Provider confidence", "number", decimal=True, visible=False,
           description="Best provider confidence (empty for OSM-only businesses)"),
    Column("website_source", "Website from", "select", facet=True, visible=False,
           expression="field_sources->>'website'"),
    Column("phone_source", "Phone from", "select", facet=True, visible=False, expression="field_sources->>'phone'"),
    Column("field_sources", "Field sources", "json", sortable=False, visible=False),
    Column("wikidata_id", "Wikidata", "text", searchable=True, visible=False,
           description="The Wikidata item for this location (e.g. a school), not its brand"),
    Column("enriched_by", "Enriched by", "list", facet=True, visible=False,
           description="Non-location sources describing it: Wikidata, registries, news, web crawl"),
]

ORGANIZATION_RECORD_COLUMNS = [
    Column("silver_id", "Silver id", "text", visible=False, searchable=True),
    Column("name", "Name", "text", searchable=True),
    Column("source", "Source", "select", facet=True),
    Column("source_record_id", "Source record", "text", searchable=True),
    Column("legal_name", "Legal name", "text", searchable=True, visible=False),
    Column("entity_type", "Type", "select", facet=True),
    Column("category_group", "Category group", "select", facet=True),
    Column("org_types", "Classes", "list", facet=True, visible=False),
    Column("industries", "Industries", "list", facet=True),
    Column("country_code", "Country", "select", facet=True, visible=False),
    Column("headquarters", "Headquarters", "select", facet=True),
    Column("normalized_domain", "Domain", "select", facet=True),
    Column("website", "Website", "text", visible=False),
    Column("inception", "Founded", "text", visible=False),
    Column("employees", "Employees", "number", visible=False),
    Column("parent_name", "Parent", "select", facet=True, visible=False),
    Column("registry_id", "Registry id", "text", searchable=True, visible=False),
    Column("jurisdiction", "Jurisdiction", "select", facet=True, visible=False),
    Column("status", "Status", "select", facet=True, visible=False),
    Column("wikidata", "Wikidata", "text", searchable=True, visible=False),
    Column("description", "Description", "text", searchable=True, visible=False),
    Column("aliases", "Aliases", "list", visible=False),
    Column("identifiers", "Identifiers", "json", sortable=False, visible=False),
    Column("latitude", "Latitude", "number", decimal=True, visible=False),
    Column("longitude", "Longitude", "number", decimal=True, visible=False),
    Column("location", "Location", "geo", geo=('"latitude"', '"longitude"'), sortable=False),
    Column("source_confidence", "Confidence", "number", decimal=True, visible=False),
    Column("source_url", "Source URL", "text", visible=False),
    Column("transformation_version", "Transformation", "select", facet=True, visible=False),
    *_lineage(),
    Column("attributes", "Raw attributes", "json", sortable=False, visible=False),
]

_bronze_lineage = [Column("source", "Source", "select", facet=True, visible=False), *_lineage(True),
                   Column("ingested_at", "Ingested", "datetime", visible=False)]

DATASETS: list[DatasetSpec] = [
    DatasetSpec("bronze.osm_nodes", "OSM nodes", "Raw OSM nodes (all of them)", "parquet",
                _osm_common + [Column("latitude", "Latitude", "number", decimal=True),
                               Column("longitude", "Longitude", "number", decimal=True),
                               Column("location", "Location", "geo", geo=('"latitude"', '"longitude"')),
                               _tags(), *_lineage(True)],
                default_sort=("id", "asc"), permission="data_platform.view_raw_data", primary_key="id",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.osm_ways", "OSM ways", "Raw OSM ways with node references", "parquet",
                _osm_common + [Column("node_count", "Nodes", "number", expression="len(node_refs)"), _tags(),
                               *_lineage(True)],
                default_sort=("id", "asc"), permission="data_platform.view_raw_data", primary_key="id",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.osm_relations", "OSM relations", "Raw OSM relations with members", "parquet",
                _osm_common + [Column("member_count", "Members", "number", expression="len(members)"), _tags(),
                               *_lineage(True)],
                default_sort=("id", "asc"), permission="data_platform.view_raw_data", primary_key="id",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.overture_places", "Overture places", "Raw Overture records (all provider columns)", "parquet",
                [Column("id", "Overture id", "text", searchable=True),
                 Column("name", "Name", "text", searchable=True, expression='"names".primary'),
                 Column("category", "Category", "select", facet=True, expression='"taxonomy".primary'),
                 Column("confidence", "Confidence", "number", decimal=True),
                 Column("country", "Address country", "select", facet=True, expression='"addresses"[1].country'),
                 Column("locality", "Address locality", "select", facet=True, expression='"addresses"[1].locality'),
                 Column("websites", "Websites", "list", sortable=False),
                 Column("phones", "Phones", "list", sortable=False),
                 Column("datasets", "Underlying datasets", "list", sortable=False, filterable=False,
                        expression='list_transform("sources", s -> s.dataset)'),
                 Column("latitude", "Latitude", "number", decimal=True, expression='("bbox".ymin + "bbox".ymax) / 2'),
                 Column("longitude", "Longitude", "number", decimal=True, expression='("bbox".xmin + "bbox".xmax) / 2'),
                 *_lineage(True)],
                default_sort=("id", "asc"), permission="data_platform.view_raw_data", primary_key="id",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.geonames_places", "GeoNames features", "Every GeoNames feature (raw dump rows)", "parquet",
                [Column("geonameid", "GeoNames id", "number"), Column("name", "Name", "text", searchable=True),
                 Column("asciiname", "ASCII name", "text", searchable=True, visible=False),
                 Column("feature_class", "Class", "select", facet=True),
                 Column("feature_code", "Feature", "select", facet=True),
                 Column("population", "Population", "number"),
                 Column("admin1_code", "Admin1 code", "select", facet=True, visible=False),
                 Column("latitude", "Latitude", "number", decimal=True, visible=False),
                 Column("longitude", "Longitude", "number", decimal=True, visible=False),
                 Column("location", "Location", "geo", geo=('"latitude"', '"longitude"'), sortable=False),
                 Column("alternatenames", "Alternate names", "text", searchable=True, visible=False),
                 Column("modification_date", "Modified", "date", visible=False), *_bronze_lineage],
                default_sort=("geonameid", "asc"), permission="data_platform.view_raw_data", primary_key="geonameid",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.wikidata_entities", "Wikidata entities", "Full entity JSON as returned by Wikidata", "parquet",
                [Column("qid", "QID", "text", searchable=True), Column("label_en", "Label", "text", searchable=True),
                 Column("label_ur", "Urdu label", "text", searchable=True, visible=False),
                 Column("role", "Role", "select", facet=True), Column("classes", "Classes", "list", facet=True),
                 Column("description_en", "Description", "text", searchable=True),
                 Column("revision", "Revision", "number", visible=False),
                 Column("modified", "Modified", "datetime"),
                 Column("entity_json", "Entity JSON", "json", sortable=False, visible=False), *_bronze_lineage],
                default_sort=("qid", "asc"), permission="data_platform.view_raw_data", primary_key="qid",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.commoncrawl_captures", "Common Crawl captures", "URL-index records with WARC references",
                "parquet",
                [Column("url", "URL", "text", searchable=True), Column("host", "Host", "text", searchable=True),
                 Column("status", "HTTP status", "number"), Column("mime", "MIME", "select", facet=True),
                 Column("languages", "Languages", "select", facet=True), Column("captured_at", "Captured", "datetime"),
                 Column("scope", "Scope", "select", facet=True), Column("crawl", "Crawl", "select", facet=True),
                 Column("redirect", "Redirect", "text", visible=False),
                 Column("filename", "WARC file", "text", visible=False),
                 Column("offset", "WARC offset", "number", visible=False),
                 Column("length", "WARC length", "number", visible=False),
                 Column("urlkey", "SURT key", "text", searchable=True, visible=False), *_bronze_lineage],
                default_sort=("urlkey", "asc"), permission="data_platform.view_raw_data",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.gdelt_gkg", "GDELT knowledge graph", "News records about the country (GKG 2.1 columns)",
                "parquet",
                [Column("gkg_record_id", "Record", "text", searchable=True),
                 Column("published_at", "Published", "datetime"),
                 Column("source_name", "Outlet", "select", facet=True),
                 Column("document_url", "Article", "text", searchable=True),
                 Column("organizations", "Organizations", "text", searchable=True),
                 Column("locations", "Locations", "text", searchable=True, visible=False),
                 Column("themes", "Themes", "text", searchable=True, visible=False),
                 Column("tone", "Tone", "text", visible=False), Column("feed", "Feed", "select", facet=True),
                 Column("source_file", "File", "select", facet=True, visible=False), *_bronze_lineage],
                default_sort=("published_at", "desc"), permission="data_platform.view_raw_data",
                primary_key="gkg_record_id", partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("bronze.opencorporates_companies", "OpenCorporates companies", "Registry search results (raw JSON)",
                "parquet",
                [Column("name", "Name", "text", searchable=True), Column("query", "Searched for", "text",
                                                                        searchable=True),
                 Column("jurisdiction_code", "Jurisdiction", "select", facet=True),
                 Column("company_number", "Number", "text", searchable=True),
                 Column("current_status", "Status", "select", facet=True),
                 Column("company_type", "Type", "select", facet=True),
                 Column("incorporation_date", "Incorporated", "text"),
                 Column("searched_at", "Searched", "datetime", visible=False),
                 Column("company_json", "Company JSON", "json", sortable=False, visible=False), *_bronze_lineage],
                default_sort=("name", "asc"), permission="data_platform.view_raw_data",
                partition_filters={"artifact_id": "artifact_id"}),
    DatasetSpec("silver.places", "Places", "Normalized place observations from every source", "parquet",
                PLACES_COLUMNS, primary_key="silver_id", partition_filters={"source": "source"}),
    DatasetSpec("silver.rejects", "Rejected records", "Source records that failed normalization", "parquet",
                [Column("source", "Source", "select", facet=True),
                 Column("source_record_id", "Source record", "text", searchable=True),
                 Column("reason", "Reason", "select", facet=True), Column("name", "Name", "text", searchable=True),
                 Column("latitude", "Latitude", "number", decimal=True),
                 Column("longitude", "Longitude", "number", decimal=True),
                 Column("attributes", "Raw attributes", "json", sortable=False, visible=False),
                 *_lineage(True), Column("rejected_at", "Rejected", "datetime")],
                default_sort=("source_record_id", "asc"), partition_filters={"source": "source"}),
    DatasetSpec("silver.geo_boundaries", "Administrative boundaries", "Country / region / district polygons",
                "parquet",
                [Column("boundary_id", "Boundary", "text", searchable=True),
                 Column("source", "Source", "select", facet=True),
                 Column("admin_level", "Admin level", "select", facet=True, expression='CAST("admin_level" AS VARCHAR)'),
                 Column("name", "Name", "text", searchable=True),
                 Column("name_local", "Local name", "text", searchable=True),
                 Column("country_code", "Country", "select", facet=True),
                 Column("area_km2", "Area (km²)", "number", decimal=True), *_lineage(True)],
                default_sort=("name", "asc")),
    DatasetSpec("silver.geo_places", "Populated places", "Cities, towns and villages from the gazetteers",
                "parquet",
                [Column("place_id", "Place", "text", searchable=True, visible=False),
                 Column("name", "Name", "text", searchable=True),
                 Column("name_local", "Local name", "text", searchable=True),
                 Column("source", "Source", "select", facet=True), Column("kind", "Kind", "select", facet=True),
                 Column("population", "Population", "number"), Column("admin1", "Region", "select", facet=True),
                 Column("admin2", "District", "select", facet=True, visible=False),
                 Column("feature_code", "Feature", "select", facet=True, visible=False),
                 Column("latitude", "Latitude", "number", decimal=True, visible=False),
                 Column("longitude", "Longitude", "number", decimal=True, visible=False),
                 Column("location", "Location", "geo", geo=('"latitude"', '"longitude"'), sortable=False)],
                default_sort=("population", "desc"), partition_filters={"source": "source"}),
    DatasetSpec("silver.postal_codes", "Postal codes", "Postal codes with place and admin names", "parquet",
                [Column("postal_code", "Postal code", "text", searchable=True),
                 Column("place_name", "Place", "text", searchable=True),
                 Column("admin1", "Region", "select", facet=True), Column("admin2", "District", "select", facet=True),
                 Column("source", "Source", "select", facet=True, visible=False),
                 Column("latitude", "Latitude", "number", decimal=True, visible=False),
                 Column("longitude", "Longitude", "number", decimal=True, visible=False)],
                default_sort=("postal_code", "asc")),
    DatasetSpec("silver.organizations", "Organization records", "Companies, brands and institutions from Wikidata "
                "and registries (one row per source record)", "parquet", ORGANIZATION_RECORD_COLUMNS,
                primary_key="silver_id", partition_filters={"source": "source"}),
    DatasetSpec("silver.web_domains", "Web domains (crawl)", "Domains discovered in the web-crawl index", "parquet",
                [Column("domain", "Domain", "text", searchable=True),
                 Column("domain_type", "Type", "select", facet=True),
                 Column("is_business_candidate", "Business candidate", "boolean"),
                 Column("pages", "Pages", "number"), Column("captures", "Captures", "number", visible=False),
                 Column("host_count", "Hosts", "number", visible=False),
                 Column("top_language", "Language", "select", facet=True),
                 Column("homepage_status", "Homepage status", "number"),
                 Column("homepage_url", "Homepage", "text", visible=False),
                 Column("redirect_domain", "Redirects to", "select", facet=True, visible=False),
                 Column("ok_ratio", "OK ratio", "number", decimal=True, visible=False),
                 Column("last_seen", "Last crawled", "datetime"),
                 Column("suffix", "Suffix", "select", facet=True, visible=False),
                 Column("crawl", "Crawl", "select", facet=True, visible=False),
                 Column("warc_filename", "WARC file", "text", visible=False),
                 Column("warc_offset", "WARC offset", "number", visible=False),
                 Column("source", "Source", "select", facet=True, visible=False)],
                default_sort=("pages", "desc"), primary_key="domain", partition_filters={"source": "source"}),
    DatasetSpec("silver.org_mentions", "News mentions", "Organizations named in news about the country", "parquet",
                [Column("name", "Organization", "text", searchable=True),
                 Column("mentions", "Mentions", "number"), Column("articles", "Articles", "number"),
                 Column("outlets", "Outlets", "number"), Column("avg_tone", "Tone", "number", decimal=True),
                 Column("last_seen", "Last seen", "datetime"), Column("first_seen", "First seen", "datetime",
                                                                      visible=False),
                 Column("top_themes", "Themes", "list", facet=True, visible=False),
                 Column("top_locations", "Places", "list", facet=True),
                 Column("sample_urls", "Articles (sample)", "list", visible=False, sortable=False),
                 Column("name_key", "Match key", "text", searchable=True, visible=False),
                 Column("source", "Source", "select", facet=True, visible=False)],
                default_sort=("mentions", "desc"), primary_key="name_key", partition_filters={"source": "source"}),
    DatasetSpec("gold.businesses", "Businesses", "Resolved businesses (one row per real-world location)",
                "postgres", GOLD_COLUMNS, table="gold.businesses", primary_key="gold_id"),
    DatasetSpec("gold.organizations", "Organizations", "Companies, brands and institutions: from their locations, "
                "Wikidata and registries, with news signals", "postgres",
                [Column("organization_id", "Organization", "text", visible=False),
                 Column("name", "Name", "text", searchable=True),
                 Column("legal_name", "Legal name", "text", searchable=True, visible=False),
                 Column("country_code", "Country", "select", facet=True, visible=False),
                 Column("category_group", "Category group", "select", facet=True),
                 Column("industry", "Industry", "select", facet=True),
                 Column("location_count", "Locations", "number"), Column("city_count", "Cities", "number"),
                 Column("domain", "Domain", "select", facet=True),
                 Column("headquarters", "Headquarters", "select", facet=True, visible=False),
                 Column("inception", "Founded", "text", visible=False),
                 Column("employees", "Employees", "number", visible=False),
                 Column("parent_name", "Parent", "select", facet=True, visible=False),
                 Column("wikidata_id", "Wikidata", "text", searchable=True),
                 Column("brand_wikidata", "Brand Wikidata", "text", searchable=True, visible=False),
                 Column("registry_id", "Registry id", "text", searchable=True, visible=False),
                 Column("registry_status", "Registry status", "select", facet=True, visible=False),
                 Column("news_mentions", "News mentions", "number"),
                 Column("news_last_seen", "In the news", "datetime", visible=False),
                 Column("news_tone", "News tone", "number", decimal=True, visible=False),
                 Column("linked_records", "Linked records", "number", visible=False),
                 Column("sources", "Sources", "list", facet=True),
                 Column("description", "Description", "text", searchable=True, visible=False),
                 Column("aliases", "Aliases", "list", visible=False),
                 Column("identifiers", "Identifiers", "json", sortable=False, visible=False),
                 Column("field_sources", "Field sources", "json", sortable=False, visible=False)],
                table="gold.organizations", default_sort=("location_count", "desc"), primary_key="organization_id"),
    DatasetSpec("gold.organization_sources", "Organization provenance",
                "Registry / knowledge-base records behind each organization, and how they were linked", "postgres",
                [Column("organization_id", "Organization", "text", searchable=True),
                 Column("silver_id", "Silver id", "text", searchable=True),
                 Column("source", "Source", "select", facet=True),
                 Column("source_record_id", "Source record", "text", searchable=True),
                 Column("link_method", "Linked by", "select", facet=True),
                 Column("confidence", "Confidence", "number", decimal=True),
                 Column("country_code", "Country", "select", facet=True, visible=False),
                 Column("artifact_id", "Artifact", "number", visible=False),
                 Column("pipeline_run_id", "Pipeline run", "number", visible=False),
                 Column("source_url", "Source URL", "text", visible=False)],
                table="gold.organization_sources", default_sort=("organization_id", "asc")),
    DatasetSpec("gold.domains", "Domains", "Web domains used by businesses, stated by organizations or discovered "
                "by the crawl", "postgres",
                [Column("domain", "Domain", "text", searchable=True),
                 Column("country_code", "Country", "select", facet=True, visible=False),
                 Column("tld", "TLD", "select", facet=True),
                 Column("domain_type", "Type", "select", facet=True),
                 Column("business_count", "Businesses", "number"),
                 Column("organization_count", "Organizations", "number"),
                 Column("sources", "Sources", "list", facet=True),
                 Column("top_category_group", "Main category", "select", facet=True),
                 Column("is_business_candidate", "Business candidate", "boolean", visible=False),
                 Column("crawl_pages", "Crawled pages", "number"),
                 Column("homepage_status", "Homepage status", "number", visible=False),
                 Column("crawl_languages", "Languages", "list", facet=True, visible=False),
                 Column("crawl_last_seen", "Last crawled", "datetime", visible=False),
                 Column("homepage_url", "Homepage", "text", visible=False),
                 Column("organization_id", "Organization", "text", visible=False)],
                table="gold.domains", default_sort=("business_count", "desc"), primary_key="domain"),
    DatasetSpec("gold.cities", "Cities", "Cities and towns: population and business coverage", "postgres",
                [Column("city_id", "City", "text", visible=False),
                 Column("name", "Name", "text", searchable=True),
                 Column("name_local", "Local name", "text", searchable=True, visible=False),
                 Column("kind", "Kind", "select", facet=True), Column("admin1", "Region", "select", facet=True),
                 Column("admin2", "District", "select", facet=True, visible=False),
                 Column("population", "Population", "number"),
                 Column("business_count", "Businesses", "number"),
                 Column("businesses_per_10k", "Per 10k people", "number", decimal=True),
                 Column("with_website", "With website", "number", visible=False),
                 Column("with_phone", "With phone", "number", visible=False),
                 Column("organizations", "Organizations", "number", visible=False),
                 Column("top_category_group", "Main category", "select", facet=True),
                 Column("source", "Gazetteer", "select", facet=True, visible=False),
                 Column("latitude", "Latitude", "number", decimal=True, visible=False),
                 Column("longitude", "Longitude", "number", decimal=True, visible=False),
                 Column("location", "Location", "geo", geo=("latitude", "longitude"), sortable=False)],
                table="gold.cities", default_sort=("business_count", "desc"), primary_key="city_id"),
    DatasetSpec("gold.business_sources", "Business provenance", "Source records behind each Gold business",
                "postgres",
                [Column("gold_id", "Gold id", "text", searchable=True),
                 Column("silver_id", "Silver id", "text", searchable=True),
                 Column("source", "Source", "select", facet=True),
                 Column("source_record_id", "Source record", "text", searchable=True),
                 Column("country_code", "Country", "select", facet=True),
                 Column("artifact_id", "Artifact", "number"), Column("pipeline_run_id", "Pipeline run", "number"),
                 Column("transformation_version", "Transformation", "select", facet=True),
                 Column("match_confidence", "Confidence", "number", decimal=True),
                 Column("is_primary", "Primary", "boolean")],
                table="gold.business_sources", default_sort=("gold_id", "asc")),
    DatasetSpec("er.entity_matches", "Entity matches", "Candidate duplicate pairs with match evidence", "postgres",
                [Column("id", "Match", "number", visible=False),
                 Column("left_id", "Left record", "text", searchable=True),
                 Column("right_id", "Right record", "text", searchable=True),
                 Column("country_code", "Country", "select", facet=True),
                 Column("match_type", "Type", "select", facet=True,
                        description="location: same place twice; organization: a place or record of an organization"),
                 Column("status", "Status", "select", facet=True),
                 Column("method", "Linked by", "select", facet=True, expression="evidence->>'method'",
                        visible=False),
                 Column("match_score", "Score", "number", decimal=True),
                 Column("name_score", "Name", "number", decimal=True),
                 Column("geo_score", "Geo", "number", decimal=True),
                 Column("phone_score", "Phone", "number", decimal=True, visible=False),
                 Column("domain_score", "Domain", "number", decimal=True, visible=False),
                 Column("address_score", "Address", "number", decimal=True, visible=False),
                 Column("distance_m", "Distance (m)", "number", decimal=True),
                 Column("algorithm_version", "Algorithm", "select", facet=True),
                 Column("decided_by", "Decided by", "text", visible=False),
                 Column("updated_at", "Updated", "datetime", visible=False)],
                table="er.entity_matches", default_sort=("match_score", "desc"), primary_key="id"),
]

BY_ID = {d.id: d for d in DATASETS}


def get_dataset(dataset_id: str) -> DatasetSpec:
    try:
        return BY_ID[dataset_id]
    except KeyError:
        raise KeyError(f"Unknown dataset {dataset_id!r}") from None
