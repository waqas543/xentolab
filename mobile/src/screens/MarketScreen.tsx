import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Actions, Bar, detailStyles as s, Pill } from '../components/detail';
import { Callout, CompareBar, Footnote, StatTiles, verdictText } from '../components/insights';
import { PlaceRow } from '../components/rows';
import { Card, ErrorView, GroupIcon, IconCircle, Loading, Row, Section } from '../components/ui';
import { get, type Market, type MarketCity } from '../lib/api';
import { categoryNoun, compact, count, ordinal, pct, plural, stars } from '../lib/format';
import { groupStyle, space, useColors } from '../lib/theme';
import type { ScreenProps } from '../navigation/types';

/**
 * A category's market: how big it is, where it is crowded or thin, who leads it, how customers rate it and how
 * easy it is to reach. With a city, the same questions answered for that city against the national picture.
 */
export function MarketScreen({ route, navigation }: ScreenProps<'Market'>) {
  const { key, city_id } = route.params;
  const c = useColors();
  const market = useQuery({ queryKey: ['market', key, city_id], queryFn: () => get<Market>(`/insights/categories/${key}`, { city_id }) });
  const m = market.data;

  useLayoutEffect(() => {
    navigation.setOptions({ title: m ? `${m.category.label}${m.city ? ` in ${m.city.name}` : ''}` : 'Market' });
  }, [navigation, m]);

  if (market.isPending) return <Loading label="Reading the market…" />;
  if (market.isError || !m) return <ErrorView error={market.error} onRetry={() => market.refetch()} />;

  const label = m.category.label;
  const lower = categoryNoun(label);
  const n = m.national;
  const city = m.city;
  const scope = city ? `in ${city.name}` : 'nationwide';
  const openCity = (x: { id: string }) => navigation.push('Market', { key, city_id: x.id });
  const list = (extra: object, title: string) =>
    navigation.push('Places', { category: key, city_id: city?.id, title, ...extra });

  return (
    <ScrollView style={{ backgroundColor: c.background }} contentContainerStyle={styles.bottom}>
      <View style={s.hero}>
        <GroupIcon group={m.category.group} size={52} />
        <Text style={[s.heroTitle, { color: c.text }]}>{label}</Text>
        <Text style={[s.heroSub, { color: c.muted }]}>
          {city ? `Market in ${city.name}` : `${plural(n.places, 'place')} in ${plural(n.cities, 'city', 'cities')}`}
        </Text>
        <View style={s.pills}>
          {m.category.group ? <Pill text={m.category.group} /> : null}
          {city ? <Pill icon="podium-outline" text={city.rank === 1 ? 'Most of any city' : `${ordinal(city.rank)} most of any city`} /> : null}
        </View>
      </View>

      {city ? <CityVerdict market={m} /> : null}

      <View style={{ marginTop: space.md }}>
        <StatTiles items={city ? [
          { value: compact(city.places), label: 'Places' },
          { value: city.per_10k_people !== null ? city.per_10k_people.toFixed(1) : '–', label: 'Per 10,000 people' },
          { value: stars(city.avg_rating), label: 'Avg rating' },
          { value: compact(city.open_24h), label: 'Open 24 hours' },
        ] : [
          { value: compact(n.places), label: 'Places' },
          { value: pct(n.share_of_all_pct), label: 'Of all businesses' },
          { value: stars(n.avg_rating), label: 'Avg rating' },
          { value: compact(n.open_24h), label: 'Open 24 hours' },
        ]} />
      </View>

      <Actions items={[
        { icon: 'list', label: `See all`, onPress: () => list({}, `${label} ${scope}`), primary: true },
        { icon: 'star', label: 'Top rated', onPress: () => list({ sort: 'rating' }, `Top rated ${lower} ${scope}`) },
        { icon: 'time-outline', label: 'Open 24h', onPress: () => list({ open_24h: true }, `${label} open 24 hours`) },
      ]} />

      <Section title="How easy they are to reach">
        <Card style={[s.card, styles.padded]}>
          <CompareBar label="List a phone number" value={city ? city.with_phone_pct : n.with_phone_pct}
                      reference={city ? n.with_phone_pct : n.all_with_phone_pct}
                      referenceLabel={city ? `all ${lower} nationally` : 'all businesses'} />
          <CompareBar label="Have their own website" value={city ? city.with_website_pct : n.with_website_pct}
                      reference={city ? n.with_website_pct : n.all_with_website_pct}
                      referenceLabel={city ? `all ${lower} nationally` : 'all businesses'} />
          <CompareBar label="Rated by customers" value={city ? city.rated_pct : n.rated_pct}
                      reference={city ? n.rated_pct : undefined} referenceLabel={`all ${lower} nationally`} />
        </Card>
        <Footnote>
          {(city ? city.with_website_pct ?? 0 : n.with_website_pct ?? 0) < 30
            ? `Most ${lower} ${scope} have no website of their own: a gap for anyone offering online services.`
            : `Many ${lower} ${scope} are already online.`}
        </Footnote>
      </Section>

      {m.brands.length > 0 ? (
        <Section title={`Leading brands ${scope}`}>
          <Card style={s.card}>
            {m.brands.map((b, i) => (
              <Row key={b.id} icon={<GroupIcon group={m.category.group} size={34} />} title={b.name}
                   subtitle={`${plural(b.locations, 'location')} · ${pct(b.share_pct)} of the market`}
                   onPress={() => navigation.push('Company', { id: b.id })} last={i === m.brands.length - 1} />
            ))}
          </Card>
          {n.in_chains_pct !== null ? (
            <Footnote>{pct(100 - n.in_chains_pct)} of {lower} nationwide are independent, not part of a chain.</Footnote>
          ) : null}
        </Section>
      ) : null}

      {m.top_rated.length > 0 ? (
        <Section title={`Top rated ${scope}`} action="More" onAction={() => list({ sort: 'rating' }, `Top rated ${lower} ${scope}`)}>
          <Card style={s.card}>
            {m.top_rated.map((p, i) => <PlaceRow key={p.id} place={p} last={i === m.top_rated.length - 1} />)}
          </Card>
          <Footnote>Ranked by rating weighted by the number of reviews, so a few 5-star reviews don't beat hundreds of good ones.</Footnote>
        </Section>
      ) : null}

      {city && city.areas.length > 0 ? (
        <Section title={`Where in ${city.name}`}>
          <Card style={[s.card, styles.bars]}>
            {city.areas.map((a) => (
              <Bar key={a.name} label={a.name} value={a.count} max={city.areas[0].count} tint={groupStyle(m.category.group).tint}
                   onPress={() => list({ area: a.name }, `${label} in ${a.name}`)} />
            ))}
          </Card>
        </Section>
      ) : null}

      <Section title="Cities with the most">
        <Card style={s.card}>
          {m.top_cities.map((x, i) => <CityLine key={x.id} city={x} onPress={() => openCity(x)} last={i === m.top_cities.length - 1} />)}
        </Card>
      </Section>

      {m.crowded.length > 0 ? (
        <Section title="Crowded markets">
          <Card style={s.card}>
            {m.crowded.map((x, i) => (
              <Row key={x.id} icon={<IconCircle name="people-outline" size={34} color={c.warning} />} title={x.name}
                   subtitle={`${count(x.places)} ${lower} · about ${count(x.expected)} expected`}
                   trailing={`${x.index.toFixed(1)}×`} onPress={() => openCity(x)} last={i === m.crowded.length - 1} />
            ))}
          </Card>
          <Footnote>Big cities where {lower} make up a larger share of businesses than nationally: more competition.</Footnote>
        </Section>
      ) : null}

      {m.room_to_grow.length > 0 ? (
        <Section title="Room to grow">
          <Card style={s.card}>
            {m.room_to_grow.map((x, i) => (
              <Row key={x.id} icon={<IconCircle name="trending-up" size={34} />} title={x.name}
                   subtitle={`${count(x.places)} ${lower} · about ${count(x.expected)} expected`}
                   trailing={`${x.index.toFixed(1)}×`} onPress={() => openCity(x)} last={i === m.room_to_grow.length - 1} />
            ))}
          </Card>
          <Footnote>Big cities with fewer {lower} than their size and business mix suggest: possible openings.</Footnote>
        </Section>
      ) : null}

      {city ? (
        <View style={{ marginTop: space.lg }}>
          <Actions items={[{ icon: 'globe-outline', label: `${label} across the country`, onPress: () => navigation.push('Market', { key }) }]} />
        </View>
      ) : null}

      <Footnote>
        Based on listed businesses. Cities are compared by each category's share of their businesses, so cities
        listed in more detail don't look bigger than they are; "expected" is what the national mix would give.
      </Footnote>
    </ScrollView>
  );
}

function CityVerdict({ market }: { market: Market }) {
  const city = market.city!;
  const lower = categoryNoun(market.category.label);
  const detail = `${pct(city.share_of_all_pct)} of businesses in ${city.name} are ${lower}, against ${pct(market.national.share_of_all_pct)} nationally (about ${count(city.expected)} expected).`;
  if (city.verdict === 'much_more' || city.verdict === 'more') {
    return <Callout icon="people" tone="warn" title={`Crowded: ${city.name} has more ${lower} than usual`}>{detail}</Callout>;
  }
  if (city.verdict === 'much_fewer' || city.verdict === 'fewer') {
    return <Callout icon="trending-up" tone="good" title={`Room to grow: fewer ${lower} than usual`}>{detail}</Callout>;
  }
  return <Callout icon="analytics" title={`About the usual number of ${lower}`}>{detail}</Callout>;
}

function CityLine({ city, onPress, last }: { city: MarketCity; onPress: () => void; last?: boolean }) {
  return (
    <Row
      icon={<IconCircle name="location" size={34} />}
      title={city.name}
      subtitle={[`${count(city.places)} places`, city.per_10k_people !== null ? `${city.per_10k_people.toFixed(1)} per 10k people` : null].filter(Boolean).join(' · ')}
      detail={verdictText(city.verdict)}
      trailing={`${city.index.toFixed(1)}×`}
      onPress={onPress}
      last={last}
    />
  );
}

const styles = StyleSheet.create({
  bottom: { paddingBottom: space.xl * 2 },
  padded: { paddingVertical: space.sm },
  bars: { paddingVertical: space.sm },
});
