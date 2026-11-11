import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Actions, Bar, detailStyles as s, Pill } from '../components/detail';
import { SaveButton } from '../components/SaveButton';
import { CompareBar, Footnote, StatTiles } from '../components/insights';
import { PlaceRow } from '../components/rows';
import { Card, Chip, ErrorView, GroupIcon, IconCircle, Loading, Row, Section } from '../components/ui';
import { get, type CityDetail } from '../lib/api';
import { compact, count, plural, places, stars, times } from '../lib/format';
import { groupStyle, radius, space, useColors } from '../lib/theme';
import type { PlacesFilters, ScreenProps } from '../navigation/types';

export function CityScreen({ route, navigation }: ScreenProps<'City'>) {
  const { id } = route.params;
  const c = useColors();
  const city = useQuery({ queryKey: ['city', id], queryFn: () => get<CityDetail>(`/cities/${id}`) });
  const x = city.data;

  useLayoutEffect(() => {
    const item = x ? { kind: 'city' as const, id: x.id, title: x.name, subtitle: [x.kind, x.province].filter(Boolean).join(' · ') } : null;
    navigation.setOptions({ headerRight: () => <SaveButton item={item} /> });
  }, [navigation, x]);

  if (city.isPending) return <Loading />;
  if (city.isError || !x) return <ErrorView error={city.error} onRetry={() => city.refetch()} />;

  const openPlaces = (filters: PlacesFilters, title: string) => navigation.push('Places', { city_id: x.id, ...filters, title });
  const ins = x.insights;

  return (
    <ScrollView style={{ backgroundColor: c.background }} contentContainerStyle={styles.bottom}>
      <View style={s.hero}>
        <IconCircle name="location" size={52} />
        <Text style={[s.heroTitle, { color: c.text }]}>
          {x.name}
          {x.local_name ? <Text style={[styles.local, { color: c.muted }]}>  {x.local_name}</Text> : null}
        </Text>
        <Text style={[s.heroSub, { color: c.muted }]}>{[x.kind, x.district, x.province].filter(Boolean).join(' · ')}</Text>
        {x.population ? <View style={s.pills}><Pill icon="people-outline" text={`${compact(x.population)} people`} /></View> : null}
      </View>

      <View style={styles.stats}>
        <Stat value={compact(x.businesses)} label="Places" />
        <Stat value={compact(x.with_phone)} label="With phone" />
        <Stat value={compact(x.companies)} label="Brands" />
      </View>

      <Actions items={[{ icon: 'list', label: `See all ${places(x.businesses)}`, onPress: () => openPlaces({}, `Places in ${x.name}`), primary: true }]} />

      {ins && ins.stands_out.length > 0 ? (
        <Section title="Stands out for">
          <Card style={s.card}>
            {ins.stands_out.map((cat, i) => (
              <Row
                key={cat.key}
                icon={<GroupIcon group={cat.group} size={34} />}
                title={cat.label}
                subtitle={`${count(cat.places)} places · ${times(cat.index)}`}
                onPress={() => navigation.push('Market', { key: cat.key, city_id: x.id })}
                last={i === ins.stands_out.length - 1}
              />
            ))}
          </Card>
          <Footnote>A bigger share of {x.name}'s businesses than nationally: what the city is known for, and where competition is stiff.</Footnote>
        </Section>
      ) : null}

      {ins && ins.fewer_than_usual.length > 0 ? (
        <Section title="Fewer than usual">
          <Card style={s.card}>
            {ins.fewer_than_usual.map((cat, i) => (
              <Row
                key={cat.key}
                icon={<IconCircle name="trending-up" size={34} />}
                title={cat.label}
                subtitle={`${count(cat.places)} listed · about ${count(cat.expected)} expected`}
                onPress={() => navigation.push('Market', { key: cat.key, city_id: x.id })}
                last={i === ins.fewer_than_usual.length - 1}
              />
            ))}
          </Card>
          <Footnote>Common businesses that are thin on the ground here for a city of {x.name}'s size: possible openings, or gaps in our listings.</Footnote>
        </Section>
      ) : null}

      {ins ? (
        <Section title="Business health">
          <StatTiles items={[
            { value: stars(ins.ratings.avg_rating), label: `Avg rating (national ${stars(ins.ratings.national_avg_rating)})` },
            { value: compact(ins.ratings.rated), label: 'Rated by customers' },
            { value: compact(ins.open_24h), label: 'Open 24 hours', onPress: () => openPlaces({ open_24h: true }, `Open 24 hours in ${x.name}`) },
          ]} />
          <Card style={[s.card, styles.padded]}>
            <CompareBar label="Have their own website" value={ins.online.with_website_pct} reference={ins.online.national_with_website_pct} />
            <CompareBar label="List a phone number" value={ins.online.with_phone_pct} reference={ins.online.national_with_phone_pct} />
          </Card>
          {x.per_10k_people ? (
            <Footnote>About {Math.round(x.per_10k_people)} listed places for every 10,000 people.</Footnote>
          ) : null}
        </Section>
      ) : null}

      {ins && ins.top_rated.length > 0 ? (
        <Section title="Top rated" action="More" onAction={() => openPlaces({ sort: 'rating' }, `Top rated in ${x.name}`)}>
          <Card style={s.card}>
            {ins.top_rated.map((p, i) => <PlaceRow key={p.id} place={p} last={i === ins.top_rated.length - 1} />)}
          </Card>
        </Section>
      ) : null}

      {x.categories.length > 0 ? (
        <Section title="Popular here">
          <View style={styles.chips}>
            {x.categories.map((cat) => (
              <Chip key={cat.key} label={`${cat.label} · ${compact(cat.count)}`} onPress={() => navigation.push('Market', { key: cat.key, city_id: x.id })} />
            ))}
          </View>
        </Section>
      ) : null}

      {x.groups.length > 0 ? (
        <Section title="What's here">
          <Card style={[s.card, styles.bars]}>
            {x.groups.map((g) => (
              <Bar key={g.name} label={g.name} value={g.count} max={x.groups[0].count} tint={groupStyle(g.name).tint} onPress={() => openPlaces({ group: g.name }, `${g.name} in ${x.name}`)} />
            ))}
          </Card>
        </Section>
      ) : null}

      {x.areas.length > 0 ? (
        <Section title="Areas">
          <Card style={s.card}>
            {x.areas.map((a, i) => (
              <Row key={a.name} icon={<IconCircle name="navigate-circle-outline" size={34} />} title={a.name} trailing={count(a.count)} onPress={() => openPlaces({ area: a.name }, a.name)} last={i === x.areas.length - 1} />
            ))}
          </Card>
        </Section>
      ) : null}

      {x.brands.length > 0 ? (
        <Section title="Brands in this city">
          <Card style={s.card}>
            {x.brands.map((b, i) => (
              <Row
                key={b.id}
                icon={<GroupIcon group={null} size={34} />}
                title={b.name}
                trailing={plural(b.locations, 'branch', 'branches')}
                onPress={() => openPlaces({ organization: b.id }, `${b.name} in ${x.name}`)}
                last={i === x.brands.length - 1}
              />
            ))}
          </Card>
        </Section>
      ) : null}
    </ScrollView>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  const c = useColors();
  return (
    <View style={[styles.stat, { backgroundColor: c.card, borderColor: c.border }]}>
      <Text style={[styles.statValue, { color: c.text }]}>{value}</Text>
      <Text style={[styles.statLabel, { color: c.muted }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  bottom: { paddingBottom: space.xl * 2 },
  local: { fontWeight: '500' },
  stats: { flexDirection: 'row', gap: space.sm, paddingHorizontal: space.lg, marginBottom: space.sm },
  stat: { flex: 1, alignItems: 'center', paddingVertical: space.md, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth },
  statValue: { fontSize: 22, fontWeight: '800' },
  statLabel: { fontSize: 12.5, marginTop: 2 },
  note: { fontSize: 13.5, paddingHorizontal: space.lg, marginBottom: space.md },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, paddingHorizontal: space.lg },
  bars: { paddingVertical: space.sm },
  padded: { paddingVertical: space.sm, marginTop: space.sm },
});
