import { useQuery } from '@tanstack/react-query';
import { Image, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CompanyRow } from '../components/rows';
import { StatTiles } from '../components/insights';
import { Card, ErrorView, GroupIcon, Loading, Section } from '../components/ui';
import { get, type Home } from '../lib/api';
import { compact, pct, places, stars } from '../lib/format';
import { Ionicons, type IconName } from '../lib/icons';
import { radius, space, useColors } from '../lib/theme';
import { useNav } from '../navigation/types';

export function HomeScreen() {
  const c = useColors();
  const nav = useNav();
  const insets = useSafeAreaInsets();
  const home = useQuery({ queryKey: ['home'], queryFn: () => get<Home>('/home') });

  return (
    <ScrollView
      style={{ backgroundColor: c.background }}
      contentContainerStyle={{ paddingTop: insets.top + space.lg, paddingBottom: space.xl }}
      refreshControl={<RefreshControl refreshing={home.isRefetching} onRefresh={() => { home.refetch(); }} tintColor={c.primary} colors={[c.primary]} />}>
      <View style={styles.pad}>
        <View style={styles.brandRow}>
          <Image source={require('../assets/logo.png')} style={styles.logo} accessibilityIgnoresInvertColors />
          <View>
            <Text style={[styles.brand, { color: c.text }]}>XentoLab</Text>
            <Text style={[styles.hello, { color: c.muted }]}>Find businesses. Understand markets.</Text>
          </View>
        </View>

        <Pressable
          onPress={() => nav.navigate('Tabs', { screen: 'Search' })}
          accessibilityRole="search"
          style={[styles.fakeSearch, { backgroundColor: c.card, borderColor: c.border }]}>
          <Ionicons name="search" size={20} color={c.muted} />
          <Text style={[styles.fakeSearchText, { color: c.faint }]}>Search a shop, bank, school, city…</Text>
        </Pressable>

        <Pressable
          onPress={() => nav.navigate('Tabs', { screen: 'Nearby' })}
          accessibilityRole="button"
          style={({ pressed }) => [styles.nearMe, { backgroundColor: c.primary, opacity: pressed ? 0.85 : 1 }]}>
          <Ionicons name="navigate" size={22} color={c.onPrimary} />
          <View style={{ flex: 1 }}>
            <Text style={[styles.nearMeTitle, { color: c.onPrimary }]}>What's near me?</Text>
            <Text style={[styles.nearMeText, { color: c.onPrimary }]}>Shops, clinics, banks and more around you</Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color={c.onPrimary} />
        </Pressable>
      </View>

      {home.isPending ? <Loading /> : home.isError ? <ErrorView error={home.error} onRetry={() => home.refetch()} /> : (
        <>
          <View style={[styles.pad, styles.stats]}>
            <Stat value={compact(home.data.totals.businesses)} label="Places" onPress={() => nav.push('Places', {})} />
            <Stat value={compact(home.data.totals.companies)} label="Companies" onPress={() => nav.push('Companies')} />
            <Stat value={compact(home.data.totals.cities)} label="Cities & towns" onPress={() => nav.push('Cities')} />
          </View>

          {home.data.insights ? (
            <Section title="Market insights">
              <Text style={[styles.lead, { color: c.muted }]}>
                Who leads each market, where it's crowded, where there's room to grow.
              </Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.hList}>
                {home.data.insights.markets.map((m) => (
                  <Pressable
                    key={m.key}
                    onPress={() => nav.push('Market', { key: m.key })}
                    accessibilityRole="button"
                    accessibilityLabel={`${m.label} market, ${m.places} places`}
                    style={({ pressed }) => [styles.marketCard, { backgroundColor: c.card, borderColor: c.border, opacity: pressed ? 0.7 : 1 }]}>
                    <GroupIcon group={m.group} size={34} />
                    <Text style={[styles.cityName, { color: c.text }]} numberOfLines={2}>{m.label}</Text>
                    <Text style={[styles.tileCount, { color: c.muted }]}>{places(m.places)}</Text>
                    <View style={styles.marketLink}>
                      <Ionicons name="analytics" size={14} color={c.primary} />
                      <Text style={[styles.marketLinkText, { color: c.primary }]}>Insights</Text>
                    </View>
                  </Pressable>
                ))}
              </ScrollView>
            </Section>
          ) : null}

          {home.data.insights ? (
            <Section title="At a glance">
              <StatTiles items={[
                { value: pct(home.data.insights.with_website_pct), label: 'have their own website' },
                { value: stars(home.data.insights.avg_rating), label: `avg rating (${compact(home.data.insights.rated)} rated)` },
                { value: compact(home.data.insights.open_24h), label: 'open 24 hours', onPress: () => nav.push('Places', { open_24h: true, title: 'Open 24 hours' }) },
              ]} />
            </Section>
          ) : null}

          <Section title="Browse by category">
            <View style={[styles.pad, styles.grid]}>
              {home.data.groups.map((g) => (
                <Pressable
                  key={g.name}
                  onPress={() => nav.push('Places', { group: g.name, title: g.name })}
                  accessibilityRole="button"
                  accessibilityLabel={`${g.name}, ${g.count} places`}
                  style={({ pressed }) => [styles.tile, { backgroundColor: c.card, borderColor: c.border, opacity: pressed ? 0.7 : 1 }]}>
                  <GroupIcon group={g.name} size={36} />
                  <Text style={[styles.tileTitle, { color: c.text }]} numberOfLines={2}>{g.name}</Text>
                  <Text style={[styles.tileCount, { color: c.muted }]}>{compact(g.count)}</Text>
                </Pressable>
              ))}
            </View>
          </Section>

          <Section title="Popular cities" action="All cities" onAction={() => nav.push('Cities')}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.hList}>
              {home.data.top_cities.map((city) => (
                <Pressable
                  key={city.id}
                  onPress={() => nav.push('City', { id: city.id })}
                  style={({ pressed }) => [styles.cityCard, { backgroundColor: c.card, borderColor: c.border, opacity: pressed ? 0.7 : 1 }]}>
                  <Ionicons name="location" size={18} color={c.primary} />
                  <Text style={[styles.cityName, { color: c.text }]} numberOfLines={1}>{city.name}</Text>
                  <Text style={[styles.tileCount, { color: c.muted }]}>{places(city.businesses)}</Text>
                </Pressable>
              ))}
            </ScrollView>
          </Section>

          <Section title="Big brands & chains" action="See all" onAction={() => nav.push('Companies')}>
            <Card style={styles.marginH}>
              {home.data.top_companies.slice(0, 6).map((company, i, all) => (
                <CompanyRow key={company.id} company={company} last={i === all.length - 1} />
              ))}
            </Card>
          </Section>

          <Section title="More to explore">
            <Card style={styles.marginH}>
              <ExploreRow icon="business-outline" title="Companies & brands" text={`${compact(home.data.totals.companies)} organisations`} onPress={() => nav.push('Companies')} />
              <ExploreRow icon="map-outline" title="Cities & towns" text={`${places(home.data.totals.cities)} with businesses`} onPress={() => nav.push('Cities')} />
              <ExploreRow icon="globe-outline" title="Websites" text={`${compact(home.data.totals.websites)} business websites`} onPress={() => nav.push('Websites')} />
              <ExploreRow icon="call-outline" title="Places with a phone number" text={`${compact(home.data.totals.with_phone)} you can call`} onPress={() => nav.push('Places', { has_phone: true, title: 'Places you can call' })} last />
            </Card>
          </Section>

          <Section title="By region">
            <Card style={styles.marginH}>
              {home.data.provinces.map((p, i, all) => (
                <ExploreRow
                  key={p.name}
                  icon="flag-outline"
                  title={p.name}
                  text={places(p.count)}
                  onPress={() => nav.push('Places', { province: p.name, title: p.name })}
                  last={i === all.length - 1}
                />
              ))}
            </Card>
          </Section>
        </>
      )}
    </ScrollView>
  );
}

function Stat({ value, label, onPress }: { value: string; label: string; onPress: () => void }) {
  const c = useColors();
  return (
    <Pressable onPress={onPress} style={[styles.stat, { backgroundColor: c.card, borderColor: c.border }]} accessibilityRole="button">
      <Text style={[styles.statValue, { color: c.text }]}>{value}</Text>
      <Text style={[styles.statLabel, { color: c.muted }]} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

function ExploreRow({ icon, title, text, onPress, last }: { icon: IconName; title: string; text: string; onPress: () => void; last?: boolean }) {
  const c = useColors();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      style={({ pressed }) => [styles.exploreRow, { backgroundColor: pressed ? c.chip : c.card, borderBottomColor: c.border, borderBottomWidth: last ? 0 : StyleSheet.hairlineWidth }]}>
      <Ionicons name={icon} size={22} color={c.primary} />
      <View style={{ flex: 1 }}>
        <Text style={[styles.exploreTitle, { color: c.text }]}>{title}</Text>
        <Text style={[styles.tileCount, { color: c.muted }]}>{text}</Text>
      </View>
      <Ionicons name="chevron-forward" size={18} color={c.faint} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pad: { paddingHorizontal: space.lg },
  marginH: { marginHorizontal: space.lg },
  hello: { fontSize: 15, fontWeight: '500' },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, marginBottom: space.lg },
  logo: { width: 40, height: 40 },
  brand: { fontSize: 28, fontWeight: '800', letterSpacing: -0.5, lineHeight: 32 },
  fakeSearch: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderWidth: 1, borderRadius: radius.md, paddingHorizontal: space.md, minHeight: 52 },
  fakeSearchText: { fontSize: 16 },
  nearMe: { flexDirection: 'row', alignItems: 'center', gap: space.md, borderRadius: radius.lg, padding: space.lg, marginTop: space.md },
  nearMeTitle: { fontSize: 17, fontWeight: '700' },
  nearMeText: { fontSize: 13.5, opacity: 0.9, marginTop: 2 },
  stats: { flexDirection: 'row', gap: space.sm, marginTop: space.lg },
  stat: { flex: 1, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, paddingVertical: space.md, alignItems: 'center' },
  statValue: { fontSize: 22, fontWeight: '800' },
  statLabel: { fontSize: 12.5, fontWeight: '500', marginTop: 2 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  tile: { width: '31.8%', borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, padding: space.md, gap: 6, minHeight: 116 },
  tileTitle: { fontSize: 14, fontWeight: '600', lineHeight: 18 },
  tileCount: { fontSize: 13 },
  hList: { gap: space.sm, paddingHorizontal: space.lg },
  lead: { fontSize: 14, paddingHorizontal: space.lg, marginBottom: space.sm, marginTop: -2 },
  marketCard: { width: 148, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, padding: space.md, gap: 4 },
  marketLink: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 4 },
  marketLinkText: { fontSize: 13, fontWeight: '700' },
  cityCard: { width: 128, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, padding: space.md, gap: 4 },
  cityName: { fontSize: 16, fontWeight: '700' },
  exploreRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingVertical: space.md, minHeight: 60 },
  exploreTitle: { fontSize: 16, fontWeight: '600' },
});
