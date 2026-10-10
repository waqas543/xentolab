import { API_URL } from '../config';

export const API_BASE = API_URL.replace(/\/$/, '');

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

export async function get<T>(path: string, params: Params = {}): Promise<T> {
  const query = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '' && value !== false)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/app${path}${query ? `?${query}` : ''}`);
  } catch {
    throw new ApiError("Can't reach the server. Check your internet connection and try again.");
  }
  if (response.status === 404) throw new ApiError('This item is no longer available.', 404);
  if (!response.ok) throw new ApiError('Something went wrong. Please try again.', response.status);
  return response.json();
}

export type Page<T> = { items: T[]; total: number; next_offset: number | null };

export type Place = {
  id: string;
  name: string;
  category: string | null;
  category_key: string | null;
  group: string | null;
  city: string | null;
  area: string | null;
  address: string | null;
  phone: string | null;
  website: string | null;
  email: string | null;
  lat: number | null;
  lng: number | null;
  brand: string | null;
  organization_id: string | null;
  distance_km?: number;
};

export type PlaceDetail = Place & {
  postal_code: string | null;
  province: string | null;
  district: string | null;
  city_id: string | null;
  organization: { id: string; name: string; industry: string | null; locations: number } | null;
  nearby: Place[];
};

export type Company = {
  id: string;
  name: string;
  industry: string | null;
  group: string | null;
  locations: number;
  cities: number;
  headquarters: string | null;
  website: string | null;
  description: string | null;
};

export type CompanyDetail = Company & {
  legal_name: string | null;
  founded: string | null;
  employees: number | null;
  parent: string | null;
  also_known_as: string[];
  registration_number: string | null;
  status: string | null;
  news_mentions: number;
  last_in_news: string | null;
  top_cities: { id: string | null; name: string; locations: number }[];
};

export type City = {
  id: string;
  name: string;
  local_name: string | null;
  kind: string;
  province: string | null;
  district: string | null;
  population: number | null;
  businesses: number;
  with_phone: number;
  with_website: number;
  companies: number;
  per_10k_people: number | null;
  top_group: string | null;
  lat: number | null;
  lng: number | null;
};

export type Count = { name: string; count: number };

export type CityDetail = City & {
  groups: Count[];
  areas: Count[];
  brands: { id: string; name: string; locations: number }[];
  categories: Category[];
};

export type Website = {
  domain: string;
  url: string;
  type: string;
  type_label: string;
  businesses: number;
  group: string | null;
  organization: { id: string; name: string } | null;
};

export type Category = { key: string; label: string; group?: string | null; count: number };

export type Home = {
  totals: { businesses: number; companies: number; cities: number; websites: number; with_phone: number; updated_at: string };
  groups: Count[];
  provinces: Count[];
  top_cities: City[];
  top_companies: Company[];
};

export type SearchResult = {
  categories: Category[];
  cities: City[];
  businesses: Place[];
  organizations: Company[];
  websites: Website[];
};
