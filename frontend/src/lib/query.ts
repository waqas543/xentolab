export interface ColumnInfo {
  name: string;
  type: string;
}

export interface QueryResult {
  status: "ok";
  query_id: string;
  statement: string;
  columns: ColumnInfo[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  affected_rows: number | null;
  status_message: string | null;
  notices: string[];
  duration_ms: number;
}

export interface QueryError {
  status: "error" | "cancelled";
  message: string;
  detail: string | null;
  hint: string | null;
  position: number | null;
  sqlstate: string | null;
  duration_ms?: number;
  notices?: string[];
}

export interface TableInfo {
  schema: string;
  name: string;
  kind: string;
  estimated_rows: number;
  size_bytes: number;
  columns: ColumnInfo[];
}

export interface SavedQuery {
  id: number;
  name: string;
  sql: string;
  created_at: string;
  updated_at: string;
}

export interface HistoryEntry {
  id: number;
  sql: string;
  read_only: boolean;
  status: "ok" | "error" | "cancelled";
  row_count: number | null;
  duration_ms: number | null;
  error: string | null;
  ran_at: string;
}

const NUMERIC_TYPES = new Set([
  "int2", "int4", "int8", "float4", "float8", "numeric", "smallint", "integer", "bigint", "real", "double precision", "oid",
]);

export function isNumericType(type: string) {
  return NUMERIC_TYPES.has(type) || type.startsWith("numeric");
}

export const EXAMPLES: { title: string; sql: string }[] = [
  {
    title: "Companies per city with contact coverage",
    sql: `SELECT city,
       count(*) AS companies,
       round(100.0 * avg(has_phone::int), 1) AS pct_phone,
       round(100.0 * avg(has_website::int), 1) AS pct_website,
       round(avg(completeness), 1) AS avg_completeness
FROM osm_app.companies
GROUP BY city
ORDER BY companies DESC
LIMIT 25;`,
  },
  {
    title: "Restaurants by cuisine",
    sql: `SELECT tags->>'cuisine' AS cuisine, count(*) AS restaurants
FROM osm_app.companies
WHERE category IN ('restaurant', 'fast_food', 'cafe')
  AND tags ? 'cuisine'
GROUP BY 1
ORDER BY 2 DESC;`,
  },
  {
    title: "Banks by brand and province",
    sql: `SELECT province, brand, count(*) AS branches
FROM osm_app.companies
WHERE category = 'bank' AND brand IS NOT NULL
GROUP BY province, brand
ORDER BY province, branches DESC;`,
  },
  {
    title: "Leads: complete profiles in Lahore",
    sql: `SELECT name, category, phone, website, email, addr_street, locality
FROM osm_app.companies
WHERE city = 'Lahore'
  AND has_phone AND has_website
ORDER BY completeness DESC, name;`,
  },
  {
    title: "Companies within 1 km of a point",
    sql: `-- Distance search with PostGIS (Lahore Liberty Market)
SELECT name, category,
       round(ST_DistanceSphere(geom, ST_SetSRID(ST_MakePoint(74.3436, 31.5106), 4326))) AS meters
FROM osm_app.companies
WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(74.3436, 31.5106), 4326)::geography, 1000)
ORDER BY meters;`,
  },
  {
    title: "Company density per district (per 100 km²)",
    sql: `SELECT a.name AS district,
       count(c.*) AS companies,
       round((ST_Area(a.geom::geography) / 1e6)::numeric) AS area_km2,
       round((count(c.*) / (ST_Area(a.geom::geography) / 1e8))::numeric, 2) AS per_100_km2
FROM osm_app.admin_areas a
LEFT JOIN osm_app.companies c ON ST_Contains(a.geom, c.geom)
WHERE a.admin_level = 6
GROUP BY a.name, a.geom
ORDER BY per_100_km2 DESC
LIMIT 25;`,
  },
  {
    title: "New / updated companies per month",
    sql: `SELECT date_trunc('month', last_edited)::date AS month, count(*) AS companies
FROM osm_app.companies
WHERE last_edited >= now() - interval '3 years'
GROUP BY 1
ORDER BY 1;`,
  },
  {
    title: "Most common shop types",
    sql: `SELECT tags->>'shop' AS shop, count(*) AS companies
FROM osm_app.companies
WHERE tags ? 'shop'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 30;`,
  },
  {
    title: "Raw OSM: top contributors to company data",
    sql: `SELECT last_editor, count(*) AS companies, max(last_edited)::date AS latest
FROM osm_app.companies
GROUP BY 1
ORDER BY 2 DESC
LIMIT 25;`,
  },
];
