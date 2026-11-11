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
  /** The name in its local script, when the listing has one. */
  local_name?: string | null;
  category: string | null;
  category_key: string | null;
  /** The source's own, more detailed category. */
  subcategory?: string | null;
  group: string | null;
  city: string | null;
  city_id?: string | null;
  area: string | null;
  address: string | null;
  phone: string | null;
  /** direct, shared (one number on many listings) or hotline (head office / toll-free). */
  phone_type?: 'direct' | 'shared' | 'hotline' | null;
  website: string | null;
  /** A social media or link page the listing gives instead of a website. */
  social?: string | null;
  email: string | null;
  /** Null when the place's position is unknown (only its town is). */
  lat: number | null;
  lng: number | null;
  approximate_location?: boolean;
  brand: string | null;
  organization_id: string | null;
  /** OpenStreetMap opening_hours syntax, e.g. "24/7" or "Mo 09:00-17:00; Su off". */
  opening_hours?: string | null;
  rating?: number | null;
  review_count?: number | null;
  /** Rating weighted by the number of reviews, for ranking. */
  rating_score?: number | null;
  distance_km?: number;
};

export type PlaceDetail = Place & {
  postal_code: string | null;
  province: string | null;
  district: string | null;
  organization: { id: string; name: string; industry: string | null; locations: number } | null;
  nearby: Place[];
  /** Businesses of the same category within `radius_km`: the competition on the doorstep. */
  similar_nearby: { radius_km: number; count: number; items: Place[] } | null;
  /** How this rating compares with the same category in the same city. */
  rating_context: { compared: number; better_than_pct: number } | null;
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
  insights: CompanyInsights | null;
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
  insights: CityInsights | null;
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
  /** The company behind the site, read from the site itself and other sources. */
  name: string | null;
  summary: string | null;
  industry: string | null;
  city: string | null;
  has_profile: boolean;
};

export type SocialNetwork = 'facebook' | 'instagram' | 'x' | 'linkedin' | 'youtube' | 'tiktok' | 'pinterest' | 'whatsapp' | 'threads';

export type WebsiteDetail = Website & {
  founded: number | null;
  employees: string | null;
  market_cap: string | null;
  revenue: string | null;
  financials: string | null;
  socials: Partial<Record<SocialNetwork, string>>;
  emails: string[];
  phones: string[];
};

export type Category = { key: string; label: string; group?: string | null; count: number };

export type Home = {
  insights?: HomeInsights;
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

// ---------------------------------------------------------------- insights

/**
 * How a category's share of a city's businesses compares with its share nationally. Shares, not raw counts, so
 * cities listed in more detail don't look bigger than they are. 1.0 = the national mix.
 */
export type Verdict = 'much_more' | 'more' | 'typical' | 'fewer' | 'much_fewer';

export type CategoryRef = { key: string; label: string; group: string | null };

export type MarketCity = {
  id: string;
  name: string;
  province: string | null;
  places: number;
  index: number;
  verdict: Verdict | null;
  /** How many there would be if this city had the national mix of businesses. */
  expected: number;
  per_10k_people: number | null;
};

export type Market = {
  category: CategoryRef;
  country: string;
  national: {
    places: number;
    cities: number;
    share_of_all_pct: number | null;
    with_phone_pct: number | null;
    with_website_pct: number | null;
    with_social_pct: number | null;
    rated_pct: number | null;
    avg_rating: number | null;
    open_24h: number;
    in_chains_pct: number | null;
    all_with_website_pct: number | null;
    all_with_phone_pct: number | null;
    all_avg_rating: number | null;
  };
  top_cities: MarketCity[];
  crowded: MarketCity[];
  room_to_grow: MarketCity[];
  city: {
    id: string;
    name: string;
    places: number;
    rank: number;
    share_of_all_pct: number | null;
    index: number | null;
    verdict: Verdict | null;
    expected: number;
    per_10k_people: number | null;
    with_phone_pct: number | null;
    with_website_pct: number | null;
    rated_pct: number | null;
    avg_rating: number | null;
    open_24h: number;
    areas: Count[];
  } | null;
  brands: { id: string; name: string; locations: number; share_pct: number | null }[];
  top_rated: Place[];
};

export type CityInsights = {
  stands_out: (CategoryRef & { places: number; index: number })[];
  fewer_than_usual: (CategoryRef & { places: number; expected: number })[];
  online: {
    with_website_pct: number | null;
    with_phone_pct: number | null;
    with_social_pct: number | null;
    national_with_website_pct: number | null;
    national_with_phone_pct: number | null;
  };
  ratings: { rated: number; avg_rating: number | null; national_avg_rating: number | null };
  open_24h: number;
  top_rated: Place[];
};

export type CompanyInsights = {
  category: CategoryRef;
  locations_in_category: number;
  share_pct: number | null;
  rank: number | null;
  brands_in_category: number;
  competitors: { id: string; name: string; locations: number }[];
  online: { with_phone_pct: number | null; with_website_pct: number | null };
  ratings: { rated: number; avg_rating: number | null; category_avg_rating: number | null; best: Place[]; weakest: Place[] };
  strongest_cities: { id: string; name: string; locations: number; share_pct: number | null }[];
  missing_from: { id: string; name: string; category_places: number; brands_present: number }[];
};

export type HomeInsights = {
  country: string;
  with_website_pct: number | null;
  with_phone_pct: number | null;
  rated: number;
  avg_rating: number | null;
  open_24h: number;
  markets: (CategoryRef & { places: number; cities: number })[];
};
