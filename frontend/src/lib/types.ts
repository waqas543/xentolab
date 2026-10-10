export type OsmType = "n" | "w" | "r";

export interface Option {
  value: string;
  count: number;
  parent?: string;
}

export interface FilterOptions {
  groups: Option[];
  categories: Option[];
  provinces: Option[];
  districts: Option[];
  cities: Option[];
  brands: Option[];
}

export interface CompanyRow {
  osm_type: OsmType;
  osm_id: number;
  name: string;
  name_local: string | null;
  category_group: string;
  category: string;
  brand: string | null;
  phone: string | null;
  website: string | null;
  email: string | null;
  opening_hours: string | null;
  addr_street: string | null;
  addr_housenumber: string | null;
  addr_postcode: string | null;
  addr_city: string | null;
  city: string | null;
  locality: string | null;
  district: string | null;
  province: string | null;
  latitude: number;
  longitude: number;
  completeness: number;
  last_edited: string | null;
}

export interface CompanyDetail extends CompanyRow {
  category_key: string;
  operator: string | null;
  country: string | null;
  locality_distance_km: number | null;
  version: number | null;
  changeset: number | null;
  last_editor: string | null;
  brand_wikidata: string | null;
  has_phone: boolean;
  has_website: boolean;
  has_email: boolean;
  has_address: boolean;
  has_opening_hours: boolean;
  tags: Record<string, string>;
}

export interface NearbyCompany {
  osm_type: OsmType;
  osm_id: number;
  name: string;
  category_group: string;
  category: string;
  distance_m: number;
}

export interface CompanyPage {
  total: number;
  page: number;
  page_size: number;
  rows: CompanyRow[];
}

export interface Count {
  label: string | number;
  count: number;
  group?: string;
}

export interface Overview {
  kpis: {
    total: number;
    with_phone: number;
    with_website: number;
    with_email: number;
    with_address: number;
    with_opening_hours: number;
    branded: number;
    avg_completeness: number | null;
    brands: number;
    cities: number;
    categories: number;
    latest_edit: string | null;
  };
  by_group: Count[];
  by_province: Count[];
  top_cities: Count[];
  top_categories: Count[];
  completeness: Count[];
  edits_by_year: Count[];
}

export interface Pivot {
  rows: string[];
  cols: string[];
  row_totals: number[];
  col_totals: number[];
  matrix: number[][];
}

export interface BrandRow {
  brand: string;
  locations: number;
  cities: number;
  provinces: number;
  category_group: string;
  category: string;
  top_city: string | null;
  avg_completeness: number;
  pct_phone: number;
}

export interface QualityRow {
  label: string;
  count: number;
  has_phone: number;
  has_website: number;
  has_email: number;
  has_address: number;
  has_opening_hours: number;
  avg_completeness: number;
}

export interface DuplicatePair {
  name: string;
  a_type: OsmType;
  a_id: number;
  a_category: string;
  b_type: OsmType;
  b_id: number;
  b_category: string;
  city: string | null;
  distance_m: number;
}

export type MapPoint = [OsmType, number, number, number, string, string, string];

export interface CompanyRef {
  osm_type: OsmType;
  osm_id: number;
}

export const DIMENSIONS: { value: string; label: string }[] = [
  { value: "category_group", label: "Category group" },
  { value: "category", label: "Category" },
  { value: "province", label: "Province" },
  { value: "district", label: "District" },
  { value: "city", label: "City" },
  { value: "locality", label: "Locality" },
  { value: "brand", label: "Brand" },
];

export function dimensionLabel(value: string) {
  return DIMENSIONS.find((d) => d.value === value)?.label ?? value;
}
