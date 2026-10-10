import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Actions, detailStyles as s, Fact, Pill } from '../components/detail';
import { PlaceRow } from '../components/rows';
import { SaveButton } from '../components/SaveButton';
import { Card, ErrorView, GroupIcon, IconCircle, Loading, Row, Section } from '../components/ui';
import { browse, call, copy, directions, email, sharePlace } from '../lib/actions';
import { get, type PlaceDetail } from '../lib/api';
import { hostname, plural } from '../lib/format';
import type { IconName } from '../lib/icons';
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
  actions.push({ icon: 'share-outline', label: 'Share', onPress: () => sharePlace(p) });

  return (
    <ScrollView style={{ backgroundColor: c.background }} contentContainerStyle={styles.bottom}>
      <View style={s.hero}>
        <GroupIcon group={p.group} size={52} />
        <Text style={[s.heroTitle, { color: c.text }]} selectable>{p.name}</Text>
        <Text style={[s.heroSub, { color: c.muted }]}>{[p.category, [p.area, p.city].filter(Boolean).join(', ')].filter(Boolean).join(' · ')}</Text>
        <View style={s.pills}>
          {p.group ? <Pill text={p.group} /> : null}
          {p.brand && p.brand !== p.name ? <Pill icon="pricetag-outline" text={p.brand} /> : null}
        </View>
      </View>

      <Actions items={actions} />

      <Section title="Details">
        <Card style={s.card}>
          <Fact icon="location-outline" label="Address" value={p.address || [p.area, p.city, p.province].filter(Boolean).join(', ')} onLongPress={() => copy(p.address || '', 'Address copied')} />
          <Fact icon="call-outline" label="Phone" value={p.phone} onPress={() => call(p.phone!)} onLongPress={() => copy(p.phone!, 'Phone number copied')} />
          <Fact icon="mail-outline" label="Email" value={p.email} onPress={() => email(p.email!)} onLongPress={() => copy(p.email!, 'Email copied')} />
          <Fact icon="globe-outline" label="Website" value={hostname(p.website)} onPress={() => browse(p.website!)} />
          <Fact icon="map-outline" label="Area" value={[p.area, p.city, p.district, p.province].filter(Boolean).join(', ')} />
          <Fact icon="mail-unread-outline" label="Postal code" value={p.postal_code} last />
        </Card>
        {!p.phone && !p.website && !p.email ? (
          <Text style={[styles.note, { color: c.faint }]}>No contact details listed for this place yet.</Text>
        ) : (
          <Text style={[styles.note, { color: c.faint }]}>Tip: press and hold a phone number or address to copy it.</Text>
        )}
      </Section>

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
        <Section title="Nearby">
          <Card style={s.card}>
            {p.nearby.map((n, i) => <PlaceRow key={n.id} place={n} last={i === p.nearby.length - 1} />)}
          </Card>
        </Section>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  bottom: { paddingBottom: space.xl * 2 },
  note: { fontSize: 13.5, paddingHorizontal: space.lg, marginTop: space.sm },
});
