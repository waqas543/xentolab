"""Common Crawl Bronze -> silver.web_domains (one row per registrable domain).

Hosts are mapped to registrable domains with the bundled public-suffix list (offline);
the aggregation itself runs in DuckDB over the capture records.
"""

from __future__ import annotations

import re

import pyarrow as pa

from ...engine.context import StageContext
from ...lake.reader import duck, parquet_source, version_files
from ...lake.writer import DatasetVersionWriter, copy_query_into
from ...normalize import registered_domain

# What a second-level label under a ccTLD says about the registrant (Pakistan's PKNIC scheme and common
# equivalents elsewhere). Anything else under the ccTLD is a direct registration ("generic").
SECOND_LEVEL_TYPES = {
    "com": "commercial", "co": "commercial", "biz": "business", "net": "network", "org": "non-profit",
    "edu": "education", "ac": "education", "gov": "government", "gob": "government", "gok": "government",
    "gon": "government", "gop": "government", "gos": "government", "gkp": "government", "mil": "government",
    "fam": "personal", "web": "web",
}
SPAM = re.compile(r"(sex|porn|xxx|casino|betting|slot|escort|viagra|loan-?app|hack|crack|apk)")


def domain_type(suffix: str | None, tld: str, scope: str) -> str:
    if scope == "known_domain":
        return "known_business"
    if not suffix:
        return "unknown"
    parts = suffix.split(".")
    if len(parts) >= 2 and parts[-1] == tld:
        return SECOND_LEVEL_TYPES.get(parts[-2], "other")
    return "generic" if suffix == tld else "other"


def transform(ctx: StageContext, artifact: dict, bronze: dict, transformation_version: str):
    country, meta = artifact["country"], artifact["metadata"]
    tld = meta["tld"]
    partition = {"source": "commoncrawl", "country": country}
    con = duck()
    try:
        con.execute(f"CREATE VIEW captures AS SELECT * FROM {parquet_source(version_files(bronze))}")
        ctx.progress(0, None, "hosts", "mapping hosts to registrable domains", force=True)
        hosts = con.execute("""
            SELECT host, any_value(scope) FROM captures WHERE host IS NOT NULL GROUP BY host
        """).fetchall()
        mapping = {"host": [], "domain": [], "suffix": [], "domain_type": []}
        for host, scope in hosts:
            domain, suffix = registered_domain(host)
            if not domain:
                continue
            mapping["host"].append(host)
            mapping["domain"].append(domain)
            mapping["suffix"].append(suffix)
            mapping["domain_type"].append(domain_type(suffix, tld, scope))
        con.register("host_domains", pa.table(mapping))
        sampled = set(meta.get("sampled_domains") or [])
        con.register("sampled_domains", pa.table({"domain": sorted(sampled)}, schema=pa.schema([("domain", pa.string())])))
        redirect_hosts = con.execute("""
            SELECT DISTINCT lower(split_part(split_part(split_part(redirect, '://', 2), '/', 1), ':', 1))
            FROM captures WHERE redirect IS NOT NULL
        """).fetchall()
        con.register("redirect_domains", pa.table({
            "host": [r[0] for r in redirect_hosts],
            "domain": [registered_domain(r[0])[0] for r in redirect_hosts]}))

        captures = con.execute("SELECT count(*) FROM captures").fetchone()[0]
        ctx.progress(0, None, "domains", f"aggregating {captures:,} captures by domain", force=True)
        is_home = "regexp_matches(c.url, '^https?://[^/]+/?(index\\.[a-z]+)?$')"
        with DatasetVersionWriter(ctx.storage, "silver.web_domains", partition, None, run_id=ctx.run_id,
                                  versions={"transformation": transformation_version},
                                  inputs=[{"type": "artifact", "id": artifact["id"]},
                                          {"type": "dataset_version", "dataset_id": "bronze.commoncrawl_captures",
                                           "id": bronze["id"]}]) as writer:
            copy_query_into(writer, con, f"""
                WITH c AS (
                    SELECT c.*, h.domain, h.suffix, h.domain_type,
                           -- Homepage preference: the site root, answered 200 as HTML, on the bare or www host.
                           ({is_home})::INT * 4 + (c.status = 200)::INT * 2
                             + (c.host IN (h.domain, 'www.' || h.domain))::INT AS home_rank
                    FROM captures c JOIN host_domains h USING (host)
                ),
                languages AS (
                    SELECT domain, list(language ORDER BY n DESC, language) AS languages
                    FROM (SELECT domain, t.language, count(*) AS n
                          FROM c, unnest(string_split(languages, ',')) AS t(language)
                          WHERE languages IS NOT NULL AND languages <> '' GROUP BY 1, 2)
                    GROUP BY domain
                ),
                domains AS (
                    SELECT domain, any_value(suffix) AS suffix, any_value(domain_type) AS domain_type,
                           list(DISTINCT host)[1:20] AS hosts, count(DISTINCT host)::INTEGER AS host_count,
                           count(*) AS captures, count(DISTINCT url) AS pages,
                           min(captured_at) AS first_seen, max(captured_at) AS last_seen,
                           count(*) FILTER (WHERE status BETWEEN 200 AND 299) AS status_2xx,
                           count(*) FILTER (WHERE status BETWEEN 300 AND 399) AS status_3xx,
                           count(*) FILTER (WHERE status BETWEEN 400 AND 499) AS status_4xx,
                           count(*) FILTER (WHERE status >= 500) AS status_5xx,
                           arg_max(url, home_rank * 100000000 - length(url)) AS homepage_url,
                           arg_max(status, home_rank * 100000000 - length(url))::INTEGER AS homepage_status,
                           arg_max(redirect, home_rank * 100000000 - length(url)) AS homepage_redirect,
                           arg_max(filename, home_rank * 100000000 - length(url)) AS warc_filename,
                           arg_max("offset", home_rank * 100000000 - length(url)) AS warc_offset,
                           arg_max(length, home_rank * 100000000 - length(url)) AS warc_length,
                           any_value(crawl) AS crawl
                    FROM c GROUP BY domain
                )
                SELECT d.domain, 'commoncrawl' AS source, '{country}' AS country_code,
                       string_split(d.suffix, '.')[-1] AS tld, d.suffix, d.domain_type, d.hosts, d.host_count, d.captures, d.pages,
                       d.first_seen, d.last_seen, d.status_2xx, d.status_3xx, d.status_4xx, d.status_5xx,
                       round(d.status_2xx / d.captures, 4) AS ok_ratio, l.languages[1:5] AS languages,
                       l.languages[1] AS top_language, d.homepage_url, d.homepage_status,
                       r.domain AS redirect_domain, d.warc_filename, d.warc_offset, d.warc_length, d.crawl,
                       -- Large known sites are sampled (first block + homepage): their page counts are lower bounds.
                       CASE WHEN d.domain IN (SELECT domain FROM sampled_domains) THEN 'sample' ELSE 'complete' END
                           AS capture_coverage,
                       d.domain_type IN ('commercial', 'business', 'generic', 'known_business', 'web')
                         AND d.status_2xx > 0
                         AND NOT regexp_matches(d.domain, '{SPAM.pattern}') AS is_business_candidate,
                       {artifact['id']}::BIGINT AS artifact_id, {bronze['id']}::BIGINT AS bronze_version_id,
                       {ctx.run_id}::BIGINT AS pipeline_run_id, '{transformation_version}' AS transformation_version,
                       now()::TIMESTAMPTZ AS ingested_at
                FROM domains d
                LEFT JOIN languages l USING (domain)
                LEFT JOIN redirect_domains r
                       ON r.host = lower(split_part(split_part(split_part(d.homepage_redirect, '://', 2), '/', 1), ':', 1))
            """)
    finally:
        con.close()
    return writer.version, {"captures": int(captures), "hosts": len(mapping["host"]),
                            "domains": writer.version["row_count"]}
