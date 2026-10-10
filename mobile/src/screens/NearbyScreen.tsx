import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';
import { Linking, StyleSheet, Text, View } from 'react-native';

import { PagedList } from '../components/PagedList';
import { PlaceRow } from '../components/rows';
import { Chip, ChipRow, Loading, Message } from '../components/ui';
import { get, type Home, type Place } from '../lib/api';
import { locate, type Located } from '../lib/location';
import { space, useColors } from '../lib/theme';
import { useNav } from '../navigation/types';

const RADII = [1, 3, 10, 25];

export function NearbyScreen() {
  const c = useColors();
  const nav = useNav();
  const [state, setState] = useState<Located | { status: 'locating' }>({ status: 'locating' });
  const [radius, setRadius] = useState(3);
  const [group, setGroup] = useState<string | undefined>();
  const [hasPhone, setHasPhone] = useState(false);
  const home = useQuery({ queryKey: ['home'], queryFn: () => get<Home>('/home') });

  const refresh = useCallback(async () => {
    setState({ status: 'locating' });
    setState(await locate());
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (state.status === 'locating') return <Loading label="Finding your location…" />;
  if (state.status === 'denied') {
    return (
      <Message
        icon="navigate-outline"
        title="Location is off"
        text="Allow location to see places around you. You can still browse any city."
        action={state.canAsk ? 'Allow location' : 'Open settings'}
        onAction={state.canAsk ? refresh : () => Linking.openSettings()}
      />
    );
  }
  if (state.status === 'failed') {
    return <Message icon="warning-outline" title="Couldn't find your location" text="Make sure location is turned on, then try again." action="Try again" onAction={refresh} />;
  }

  const header = (
    <View style={styles.header}>
      <Text style={[styles.label, { color: c.muted }]}>Distance</Text>
      <ChipRow>
        {RADII.map((r) => <Chip key={r} label={`${r} km`} selected={radius === r} onPress={() => setRadius(r)} />)}
        <Chip label="Has phone" icon="call" selected={hasPhone} onPress={() => setHasPhone(!hasPhone)} />
      </ChipRow>
      <Text style={[styles.label, { color: c.muted }]}>Category</Text>
      <ChipRow>
        <Chip label="All" selected={!group} onPress={() => setGroup(undefined)} />
        {(home.data?.groups ?? []).map((g) => (
          <Chip key={g.name} label={g.name} selected={group === g.name} onPress={() => setGroup(group === g.name ? undefined : g.name)} />
        ))}
      </ChipRow>
    </View>
  );

  const { lat, lng } = state.coords;
  return (
    <PagedList<Place>
      path="/businesses"
      params={{ lat: lat.toFixed(5), lng: lng.toFixed(5), radius_km: radius, group, has_phone: hasPhone, sort: 'distance' }}
      keyOf={(p) => p.id}
      renderItem={(p, last) => <PlaceRow place={p} last={last} />}
      header={header}
      noun={`places within ${radius} km`}
      emptyTitle={`Nothing within ${radius} km`}
      emptyText={radius < 25 ? 'Try a bigger distance, or browse a city instead.' : "We don't have places listed around you yet. Browse a city instead."}
      emptyAction={{ label: 'Browse cities', onPress: () => nav.push('Cities') }}
    />
  );
}

const styles = StyleSheet.create({
  header: { paddingTop: space.sm, gap: space.xs },
  label: { fontSize: 12.5, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5, paddingHorizontal: space.lg, marginTop: space.sm },
});
