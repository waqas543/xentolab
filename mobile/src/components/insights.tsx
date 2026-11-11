import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { Verdict } from '../lib/api';
import { pct, stars } from '../lib/format';
import { Ionicons, type IconName } from '../lib/icons';
import { radius, space, useColors } from '../lib/theme';

/** Star rating with its review count: ★★★★☆ 4.4 (120). */
export function Stars({ rating, reviews, size = 14 }: { rating: number | null | undefined; reviews?: number | null; size?: number }) {
  const c = useColors();
  if (rating === null || rating === undefined) return null;
  const icons: IconName[] = [1, 2, 3, 4, 5].map((i) => (rating >= i - 0.25 ? 'star' : rating >= i - 0.75 ? 'star-half' : 'star-outline'));
  return (
    <View style={styles.stars} accessibilityLabel={`Rated ${stars(rating)} out of 5${reviews ? ` from ${reviews} reviews` : ''}`}>
      {icons.map((name, i) => <Ionicons key={i} name={name} size={size} color={c.star} />)}
      <Text style={[styles.starsText, { color: c.text, fontSize: size }]}>{stars(rating)}</Text>
      {reviews ? <Text style={[styles.starsCount, { color: c.muted, fontSize: size - 1 }]}>({reviews.toLocaleString('en-US')})</Text> : null}
    </View>
  );
}

/** A row of big numbers with small labels. */
export function StatTiles({ items }: { items: { value: string; label: string; onPress?: () => void }[] }) {
  const c = useColors();
  return (
    <View style={styles.tiles}>
      {items.map((t) => (
        <Pressable
          key={t.label}
          onPress={t.onPress}
          disabled={!t.onPress}
          accessibilityRole={t.onPress ? 'button' : undefined}
          style={({ pressed }) => [styles.tile, { backgroundColor: c.card, borderColor: c.border, opacity: pressed ? 0.7 : 1 }]}>
          <Text style={[styles.tileValue, { color: c.text }]} numberOfLines={1} adjustsFontSizeToFit>{t.value}</Text>
          <Text style={[styles.tileLabel, { color: c.muted }]} numberOfLines={2}>{t.label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

/** A share against a reference: the bar is this place, the tick is the national figure. */
export function CompareBar({ label, value, reference, referenceLabel = 'national' }: {
  label: string; value: number | null | undefined; reference?: number | null; referenceLabel?: string;
}) {
  const c = useColors();
  if (value === null || value === undefined) return null;
  const better = reference !== null && reference !== undefined ? value >= reference : true;
  return (
    <View style={styles.compare} accessibilityLabel={`${label}: ${pct(value)}${reference != null ? `, ${referenceLabel} ${pct(reference)}` : ''}`}>
      <View style={styles.compareText}>
        <Text style={[styles.compareLabel, { color: c.text }]}>{label}</Text>
        <Text style={[styles.compareValue, { color: c.text }]}>{pct(value)}</Text>
      </View>
      <View style={[styles.track, { backgroundColor: c.chip }]}>
        <View style={[styles.fill, { width: `${Math.min(100, Math.max(2, value))}%`, backgroundColor: better ? c.primary : c.warning }]} />
        {reference !== null && reference !== undefined ? (
          <View style={[styles.tick, { left: `${Math.min(99, reference)}%`, backgroundColor: c.text }]} />
        ) : null}
      </View>
      {reference !== null && reference !== undefined ? (
        <Text style={[styles.compareNote, { color: c.faint }]}>{referenceLabel[0].toUpperCase() + referenceLabel.slice(1)}: {pct(reference)}</Text>
      ) : null}
    </View>
  );
}

const VERDICTS: Record<Verdict, { text: string; tone: 'up' | 'neutral' | 'down' }> = {
  much_more: { text: 'Far more than usual', tone: 'up' },
  more: { text: 'More than usual', tone: 'up' },
  typical: { text: 'About usual', tone: 'neutral' },
  fewer: { text: 'Fewer than usual', tone: 'down' },
  much_fewer: { text: 'Far fewer than usual', tone: 'down' },
};

export function verdictText(verdict: Verdict | null | undefined): string | null {
  return verdict ? VERDICTS[verdict].text : null;
}

/** A small coloured label for a verdict. */
export function VerdictChip({ verdict }: { verdict: Verdict | null | undefined }) {
  const c = useColors();
  if (!verdict) return null;
  const { text, tone } = VERDICTS[verdict];
  const color = tone === 'up' ? c.primary : tone === 'down' ? c.warning : c.muted;
  const icon: IconName = tone === 'up' ? 'trending-up' : tone === 'down' ? 'trending-down' : 'remove';
  return (
    <View style={[styles.verdict, { backgroundColor: `${color}1F` }]}>
      <Ionicons name={icon} size={13} color={color} />
      <Text style={[styles.verdictText, { color }]}>{text}</Text>
    </View>
  );
}

/** A highlighted finding: an icon, one sentence in bold, an optional explanation. */
export function Callout({ icon, title, children, tone = 'info' }: { icon: IconName; title: string; children?: ReactNode; tone?: 'info' | 'good' | 'warn' }) {
  const c = useColors();
  const color = tone === 'good' ? c.primary : tone === 'warn' ? c.warning : c.info;
  return (
    <View style={[styles.callout, { backgroundColor: `${color}14`, borderColor: `${color}40` }]}>
      <Ionicons name={icon} size={22} color={color} />
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={[styles.calloutTitle, { color: c.text }]}>{title}</Text>
        {children ? <Text style={[styles.calloutText, { color: c.muted }]}>{children}</Text> : null}
      </View>
    </View>
  );
}

/** Fine print under an insight: where the number comes from. */
export function Footnote({ children }: { children: ReactNode }) {
  const c = useColors();
  return <Text style={[styles.footnote, { color: c.faint }]}>{children}</Text>;
}

const styles = StyleSheet.create({
  stars: { flexDirection: 'row', alignItems: 'center', gap: 1 },
  starsText: { fontWeight: '700', marginLeft: 4 },
  starsCount: { marginLeft: 3 },
  tiles: { flexDirection: 'row', gap: space.sm, paddingHorizontal: space.lg },
  tile: { flex: 1, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, paddingVertical: space.md, paddingHorizontal: space.sm, alignItems: 'center', minHeight: 76, justifyContent: 'center' },
  tileValue: { fontSize: 21, fontWeight: '800' },
  tileLabel: { fontSize: 12, fontWeight: '500', marginTop: 2, textAlign: 'center' },
  compare: { paddingHorizontal: space.lg, paddingVertical: space.sm },
  compareText: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
  compareLabel: { fontSize: 14.5, fontWeight: '500' },
  compareValue: { fontSize: 14.5, fontWeight: '700' },
  compareNote: { fontSize: 12, marginTop: 4 },
  track: { height: 10, borderRadius: 5, overflow: 'visible' },
  fill: { height: 10, borderRadius: 5 },
  tick: { position: 'absolute', top: -3, width: 2, height: 16, borderRadius: 1 },
  verdict: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: radius.pill, alignSelf: 'flex-start' },
  verdictText: { fontSize: 12, fontWeight: '700' },
  callout: { flexDirection: 'row', gap: space.md, marginHorizontal: space.lg, padding: space.md, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth, alignItems: 'flex-start' },
  calloutTitle: { fontSize: 15, fontWeight: '700', lineHeight: 20 },
  calloutText: { fontSize: 13.5, lineHeight: 19 },
  footnote: { fontSize: 12, lineHeight: 17, paddingHorizontal: space.lg, marginTop: space.sm },
});
