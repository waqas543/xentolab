import { useFocusEffect } from '@react-navigation/native';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CityRow, CompanyRow, PlaceRow, WebsiteRow } from '../components/rows';
import { SearchBox, type SearchInput } from '../components/SearchBox';
import { Card, ErrorView, GroupIcon, Loading, Message, Section } from '../components/ui';
import { get, type SearchResult } from '../lib/api';
import { places } from '../lib/format';
import { useDebounced } from '../lib/hooks';
import { Ionicons } from '../lib/icons';
import { useStore } from '../lib/storage';
import { space, useColors } from '../lib/theme';
import { useNav } from '../navigation/types';

const SUGGESTIONS = ['Pharmacy', 'Bank', 'Restaurant', 'School', 'Hospital', 'Fuel', 'Hotel', 'Bakery'];

export function SearchScreen() {
  const c = useColors();
  const nav = useNav();
  const insets = useSafeAreaInsets();
  const input = useRef<SearchInput>(null);
  const { recent, addRecent, clearRecent } = useStore();
  const [text, setText] = useState('');
  const q = useDebounced(text.trim());

  useFocusEffect(useCallback(() => {
    const timer = setTimeout(() => input.current?.focus(), 250);
    return () => clearTimeout(timer);
  }, []));

  const result = useQuery({
    queryKey: ['search', q],
    queryFn: () => get<SearchResult>('/search', { q }),
    enabled: q.length >= 2,
    placeholderData: keepPreviousData,
  });

  const remember = () => addRecent(q);
  const seeAllPlaces = () => {
    remember();
    nav.push('Places', { q, title: `“${q}”` });
  };

  const data = q.length >= 2 ? result.data : undefined;
  // Someone typing a city's name wants the city, not businesses that happen to share it.
  const cityFirst = !!data?.cities.some((x) => x.name.toLowerCase() === q.toLowerCase());
  const citiesSection = data && data.cities.length > 0 ? (
    <Section title="Cities & towns">
      <Card style={styles.marginH}>
        {data.cities.map((city, i) => <CityRow key={city.id} city={city} last={i === data.cities.length - 1} />)}
      </Card>
    </Section>
  ) : null;
  const nothing = data && !data.categories.length && !data.cities.length && !data.businesses.length && !data.organizations.length && !data.websites.length;

  return (
    <View style={{ flex: 1, backgroundColor: c.background, paddingTop: insets.top + space.sm }}>
      <View style={styles.bar}>
        <SearchBox
          ref={input}
          value={text}
          onChangeText={setText}
          placeholder="Shop, bank, school, brand or city"
          onSubmitEditing={() => q.length >= 2 && seeAllPlaces()}
        />
      </View>

      <ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={{ paddingBottom: space.xl }}>
        {q.length < 2 ? (
          <>
            {recent.length > 0 ? (
              <Section title="Recent searches" action="Clear" onAction={clearRecent}>
                <Card style={styles.marginH}>
                  {recent.map((r, i) => (
                    <Pressable key={r} onPress={() => setText(r)} style={[styles.line, { borderBottomColor: c.border, borderBottomWidth: i === recent.length - 1 ? 0 : StyleSheet.hairlineWidth }]}>
                      <Ionicons name="time-outline" size={20} color={c.faint} />
                      <Text style={[styles.lineText, { color: c.text }]}>{r}</Text>
                    </Pressable>
                  ))}
                </Card>
              </Section>
            ) : null}
            <Section title="Try searching for">
              <View style={styles.suggestions}>
                {SUGGESTIONS.map((s) => (
                  <Pressable key={s} onPress={() => setText(s)} style={[styles.suggestion, { backgroundColor: c.chip }]}>
                    <Text style={[styles.suggestionText, { color: c.text }]}>{s}</Text>
                  </Pressable>
                ))}
              </View>
            </Section>
          </>
        ) : result.isPending ? (
          <Loading label="Searching…" />
        ) : result.isError && !data ? (
          <ErrorView error={result.error} onRetry={() => result.refetch()} />
        ) : nothing ? (
          <Message icon="search" title={`No results for “${q}”`} text="Check the spelling, or try a shorter or more general word." />
        ) : data ? (
          <View style={{ opacity: result.isPlaceholderData ? 0.5 : 1 }}>
            {cityFirst ? citiesSection : null}
            {data.categories.length > 0 ? (
              <Section title="Categories">
                <Card style={styles.marginH}>
                  {data.categories.map((cat, i) => (
                    <Pressable
                      key={cat.key}
                      onPress={() => { remember(); nav.push('Places', { category: cat.key, title: cat.label }); }}
                      style={({ pressed }) => [styles.line, { backgroundColor: pressed ? c.chip : c.card, borderBottomColor: c.border, borderBottomWidth: i === data.categories.length - 1 ? 0 : StyleSheet.hairlineWidth }]}>
                      <GroupIcon group={cat.group} size={34} />
                      <Text style={[styles.lineText, styles.bold, { color: c.text }]}>{cat.label}</Text>
                      <Text style={[styles.small, { color: c.muted }]}>{places(cat.count)}</Text>
                    </Pressable>
                  ))}
                </Card>
              </Section>
            ) : null}
            {data.businesses.length > 0 ? (
              <Section title="Places" action="See all" onAction={seeAllPlaces}>
                <Card style={styles.marginH}>
                  {data.businesses.map((p, i) => <PlaceRow key={p.id} place={p} last={i === data.businesses.length - 1} />)}
                </Card>
              </Section>
            ) : null}
            {data.organizations.length > 0 ? (
              <Section title="Companies & brands" action="See all" onAction={() => { remember(); nav.push('Companies', { q }); }}>
                <Card style={styles.marginH}>
                  {data.organizations.map((o, i) => <CompanyRow key={o.id} company={o} last={i === data.organizations.length - 1} />)}
                </Card>
              </Section>
            ) : null}
            {cityFirst ? null : citiesSection}
            {data.websites.length > 0 ? (
              <Section title="Websites" action="See all" onAction={() => nav.push('Websites', { q })}>
                <Card style={styles.marginH}>
                  {data.websites.map((w, i) => <WebsiteRow key={w.domain} site={w} last={i === data.websites.length - 1} />)}
                </Card>
              </Section>
            ) : null}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { paddingHorizontal: space.lg, paddingBottom: space.sm },
  marginH: { marginHorizontal: space.lg },
  line: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingVertical: space.md, minHeight: 52 },
  lineText: { flex: 1, fontSize: 16 },
  bold: { fontWeight: '600' },
  small: { fontSize: 13 },
  suggestions: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, paddingHorizontal: space.lg },
  suggestion: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 999 },
  suggestionText: { fontSize: 15, fontWeight: '500' },
});
