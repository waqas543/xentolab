import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect, useState, type ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { PagedList } from '../components/PagedList';
import { CityRow, CompanyRow, PlaceRow, WebsiteRow } from '../components/rows';
import { SearchBox } from '../components/SearchBox';
import { Chip, ChipRow } from '../components/ui';
import { get, type Category, type City, type Company, type Home, type Place, type Website } from '../lib/api';
import { useDebounced } from '../lib/hooks';
import { Ionicons } from '../lib/icons';
import { space, useColors } from '../lib/theme';
import type { ScreenProps } from '../navigation/types';

function useHome() {
  return useQuery({ queryKey: ['home'], queryFn: () => get<Home>('/home') });
}

function FilterHeader({ children }: { children: ReactNode }) {
  const c = useColors();
  return <View style={[styles.header, { backgroundColor: c.card, borderBottomColor: c.border }]}>{children}</View>;
}

/** Places matching any mix of filters passed in from other screens, refined here with chips. */
export function PlacesScreen({ route, navigation }: ScreenProps<'Places'>) {
  const c = useColors();
  const base = route.params ?? {};
  const [category, setCategory] = useState(base.category);
  const [hasPhone, setHasPhone] = useState(!!base.has_phone);
  const [hasWebsite, setHasWebsite] = useState(false);
  const [open24h, setOpen24h] = useState(!!base.open_24h);
  const [sort, setSort] = useState<'best' | 'rating' | 'name'>(base.sort ?? 'best');
  const sorts = { best: 'Most details first', rating: 'Top rated', name: 'A to Z' } as const;
  const next = { best: 'rating', rating: 'name', name: 'best' } as const;
  const categories = useQuery({
    queryKey: ['categories', base.group],
    queryFn: () => get<Category[]>('/categories', { group: base.group, limit: 15 }),
    enabled: !!base.group && !base.category,
  });

  useLayoutEffect(() => {
    navigation.setOptions({ title: base.title ?? 'All places' });
  }, [navigation, base.title]);

  const header = (
    <FilterHeader>
      {category ? (
        <Pressable
          onPress={() => navigation.push('Market', { key: category, city_id: base.city_id })}
          accessibilityRole="button"
          style={({ pressed }) => [styles.insight, { backgroundColor: c.primarySoft, opacity: pressed ? 0.75 : 1 }]}>
          <Ionicons name="analytics" size={20} color={c.primary} />
          <Text style={[styles.insightText, { color: c.primary }]}>Market insights: competition, top brands, ratings</Text>
          <Ionicons name="chevron-forward" size={18} color={c.primary} />
        </Pressable>
      ) : null}
      <ChipRow>
        <Chip label={sorts[sort]} icon="swap-vertical" onPress={() => setSort(next[sort])} />
        <Chip label="Has phone" icon="call" selected={hasPhone} onPress={() => setHasPhone(!hasPhone)} />
        <Chip label="Has website" icon="globe-outline" selected={hasWebsite} onPress={() => setHasWebsite(!hasWebsite)} />
        <Chip label="Open 24 hours" icon="time-outline" selected={open24h} onPress={() => setOpen24h(!open24h)} />
      </ChipRow>
      {categories.data && categories.data.length > 1 ? (
        <ChipRow>
          <Chip label="All types" selected={!category} onPress={() => setCategory(undefined)} />
          {categories.data.map((cat) => (
            <Chip key={cat.key} label={cat.label} selected={category === cat.key} onPress={() => setCategory(category === cat.key ? undefined : cat.key)} />
          ))}
        </ChipRow>
      ) : null}
    </FilterHeader>
  );

  return (
    <PagedList<Place>
      path="/businesses"
      params={{ ...base, title: undefined, category, has_phone: hasPhone, has_website: hasWebsite, open_24h: open24h, sort }}
      keyOf={(p) => p.id}
      renderItem={(p, last) => <PlaceRow place={p} last={last} />}
      header={header}
      noun="places"
    />
  );
}

export function CompaniesScreen({ route }: ScreenProps<'Companies'>) {
  const [text, setText] = useState(route.params?.q ?? '');
  const q = useDebounced(text.trim());
  const [group, setGroup] = useState<string | undefined>();
  const [sort, setSort] = useState<'locations' | 'name' | 'news'>('locations');
  const home = useHome();
  const sorts = { locations: 'Most locations', name: 'A to Z', news: 'Most in the news' } as const;
  const next = { locations: 'name', name: 'news', news: 'locations' } as const;

  const header = (
    <FilterHeader>
      <View style={styles.search}><SearchBox value={text} onChangeText={setText} placeholder="Search companies and brands" /></View>
      <ChipRow>
        <Chip label={sorts[sort]} icon="swap-vertical" onPress={() => setSort(next[sort])} />
        {(home.data?.groups ?? []).map((g) => (
          <Chip key={g.name} label={g.name} selected={group === g.name} onPress={() => setGroup(group === g.name ? undefined : g.name)} />
        ))}
      </ChipRow>
    </FilterHeader>
  );

  return (
    <PagedList<Company>
      path="/organizations"
      params={{ q, group, sort }}
      keyOf={(o) => o.id}
      renderItem={(o, last) => <CompanyRow company={o} last={last} />}
      header={header}
      noun="companies"
    />
  );
}

export function CitiesScreen() {
  const [text, setText] = useState('');
  const q = useDebounced(text.trim());
  const [province, setProvince] = useState<string | undefined>();
  const [sort, setSort] = useState<'businesses' | 'population' | 'name'>('businesses');
  const home = useHome();
  const sorts = { businesses: 'Most places', population: 'Most people', name: 'A to Z' } as const;
  const next = { businesses: 'population', population: 'name', name: 'businesses' } as const;

  const header = (
    <FilterHeader>
      <View style={styles.search}><SearchBox value={text} onChangeText={setText} placeholder="Search cities, towns and villages" /></View>
      <ChipRow>
        <Chip label={sorts[sort]} icon="swap-vertical" onPress={() => setSort(next[sort])} />
        {(home.data?.provinces ?? []).map((p) => (
          <Chip key={p.name} label={p.name} selected={province === p.name} onPress={() => setProvince(province === p.name ? undefined : p.name)} />
        ))}
      </ChipRow>
    </FilterHeader>
  );

  return (
    <PagedList<City>
      path="/cities"
      params={{ q, province, sort }}
      keyOf={(x) => x.id}
      renderItem={(x, last) => <CityRow city={x} last={last} />}
      header={header}
      noun="cities, towns and villages"
    />
  );
}

const WEBSITE_TYPES = [
  { key: 'business', label: 'Business' },
  { key: 'education', label: 'Education' },
  { key: 'government', label: 'Government' },
  { key: 'nonprofit', label: 'Non-profit' },
  { key: 'general', label: 'General' },
];

export function WebsitesScreen({ route }: ScreenProps<'Websites'>) {
  const [text, setText] = useState(route.params?.q ?? '');
  const q = useDebounced(text.trim());
  const [type, setType] = useState<string | undefined>();

  const header = (
    <FilterHeader>
      <View style={styles.search}><SearchBox value={text} onChangeText={setText} placeholder="Search websites" /></View>
      <ChipRow>
        <Chip label="All" selected={!type} onPress={() => setType(undefined)} />
        {WEBSITE_TYPES.map((t) => (
          <Chip key={t.key} label={t.label} selected={type === t.key} onPress={() => setType(type === t.key ? undefined : t.key)} />
        ))}
      </ChipRow>
    </FilterHeader>
  );

  return (
    <PagedList<Website>
      path="/websites"
      params={{ q, type }}
      keyOf={(w) => w.domain}
      renderItem={(w, last) => <WebsiteRow site={w} last={last} />}
      header={header}
      noun="websites"
    />
  );
}

const styles = StyleSheet.create({
  header: { paddingVertical: space.sm, gap: space.xs, borderBottomWidth: StyleSheet.hairlineWidth },
  search: { paddingHorizontal: space.lg, paddingBottom: space.xs },
  insight: { flexDirection: 'row', alignItems: 'center', gap: space.sm, marginHorizontal: space.lg, marginBottom: space.xs, paddingHorizontal: space.md, paddingVertical: 10, borderRadius: 12 },
  insightText: { flex: 1, fontSize: 14, fontWeight: '700' },
});
