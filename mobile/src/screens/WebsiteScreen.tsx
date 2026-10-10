import { useQuery } from '@tanstack/react-query';
import { StyleSheet, Text, View } from 'react-native';

import { Actions, detailStyles as s, Pill } from '../components/detail';
import { PagedList } from '../components/PagedList';
import { PlaceRow } from '../components/rows';
import { ErrorView, IconCircle, Loading } from '../components/ui';
import { browse } from '../lib/actions';
import { get, type Place, type Website } from '../lib/api';
import { hostname } from '../lib/format';
import type { IconName } from '../lib/icons';
import { space, useColors } from '../lib/theme';
import type { ScreenProps } from '../navigation/types';

export function WebsiteScreen({ route, navigation }: ScreenProps<'Website'>) {
  const { domain } = route.params;
  const c = useColors();
  const site = useQuery({ queryKey: ['website', domain], queryFn: () => get<Website>(`/websites/${encodeURIComponent(domain)}`) });
  const w = site.data;

  if (site.isPending) return <Loading />;
  if (site.isError || !w) return <ErrorView error={site.error} onRetry={() => site.refetch()} />;

  const actions: { icon: IconName; label: string; onPress: () => void; primary?: boolean }[] = [
    { icon: 'open-outline', label: 'Open website', onPress: () => browse(w.url), primary: true },
  ];
  if (w.organization) {
    const org = w.organization;
    actions.push({ icon: 'business-outline', label: org.name, onPress: () => navigation.push('Company', { id: org.id }) });
  }

  const header = (
    <View style={[styles.header, { backgroundColor: c.background }]}>
      <View style={s.hero}>
        <IconCircle name="globe-outline" size={52} />
        <Text style={[s.heroTitle, { color: c.text }]} selectable>{hostname(w.url) ?? w.domain}</Text>
        <View style={s.pills}>
          <Pill text={w.type_label} />
          {w.group ? <Pill text={w.group} /> : null}
        </View>
      </View>
      <Actions items={actions} />
    </View>
  );

  return (
    <PagedList<Place>
      path="/businesses"
      params={{ website: w.domain }}
      keyOf={(p) => p.id}
      renderItem={(p, last) => <PlaceRow place={p} last={last} />}
      header={header}
      noun="places use this website"
      emptyTitle="No places linked"
      emptyText="No listed places use this website yet."
    />
  );
}

const styles = StyleSheet.create({
  header: { paddingBottom: space.lg },
});
