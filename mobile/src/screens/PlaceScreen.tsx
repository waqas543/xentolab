import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Actions, detailStyles as s, Fact, Pill } from '../components/detail';
import { PlaceRow } from '../components/rows';
import { SaveButton } from '../components/SaveButton';
import { Callout, Stars } from '../components/insights';
import { Card, ErrorView, GroupIcon, IconCircle, Loading, Row, Section } from '../components/ui';
import { browse, call, copy, directions, email, sharePlace } from '../lib/actions';
import { get, type PlaceDetail } from '../lib/api';
import { categoryNoun, count, hostname, plural } from '../lib/format';
import { hoursStatus, weekLines } from '../lib/hours';
import { Ionicons, type IconName } from '../lib/icons';
import { space, useColors } from '../lib/theme';
import type { ScreenProps } from '../navigation/types';

export function PlaceScreen({ route, navigation }: ScreenProps<'Place'>) {
  const { id } = route.params;
  const c = useColors();
  const place = useQuery({ queryKey: ['place', id], queryFn: () => get<PlaceDetail>(`/businesses/${id}`) });
  const p = place.data;

  useLayoutEffect(() => {
    const item = p ? { kind: 'place' as const, id: p.id, title: p.name, subtitle: [p.category, p.area, p.city].filter(Boolean).join(' · '), group: p.group } : null;
    navigation.setOptions({ headerRight: () => <SaveButton item={item} /> });
  }, [navigation, p]);

  if (place.isPending) return <Loading />;
  if (place.isError || !p) return <ErrorView error={place.error} onRetry={() => place.refetch()} />;

  const actions: { icon: IconName; label: string; onPress: () => void; primary?: boolean }[] = [];
  if (p.phone) actions.push({ icon: 'call', label: 'Call', onPress: () => call(p.phone!), primary: true });
  actions.push({ icon: 'navigate', label: 'Directions', onPress: () => directions(p), primary: !p.phone });
  if (p.website) actions.push({ icon: 'globe-outline', label: 'Website', onPress: () => browse(p.website!) });
  else if (p.social) actions.push({ icon: socialIcon(p.social), label: 'Page', onPress: () => browse(p.social!) });
  actions.push({ icon: 'share-outline', label: 'Share', onPress: () => sharePlace(p) });
  const status = hoursStatus(p.opening_hours);
  const week = weekLines(p.opening_hours);
  const phoneLabel = p.phone_type === 'hotline' ? 'Phone · head office / helpline'
    : p.phone_type === 'shared' ? 'Phone · shared by several listings' : 'Phone';
  const similar = p.similar_nearby;
  const lower = categoryNoun(p.category);

  return (
    <ScrollView style={{ backgroundColor: c.background }} contentContainerStyle={styles.bottom}>
      <View style={s.hero}>
        <GroupIcon group={p.group} size={52} />
        <Text style={[s.heroTitle, { color: c.text }]} selectable>{p.name}</Text>
        <Text style={[s.heroSub, { color: c.muted }]}>{[p.category, [p.area, p.city].filter(Boolean).join(', ')].filter(Boolean).join(' · ')}</Text>
        {p.local_name ? <Text style={[styles.local, { color: c.muted }]} selectable>{p.local_name}</Text> : null}
        {p.rating ? <Stars rating={p.rating} reviews={p.review_count} size={16} /> : null}
        {status ? (
          <View style={styles.status}>
            <Ionicons name="time" size={16} color={status.open ? c.primary : c.warning} />
            <Text style={[styles.statusText, { color: status.open ? c.primary : c.warning }]}>{status.text}</Text>
          </View>
        ) : null}
        <View style={s.pills}>
          {p.group ? <Pill text={p.group} /> : null}
          {p.subcategory && p.subcategory !== p.category ? <Pill icon="pricetag-outline" text={p.subcategory} /> : null}
          {p.brand && p.brand !== p.name ? <Pill icon="storefront-outline" text={p.brand} /> : null}
        </View>
      </View>

      {p.approximate_location ? (
        <View style={styles.callout}>
          <Callout icon="location-outline" title="Exact location not known">
            {`Listed in ${p.city ?? 'this area'}, but its position on the map isn't reliable. Call or check the address before going.`}
          </Callout>
        </View>
      ) : null}

      <Actions items={actions} />

      <Section title="Details">
        <Card style={s.card}>
          <Fact icon="location-outline" label="Address" value={p.address || [p.area, p.city, p.province].filter(Boolean).join(', ')} onLongPress={() => copy(p.address || '', 'Address copied')} />
          <Fact icon="call-outline" label={phoneLabel} value={p.phone} onPress={() => call(p.phone!)} onLongPress={() => copy(p.phone!, 'Phone number copied')} />
          <Fact icon="mail-outline" label="Email" value={p.email} onPress={() => email(p.email!)} onLongPress={() => copy(p.email!, 'Email copied')} />
          <Fact icon="globe-outline" label="Website" value={hostname(p.website)} onPress={() => browse(p.website!)} />
          <Fact icon={socialIcon(p.social)} label="Social page" value={p.website ? null : hostname(p.social)} onPress={() => browse(p.social!)} />
          <Fact icon="time-outline" label="Opening hours" value={week ? week.map(([d, h]) => `${d}: ${h}`).join('\n') : null} />
          <Fact icon="map-outline" label="Area" value={[p.area, p.city, p.district, p.province].filter(Boolean).join(', ')} />
          <Fact icon="mail-unread-outline" label="Postal code" value={p.postal_code} last />
        </Card>
        {!p.phone && !p.website && !p.email ? (
          <Text style={[styles.note, { color: c.faint }]}>No contact details listed for this place yet.</Text>
        ) : (
          <Text style={[styles.note, { color: c.faint }]}>Tip: press and hold a phone number or address to copy it.</Text>
        )}
      </Section>

      {p.rating_context && p.rating ? (
        <Section title="How it compares">
          <Callout
            icon={p.rating_context.better_than_pct >= 50 ? 'ribbon' : 'stats-chart'}
            tone={p.rating_context.better_than_pct >= 75 ? 'good' : p.rating_context.better_than_pct < 25 ? 'warn' : 'info'}
            title={`Rated higher than ${p.rating_context.better_than_pct}% of ${lower} in ${p.city}`}>
            {`Compared with ${p.rating_context.compared} rated ${lower} there, weighting each rating by its number of reviews.`}
          </Callout>
        </Section>
      ) : null}

      {similar && p.category_key ? (
        <Section
          title="Competition nearby"
          action="Market insights"
          onAction={() => navigation.push('Market', { key: p.category_key!, city_id: p.city_id ?? undefined })}>
          <Text style={[styles.lead, { color: c.text }]}>
            {similar.count === 0
              ? `No other ${lower} listed within ${similar.radius_km} km.`
              : `${count(similar.count)} other ${similar.count === 1 ? categoryNoun(p.category, false) : lower} within ${similar.radius_km} km.`}
          </Text>
          {similar.items.length > 0 ? (
            <Card style={s.card}>
              {similar.items.map((n, i) => <PlaceRow key={n.id} place={n} last={i === similar.items.length - 1} />)}
            </Card>
          ) : null}
        </Section>
      ) : null}

      {p.organization || (p.city_id && p.city) ? (
        <Section title="Related">
          <Card style={s.card}>
            {p.organization ? (
              <Row
                icon={<GroupIcon group={p.group} />}
                title={p.organization.name}
                subtitle={p.organization.industry}
                detail={`Part of a company · ${plural(p.organization.locations, 'location')}`}
                onPress={() => navigation.push('Company', { id: p.organization!.id })}
                last={!p.city_id}
              />
            ) : null}
            {p.city_id && p.city ? (
              <Row
                icon={<IconCircle name="location" />}
                title={`More in ${p.city}`}
                subtitle="Places, areas and brands in this city"
                onPress={() => navigation.push('City', { id: p.city_id! })}
                last
              />
            ) : null}
          </Card>
        </Section>
      ) : null}

      {p.nearby.length > 0 ? (
        <Section title="Also nearby">
          <Card style={s.card}>
            {p.nearby.map((n, i) => <PlaceRow key={n.id} place={n} last={i === p.nearby.length - 1} />)}
          </Card>
        </Section>
      ) : null}
    </ScrollView>
  );
}

function socialIcon(url: string | null | undefined): IconName {
  const host = hostname(url) ?? '';
  if (host.includes('facebook') || host.startsWith('fb.')) return 'logo-facebook';
  if (host.includes('instagram')) return 'logo-instagram';
  if (host.includes('wa.me') || host.includes('whatsapp')) return 'logo-whatsapp';
  if (host.includes('youtube') || host.includes('youtu.be')) return 'logo-youtube';
  if (host.includes('linkedin')) return 'logo-linkedin';
  return 'link-outline';
}

const styles = StyleSheet.create({
  bottom: { paddingBottom: space.xl * 2 },
  local: { fontSize: 17 },
  status: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  statusText: { fontSize: 15, fontWeight: '700' },
  callout: { marginTop: space.sm },
  lead: { fontSize: 15.5, fontWeight: '600', paddingHorizontal: space.lg, marginBottom: space.sm },
  note: { fontSize: 13.5, paddingHorizontal: space.lg, marginTop: space.sm },
});
