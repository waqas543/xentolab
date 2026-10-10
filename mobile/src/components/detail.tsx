import { Ionicons, type IconName } from '../lib/icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { radius, space, useColors } from '../lib/theme';


/** Big, labelled quick-action buttons (Call, Directions, …). Disabled ones are hidden, not greyed out. */
export function Actions({ items }: { items: { icon: IconName; label: string; onPress: () => void; primary?: boolean }[] }) {
  const c = useColors();
  return (
    <View style={styles.actions}>
      {items.map((a) => (
        <Pressable
          key={a.label}
          onPress={a.onPress}
          accessibilityRole="button"
          accessibilityLabel={a.label}
          style={({ pressed }) => [styles.action, { backgroundColor: a.primary ? c.primary : c.primarySoft, opacity: pressed ? 0.75 : 1 }]}>
          <Ionicons name={a.icon} size={22} color={a.primary ? c.onPrimary : c.primary} />
          <Text style={[styles.actionLabel, { color: a.primary ? c.onPrimary : c.primary }]} numberOfLines={1}>{a.label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

/** One fact: icon, small label, value. Tappable when it does something (call, open, copy). */
export function Fact({ icon, label, value, onPress, onLongPress, last }: {
  icon: IconName; label: string; value: string | null | undefined; onPress?: () => void; onLongPress?: () => void; last?: boolean;
}) {
  const c = useColors();
  if (!value) return null;
  return (
    <Pressable
      onPress={onPress}
      onLongPress={onLongPress}
      disabled={!onPress && !onLongPress}
      style={({ pressed }) => [styles.fact, { backgroundColor: pressed ? c.chip : c.card, borderBottomColor: c.border, borderBottomWidth: last ? 0 : StyleSheet.hairlineWidth }]}>
      <Ionicons name={icon} size={20} color={c.muted} style={{ marginTop: 2 }} />
      <View style={{ flex: 1 }}>
        <Text style={[styles.factLabel, { color: c.faint }]}>{label}</Text>
        <Text style={[styles.factValue, { color: onPress ? c.primary : c.text }]} selectable={!onPress}>{value}</Text>
      </View>
    </Pressable>
  );
}

export function Pill({ text, icon }: { text: string; icon?: IconName }) {
  const c = useColors();
  return (
    <View style={[styles.pill, { backgroundColor: c.chip }]}>
      {icon ? <Ionicons name={icon} size={13} color={c.muted} /> : null}
      <Text style={[styles.pillText, { color: c.muted }]}>{text}</Text>
    </View>
  );
}

/** A labelled horizontal bar, for "how many of each" breakdowns. */
export function Bar({ label, value, max, onPress, tint }: { label: string; value: number; max: number; onPress?: () => void; tint?: string }) {
  const c = useColors();
  return (
    <Pressable onPress={onPress} disabled={!onPress} style={({ pressed }) => [styles.bar, { opacity: pressed ? 0.6 : 1 }]} accessibilityRole={onPress ? 'button' : undefined}>
      <View style={styles.barText}>
        <Text style={[styles.barLabel, { color: c.text }]} numberOfLines={1}>{label}</Text>
        <Text style={[styles.barValue, { color: c.muted }]}>{value.toLocaleString('en-US')}</Text>
      </View>
      <View style={[styles.track, { backgroundColor: c.chip }]}>
        <View style={[styles.fill, { width: `${Math.max(2, (100 * value) / Math.max(max, 1))}%`, backgroundColor: tint ?? c.primary }]} />
      </View>
    </Pressable>
  );
}

export const detailStyles = StyleSheet.create({
  hero: { padding: space.lg, gap: space.sm },
  heroTitle: { fontSize: 26, fontWeight: '800', letterSpacing: -0.4, lineHeight: 31 },
  heroSub: { fontSize: 16 },
  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.xs },
  card: { marginHorizontal: space.lg },
  paragraph: { fontSize: 15.5, lineHeight: 23, paddingHorizontal: space.lg },
});

const styles = StyleSheet.create({
  actions: { flexDirection: 'row', gap: space.sm, paddingHorizontal: space.lg, marginTop: space.xs },
  action: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 4, paddingVertical: space.md, borderRadius: radius.md, minHeight: 64 },
  actionLabel: { fontSize: 13, fontWeight: '700' },
  fact: { flexDirection: 'row', gap: space.md, paddingHorizontal: space.lg, paddingVertical: space.md },
  factLabel: { fontSize: 12.5, fontWeight: '600', marginBottom: 2 },
  factValue: { fontSize: 16, lineHeight: 22 },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill },
  pillText: { fontSize: 13, fontWeight: '600' },
  bar: { paddingHorizontal: space.lg, paddingVertical: 7 },
  barText: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 5 },
  barLabel: { fontSize: 14.5, fontWeight: '500', flex: 1 },
  barValue: { fontSize: 13.5, fontWeight: '600' },
  track: { height: 8, borderRadius: 4, overflow: 'hidden' },
  fill: { height: 8, borderRadius: 4 },
});
