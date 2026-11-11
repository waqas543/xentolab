"""Organization keys: one brand tagged three different ways still becomes one organization."""

import duckdb
import pyarrow as pa

from data_platform.gold import organizations


def _prepare(con, links=()):
    con.execute("ALTER TABLE businesses ADD COLUMN organization_id VARCHAR")
    con.execute("ALTER TABLE businesses ADD COLUMN wikidata_id VARCHAR")
    con.execute("ALTER TABLE businesses ADD COLUMN city VARCHAR")
    con.execute("ALTER TABLE businesses ADD COLUMN locality VARCHAR")
    con.execute("CREATE TABLE members AS SELECT gold_id, 'osm:' || gold_id AS silver_id FROM businesses")
    con.register("place_org", pa.table({"place_id": [l[0] for l in links], "org_key": [l[1] for l in links],
                                        "method": [l[2] for l in links], "strength": [1 for _ in links]},
                                       schema=pa.schema([("place_id", pa.string()), ("org_key", pa.string()),
                                                         ("method", pa.string()), ("strength", pa.int32())])))


def test_brand_name_and_shared_domain_follow_the_brand_qid():
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE businesses AS SELECT * FROM (VALUES
            ('b1', 'Bank Alfalah Gulberg', 'Bank Alfalah', 'Q99', 'bankalfalah.com'),   -- brand tag + Wikidata id
            ('b2', 'Bank Alfalah DHA', 'Bank Alfalah', NULL, NULL),                      -- brand name only
            ('b3', 'Bank Alfalah Saddar', NULL, NULL, 'bankalfalah.com'),               -- only the shared website
            ('b4', 'Bank Alfalah Mall Road', NULL, NULL, 'bankalfalah.com'),
            ('b5', 'Corner Shop', 'Corner Shop', NULL, NULL),                            -- unrelated brand
            ('b6', 'Two Shops Model Town', NULL, NULL, 'twoshops.pk'),                   -- domain-only organization
            ('b7', 'Two Shops Gulberg', NULL, NULL, 'twoshops.pk'),
            -- A portal domain shared by unrelated offices: no organization from the domain alone.
            ('p1', 'District Health Authority Lodhran', NULL, NULL, 'punjab.gov.pk'),
            ('p2', 'Counter Narcotics Force', NULL, NULL, 'punjab.gov.pk'),
            ('p3', 'Social Welfare Liaquatpur', NULL, NULL, 'punjab.gov.pk')
        ) t(gold_id, name, brand_name, brand_wikidata, domain)
    """)
    _prepare(con)
    organizations.assign_business_organizations(con)
    org = dict(con.execute("SELECT gold_id, organization_id FROM businesses").fetchall())
    assert org["b1"] == org["b2"] == org["b3"] == org["b4"]
    assert org["b5"] not in (org["b1"], None)
    assert org["b6"] == org["b7"] and org["b6"] != org["b1"]
    assert org["p1"] is None and org["p2"] is None and org["p3"] is None


def test_one_brand_under_two_keys_becomes_one_organization():
    """Shell plc's Wikidata item on some stations and a plain brand name on others: one organization."""
    con = duckdb.connect()
    rows = [(f"q{i}", f"Shell Station {i}", "Shell", "Q154950", None) for i in range(4)]
    rows += [(f"n{i}", f"Shell Petrol {i}", "Shell", None, None) for i in range(4)]
    rows += [(f"x{i}", f"Total Parco {i}", "Total Parco", None, None) for i in range(3)]
    values = ", ".join(f"('{g}', '{n}', '{b}', {repr(q) if q else 'NULL'}, NULL)" for g, n, b, q, _ in rows)
    con.execute(f"CREATE TABLE businesses AS SELECT * FROM (VALUES {values}) "
                "t(gold_id, name, brand_name, brand_wikidata, domain)")
    con.execute("UPDATE businesses SET domain = CAST(domain AS VARCHAR)")
    _prepare(con)
    # A different spelling of the brand ("Shell Pakistan") does not map to the QID by name: two keys, one brand.
    con.execute("UPDATE businesses SET brand_name = 'Shell Pakistan' WHERE gold_id LIKE 'n%'")
    organizations.assign_business_organizations(con)
    assert con.execute("SELECT count(*) FROM org_key_remap").fetchone()[0] == 1
    org = dict(con.execute("SELECT gold_id, organization_id FROM businesses").fetchall())
    assert len({org[f"q{i}"] for i in range(4)} | {org[f"n{i}"] for i in range(4)}) == 1
    assert org["x0"] != org["q0"]
