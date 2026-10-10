import { useQuery } from '@tanstack/react-query';
import { useLayoutEffect } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Actions, Bar, detailStyles as s, Fact, Pill } from '../components/detail';
import { SaveButton } from '../components/SaveButton';
import { Card, ErrorView, GroupIcon, Loading, Section } from '../components/ui';
import { browse } from '../lib/actions';
import { get, type CompanyDetail } from '../lib/api';
import { count, hostname, monthYear, plural } from '../lib/format';
import type { IconName } from '../lib/icons';
import { groupStyle, space, useColors } from '../lib/theme';
import type { ScreenProps } from '../navigation/types';

export function CompanyScreen({ route, navigation }: ScreenProps<'Company'>) {
  const { id } = route.params;
  const c = useColors();
  const company = useQuery({ queryKey: ['company', id], queryFn: () => get<CompanyDetail>(`/organizations/${id}`) });
  const o = company.data;

  useLayoutEffect(() => {
    const item = o ? { kind: 'company' as const, id: o.id, title: o.name, subtitle: o.industry ?? o.group ?? undefined, group: o.group } : null;
    navigation.setOptions({ headerRight: () => <SaveButton item={item} /> });
  }, [navigation, o]);

  if (company.isPending) return <Loading />;
  if (company.isError || !o) return <ErrorView error={company.error} onRetry={() => company.refetch()} />;

  const showLocations = (city?: string) =>
    navigation.push('Places', { organization: id, city, title: city ? `${o.name} in ${city}` : o.name });
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
              <Bar key={city.name} label={city.name} value={city.locations} max={o.top_cities[0].locations} tint={groupStyle(o.group).tint} onPress={() => showLocations(city.name)} />
            ))}
          </Card>
        </Section>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  bottom: { paddingBottom: space.xl * 2 },
  bars: { paddingVertical: space.sm },
});
