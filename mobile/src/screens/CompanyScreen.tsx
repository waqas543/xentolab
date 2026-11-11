import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Actions, Bar, detailStyles as s, Fact, Pill } from '../components/detail';
import { SaveButton } from '../components/SaveButton';
import { Callout, Footnote, StatTiles } from '../components/insights';
import { PlaceRow } from '../components/rows';
import { Card, ErrorView, GroupIcon, IconCircle, Loading, Row, Section } from '../components/ui';
import { browse } from '../lib/actions';
import { get, type CompanyDetail, type CompanyInsights } from '../lib/api';
import { categoryNoun, count, hostname, monthYear, ordinal, pct, plural, stars } from '../lib/format';
import type { IconName } from '../lib/icons';
import { groupStyle, space, useColors } from '../lib/theme';
import { useNav, type ScreenProps } from '../navigation/types';

export function CompanyScreen({ route, navigation }: ScreenProps<'Company'>) {
  const { id } = route.params;
  const c = useColors();
  const company = useQuery({ queryKey: ['company', id], queryFn: () => get<CompanyDetail>(`/organizations/${id}`) });
  const o = company.data;
  const ins = o?.insights ?? null;

  useLayoutEffect(() => {
    const item = o ? { kind: 'company' as const, id: o.id, title: o.name, subtitle: o.industry ?? o.group ?? undefined, group: o.group } : null;
    navigation.setOptions({ headerRight: () => <SaveButton item={item} /> });
  }, [navigation, o]);

  if (company.isPending) return <Loading />;
  if (company.isError || !o) return <ErrorView error={company.error} onRetry={() => company.refetch()} />;

  const showLocations = (city?: { id: string | null; name: string }) =>
    navigation.push('Places', { organization: id, city_id: city?.id ?? undefined, city: city?.id ? undefined : city?.name,
                                title: city ? `${o.name} in ${city.name}` : o.name });
  const actions: { icon: IconName; label: string; onPress: () => void; primary?: boolean }[] = [];
  if (o.locations > 0) actions.push({ icon: 'list', label: 'All locations', onPress: () => showLocations(), primary: true });
  if (o.website) actions.push({ icon: 'globe-outline', label: 'Website', onPress: () => browse(o.website!), primary: o.locations === 0 });

  return (
    <ScrollView style={{ backgroundColor: c.background }} contentContainerStyle={styles.bottom}>
      <View style={s.hero}>
        <GroupIcon group={o.group} size={52} />
        <Text style={[s.heroTitle, { color: c.text }]} selectable>{o.name}</Text>
        {o.description ? <Text style={[s.heroSub, { color: c.muted }]}>{o.description[0].toUpperCase() + o.description.slice(1)}</Text> : null}
        <View style={s.pills}>
          {o.group ? <Pill text={o.group} /> : null}
          {o.locations > 0 ? <Pill icon="storefront-outline" text={plural(o.locations, 'location')} /> : null}
          {o.founded ? <Pill icon="calendar-outline" text={`Since ${o.founded}`} /> : null}
        </View>
      </View>

      {actions.length > 0 ? <Actions items={actions} /> : null}

      {ins ? <MarketPosition id={id} name={o.name} insights={ins} /> : null}

      <Section title="About">
        <Card style={s.card}>
          <Fact icon="briefcase-outline" label="Industry" value={o.industry} />
          <Fact icon="document-text-outline" label="Registered name" value={o.legal_name} />
          <Fact icon="business-outline" label="Head office" value={o.headquarters} />
          <Fact icon="calendar-outline" label="Founded" value={o.founded} />
          <Fact icon="people-outline" label="Employees" value={o.employees ? count(o.employees) : null} />
          <Fact icon="git-branch-outline" label="Part of" value={o.parent} />
          <Fact icon="pricetags-outline" label="Also known as" value={o.also_known_as.join(', ') || null} />
          <Fact icon="finger-print-outline" label="Registration number" value={o.registration_number} />
          <Fact icon="checkmark-circle-outline" label="Status" value={o.status} />
          <Fact icon="globe-outline" label="Website" value={hostname(o.website)} onPress={() => browse(o.website!)} />
          <Fact
            icon="newspaper-outline"
            label="In the news"
            value={o.news_mentions > 0 ? `${plural(o.news_mentions, 'mention')}${o.last_in_news ? ` · latest ${monthYear(o.last_in_news)}` : ''}` : null}
            last
          />
        </Card>
      </Section>

      {o.top_cities.length > 0 ? (
        <Section title="Where to find them" action="All locations" onAction={() => showLocations()}>
          <Card style={[s.card, styles.bars]}>
            {o.top_cities.map((city) => (
              <Bar key={city.name} label={city.name} value={city.locations} max={o.top_cities[0].locations} tint={groupStyle(o.group).tint} onPress={() => showLocations(city)} />
            ))}
          </Card>
        </Section>
      ) : null}
    </ScrollView>
  );
}

/** Where a brand stands in its market: share and rank, rivals, how its branches rate, where it is missing. */
function MarketPosition({ id, name, insights: ins }: { id: string; name: string; insights: CompanyInsights }) {
  const nav = useNav();
  const c = useColors();
  const noun = categoryNoun(ins.category.label);
  const r = ins.ratings;
  const diff = r.avg_rating !== null && r.category_avg_rating !== null ? r.avg_rating - r.category_avg_rating : null;
  return (
    <>
      <Section title="Market position" action="Market insights" onAction={() => nav.push('Market', { key: ins.category.key })}>
        {ins.rank ? (
          <Callout icon="podium" tone={ins.rank <= 3 ? 'good' : 'info'}
                   title={`${ins.rank === 1 ? 'Largest' : `${ordinal(ins.rank)} largest`} of ${count(ins.brands_in_category)} ${categoryNoun(ins.category.label, false)} brands`}>
            {`${plural(ins.locations_in_category, 'location')}: ${pct(ins.share_pct)} of all ${noun} listed nationwide.`}
          </Callout>
        ) : null}
        <View style={{ marginTop: space.md }}>
          <StatTiles items={[
            { value: pct(ins.share_pct), label: 'Market share' },
            { value: stars(r.avg_rating), label: `Avg rating (sector ${stars(r.category_avg_rating)})` },
            { value: pct(ins.online.with_phone_pct), label: 'Branches with phone' },
          ]} />
        </View>
        {diff !== null && Math.abs(diff) >= 0.1 ? (
          <Footnote>{name} is rated {Math.abs(diff).toFixed(1)} stars {diff > 0 ? 'above' : 'below'} the average for {noun} ({r.rated} branches rated).</Footnote>
        ) : null}
      </Section>

      {ins.competitors.length > 0 ? (
        <Section title="Main competitors">
          <Card style={[s.card, styles.bars]}>
            {[...ins.competitors, { id, name: `${name} (this brand)`, locations: ins.locations_in_category }]
              .sort((a, b) => b.locations - a.locations)
              .map((x, _i, all) => (
                <Bar key={x.id} label={x.name} value={x.locations} max={all[0].locations}
                     tint={x.id === id ? c.primary : c.faint}
                     onPress={x.id === id ? undefined : () => nav.push('Company', { id: x.id })} />
              ))}
          </Card>
        </Section>
      ) : null}

      {r.best.length > 0 ? (
        <Section title="Best-rated branches">
          <Card style={s.card}>{r.best.map((p, i) => <PlaceRow key={p.id} place={p} last={i === r.best.length - 1} />)}</Card>
        </Section>
      ) : null}
      {r.weakest.length > 0 ? (
        <Section title="Lowest-rated branches">
          <Card style={s.card}>{r.weakest.map((p, i) => <PlaceRow key={p.id} place={p} last={i === r.weakest.length - 1} />)}</Card>
          <Footnote>Branches with at least 10 reviews. Useful for customers choosing a branch, and for the brand.</Footnote>
        </Section>
      ) : null}

      {ins.strongest_cities.length > 0 ? (
        <Section title="Strongest cities">
          <Card style={s.card}>
            {ins.strongest_cities.map((x, i) => (
              <Row key={x.id} icon={<IconCircle name="location" size={34} />} title={x.name}
                   subtitle={`${plural(x.locations, 'location')} · ${pct(x.share_pct)} of local ${noun}`}
                   onPress={() => nav.push('Places', { organization: id, city_id: x.id, title: `${name} in ${x.name}` })}
                   last={i === ins.strongest_cities.length - 1} />
            ))}
          </Card>
        </Section>
      ) : null}

      {ins.missing_from.length > 0 ? (
        <Section title="Not present in">
          <Card style={s.card}>
            {ins.missing_from.map((x, i) => (
              <Row key={x.id} icon={<IconCircle name="add-circle-outline" size={34} />} title={x.name}
                   subtitle={`${count(x.category_places)} ${noun}${x.brands_present ? ` · ${plural(x.brands_present, 'other brand')} present` : ''}`}
                   onPress={() => nav.push('Market', { key: ins.category.key, city_id: x.id })}
                   last={i === ins.missing_from.length - 1} />
            ))}
          </Card>
          <Footnote>The cities with the most {noun} where {name} has no listed location.</Footnote>
        </Section>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  bottom: { paddingBottom: space.xl * 2 },
  bars: { paddingVertical: space.sm },
});
