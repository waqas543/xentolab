import { useQuery } from '@tanstack/react-query';
import { StyleSheet, Text, View } from 'react-native';

import { Actions, detailStyles as s, Fact, Pill } from '../components/detail';
import { PagedList } from '../components/PagedList';
import { PlaceRow } from '../components/rows';
import { Card, ErrorView, IconCircle, Loading, Section } from '../components/ui';
import { browse, call, copy, email } from '../lib/actions';
import { get, type Place, type SocialNetwork, type WebsiteDetail } from '../lib/api';
import { hostname } from '../lib/format';
import type { IconName } from '../lib/icons';
import { space, useColors } from '../lib/theme';
import type { ScreenProps } from '../navigation/types';

const SOCIALS: { key: SocialNetwork; label: string; icon: IconName }[] = [
  { key: 'facebook', label: 'Facebook', icon: 'logo-facebook' },
  { key: 'instagram', label: 'Instagram', icon: 'logo-instagram' },
  { key: 'x', label: 'X', icon: 'logo-twitter' },
  { key: 'linkedin', label: 'LinkedIn', icon: 'logo-linkedin' },
  { key: 'youtube', label: 'YouTube', icon: 'logo-youtube' },
  { key: 'tiktok', label: 'TikTok', icon: 'logo-tiktok' },
  { key: 'pinterest', label: 'Pinterest', icon: 'logo-pinterest' },
  { key: 'whatsapp', label: 'WhatsApp', icon: 'logo-whatsapp' },
  { key: 'threads', label: 'Threads', icon: 'at-outline' },
];

/** "https://www.facebook.com/acmepk" -> "acmepk"; the network is already in the label. */
function handle(url: string): string {
  const path = url.replace(/^https?:\/\/[^/]+\/?/, '').replace(/\/$/, '');
  return decodeURIComponent(path) || hostname(url) || url;
}

export function WebsiteScreen({ route, navigation }: ScreenProps<'Website'>) {
  const { domain } = route.params;
  const c = useColors();
  const site = useQuery({ queryKey: ['website', domain], queryFn: () => get<WebsiteDetail>(`/websites/${encodeURIComponent(domain)}`) });
  const w = site.data;

  if (site.isPending) return <Loading />;
  if (site.isError || !w) return <ErrorView error={site.error} onRetry={() => site.refetch()} />;

  const actions: { icon: IconName; label: string; onPress: () => void; primary?: boolean }[] = [
    { icon: 'open-outline', label: 'Open website', onPress: () => browse(w.url), primary: true },
  ];
  if (w.phones.length > 0) actions.push({ icon: 'call-outline', label: 'Call', onPress: () => call(w.phones[0]) });
  if (w.emails.length > 0) actions.push({ icon: 'mail-outline', label: 'Email', onPress: () => email(w.emails[0]) });
  if (w.organization) {
    const org = w.organization;
    actions.push({ icon: 'business-outline', label: org.name, onPress: () => navigation.push('Company', { id: org.id }) });
  }

  const socials = SOCIALS.filter((n) => w.socials[n.key]);
  const about = [w.industry, w.city, w.founded, w.employees, w.revenue, w.market_cap, w.financials].some(Boolean);
  const contacts = [...w.phones.map((p) => ({ kind: 'phone' as const, value: p })), ...w.emails.map((e) => ({ kind: 'email' as const, value: e }))];

  const header = (
    <View style={[styles.header, { backgroundColor: c.background }]}>
      <View style={s.hero}>
        <IconCircle name="globe-outline" size={52} />
        <Text style={[s.heroTitle, { color: c.text }]} selectable>{w.name ?? hostname(w.url) ?? w.domain}</Text>
        {w.name ? <Text style={[s.heroSub, { color: c.muted }]}>{hostname(w.url) ?? w.domain}</Text> : null}
        {w.summary ? <Text style={[s.heroSub, styles.summary, { color: c.text }]}>{w.summary}</Text> : null}
        <View style={s.pills}>
          <Pill text={w.type_label} />
          {w.group ? <Pill text={w.group} /> : null}
          {w.city ? <Pill icon="location-outline" text={w.city} /> : null}
          {w.founded ? <Pill icon="calendar-outline" text={`Since ${w.founded}`} /> : null}
        </View>
      </View>
      <Actions items={actions} />

      {about ? (
        <Section title="About">
          <Card style={s.card}>
            <Fact icon="briefcase-outline" label="Industry" value={w.industry} />
            <Fact icon="location-outline" label="Based in" value={w.city} />
            <Fact icon="calendar-outline" label="Founded" value={w.founded ? String(w.founded) : null} />
            <Fact icon="people-outline" label="Employees" value={w.employees} />
            <Fact icon="cash-outline" label="Revenue" value={w.revenue} />
            <Fact icon="trending-up-outline" label="Market value" value={w.market_cap} />
            <Fact icon="stats-chart-outline" label="Financials" value={w.financials} last />
          </Card>
        </Section>
      ) : null}

      {contacts.length > 0 ? (
        <Section title="Contact">
          <Card style={s.card}>
            {contacts.map((item, i) => (
              <Fact
                key={`${item.kind}:${item.value}`}
                icon={item.kind === 'phone' ? 'call-outline' : 'mail-outline'}
                label={item.kind === 'phone' ? 'Phone' : 'Email'}
                value={item.value}
                onPress={() => (item.kind === 'phone' ? call(item.value) : email(item.value))}
                onLongPress={() => copy(item.value)}
                last={i === contacts.length - 1}
              />
            ))}
          </Card>
        </Section>
      ) : null}

      {socials.length > 0 ? (
        <Section title="On social media">
          <Card style={s.card}>
            {socials.map((n, i) => (
              <Fact
                key={n.key}
                icon={n.icon}
                label={n.label}
                value={handle(w.socials[n.key]!)}
                onPress={() => browse(w.socials[n.key]!)}
                onLongPress={() => copy(w.socials[n.key]!)}
                last={i === socials.length - 1}
              />
            ))}
          </Card>
        </Section>
      ) : null}
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
  summary: { marginTop: space.xs },
});
