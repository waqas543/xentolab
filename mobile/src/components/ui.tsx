import { Ionicons, type IconName } from '../lib/icons';
import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';

import { ApiError } from '../lib/api';
import { groupStyle, radius, space, useColors } from '../lib/theme';


export function Title({ children, style }: { children: ReactNode; style?: object }) {
  const c = useColors();
  return <Text style={[styles.title, { color: c.text }, style]} accessibilityRole="header">{children}</Text>;
}

export function Body({ children, muted, numberOfLines, style }: { children: ReactNode; muted?: boolean; numberOfLines?: number; style?: object }) {
  const c = useColors();
  return <Text numberOfLines={numberOfLines} style={[styles.body, { color: muted ? c.muted : c.text }, style]}>{children}</Text>;
}

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  const c = useColors();
  return <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }, style]}>{children}</View>;
}

export function Section({ title, action, onAction, children }: { title: string; action?: string; onAction?: () => void; children: ReactNode }) {
  const c = useColors();
  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <Text style={[styles.sectionTitle, { color: c.text }]} accessibilityRole="header">{title}</Text>
        {action && onAction ? (
          <Pressable onPress={onAction} hitSlop={12} accessibilityRole="button">
            <Text style={[styles.link, { color: c.primary }]}>{action}</Text>
          </Pressable>
        ) : null}
      </View>
      {children}
    </View>
  );
}

export function GroupIcon({ group, size = 40 }: { group: string | null | undefined; size?: number }) {
  const { icon, tint } = groupStyle(group);
  return (
    <View style={[styles.groupIcon, { width: size, height: size, borderRadius: size / 3.2, backgroundColor: `${tint}1F` }]}>
      <Ionicons name={icon} size={size * 0.5} color={tint} />
    </View>
  );
}

export function IconCircle({ name, size = 40, color }: { name: IconName; size?: number; color?: string }) {
  const c = useColors();
  return (
    <View style={[styles.groupIcon, { width: size, height: size, borderRadius: size / 3.2, backgroundColor: c.primarySoft }]}>
      <Ionicons name={name} size={size * 0.5} color={color ?? c.primary} />
    </View>
  );
}

export function Chip({ label, selected, onPress, icon }: { label: string; selected?: boolean; onPress: () => void; icon?: IconName }) {
  const c = useColors();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      style={[styles.chip, { backgroundColor: selected ? c.primary : c.chip }]}>
      {icon ? <Ionicons name={icon} size={15} color={selected ? c.onPrimary : c.muted} /> : null}
      <Text style={[styles.chipText, { color: selected ? c.onPrimary : c.text }]}>{label}</Text>
    </Pressable>
  );
}

export function ChipRow({ children }: { children: ReactNode }) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow} keyboardShouldPersistTaps="handled">
      {children}
    </ScrollView>
  );
}

/** A tappable list row: icon, title, up to two lines of detail, and an optional trailing note. */
export function Row({ icon, title, subtitle, detail, trailing, onPress, last }: {
  icon: ReactNode; title: string; subtitle?: string | null; detail?: string | null; trailing?: string | null; onPress?: () => void; last?: boolean;
}) {
  const c = useColors();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      style={({ pressed }) => [styles.row, { backgroundColor: pressed ? c.chip : c.card, borderBottomColor: c.border, borderBottomWidth: last ? 0 : StyleSheet.hairlineWidth }]}>
      {icon}
      <View style={styles.rowText}>
        <Text style={[styles.rowTitle, { color: c.text }]} numberOfLines={1}>{title}</Text>
        {subtitle ? <Text style={[styles.rowSub, { color: c.muted }]} numberOfLines={1}>{subtitle}</Text> : null}
        {detail ? <Text style={[styles.rowSub, { color: c.faint }]} numberOfLines={1}>{detail}</Text> : null}
      </View>
      {trailing ? <Text style={[styles.trailing, { color: c.muted }]}>{trailing}</Text> : null}
      {onPress ? <Ionicons name="chevron-forward" size={18} color={c.faint} /> : null}
    </Pressable>
  );
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  const c = useColors();
  return (
    <View style={styles.center}>
      <ActivityIndicator color={c.primary} size="large" />
      <Text style={[styles.body, { color: c.muted, marginTop: space.md }]}>{label}</Text>
    </View>
  );
}

export function Message({ icon, title, text, action, onAction }: { icon: IconName; title: string; text?: string; action?: string; onAction?: () => void }) {
  const c = useColors();
  return (
    <View style={styles.center}>
      <Ionicons name={icon} size={44} color={c.faint} />
      <Text style={[styles.messageTitle, { color: c.text }]}>{title}</Text>
      {text ? <Text style={[styles.body, styles.centerText, { color: c.muted }]}>{text}</Text> : null}
      {action && onAction ? <Button label={action} onPress={onAction} style={{ marginTop: space.lg }} /> : null}
    </View>
  );
}

export function ErrorView({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : 'Something went wrong. Please try again.';
  const notFound = error instanceof ApiError && error.status === 404;
  return (
    <Message
      icon={notFound ? 'search' : 'cloud-offline-outline'}
      title={notFound ? 'Not found' : "Couldn't load"}
      text={message}
      action={onRetry && !notFound ? 'Try again' : undefined}
      onAction={onRetry}
    />
  );
}

export function Button({ label, onPress, icon, style, kind = 'primary' }: {
  label: string; onPress: () => void; icon?: IconName; style?: object; kind?: 'primary' | 'secondary';
}) {
  const c = useColors();
  const primary = kind === 'primary';
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      style={({ pressed }) => [styles.button, { backgroundColor: primary ? c.primary : c.primarySoft, opacity: pressed ? 0.8 : 1 }, style]}>
      {icon ? <Ionicons name={icon} size={18} color={primary ? c.onPrimary : c.primary} /> : null}
      <Text style={[styles.buttonText, { color: primary ? c.onPrimary : c.primary }]}>{label}</Text>
    </Pressable>
  );
}

export function ListFooter({ loading, total, shown }: { loading: boolean; total?: number; shown: number }) {
  const c = useColors();
  if (loading) return <ActivityIndicator color={c.primary} style={{ margin: space.xl }} />;
  if (total !== undefined && shown > 0 && shown >= total) {
    return <Text style={[styles.rowSub, styles.centerText, { color: c.faint, margin: space.xl }]}>That's everything</Text>;
  }
  return <View style={{ height: space.xl }} />;
}

export const styles = StyleSheet.create({
  title: { fontSize: 26, fontWeight: '700', letterSpacing: -0.3 },
  body: { fontSize: 15, lineHeight: 21 },
  card: { borderRadius: radius.lg, borderWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  section: { marginTop: space.xl },
  sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: space.sm, paddingHorizontal: space.lg },
  sectionTitle: { fontSize: 18, fontWeight: '700' },
  link: { fontSize: 15, fontWeight: '600' },
  groupIcon: { alignItems: 'center', justifyContent: 'center' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 14, paddingVertical: 9, borderRadius: radius.pill },
  chipText: { fontSize: 14, fontWeight: '600' },
  chipRow: { gap: space.sm, paddingHorizontal: space.lg, paddingVertical: space.xs },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingVertical: space.md, minHeight: 64 },
  rowText: { flex: 1, gap: 2 },
  rowTitle: { fontSize: 16, fontWeight: '600' },
  rowSub: { fontSize: 13.5 },
  trailing: { fontSize: 13, fontWeight: '600' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: space.xl, minHeight: 280 },
  centerText: { textAlign: 'center' },
  messageTitle: { fontSize: 18, fontWeight: '700', marginTop: space.md, marginBottom: space.xs },
  button: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space.sm, paddingHorizontal: space.xl, paddingVertical: 13, borderRadius: radius.md, minHeight: 48 },
  buttonText: { fontSize: 16, fontWeight: '700' },
});
