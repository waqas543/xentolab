import { Ionicons } from '../lib/icons';
import type { ComponentRef, Ref } from 'react';
import { Pressable, StyleSheet, TextInput, View, type TextInputProps } from 'react-native';

import { radius, space, useColors } from '../lib/theme';

export type SearchInput = ComponentRef<typeof TextInput>;

type Props = Omit<TextInputProps, 'onChangeText' | 'ref'> & { value: string; onChangeText: (text: string) => void; ref?: Ref<SearchInput> };

export function SearchBox({ value, onChangeText, style, ref, ...rest }: Props) {
  const c = useColors();
  return (
    <View style={[styles.box, { backgroundColor: c.chip }, style]}>
      <Ionicons name="search" size={20} color={c.muted} />
      <TextInput
        ref={ref}
        value={value}
        onChangeText={onChangeText}
        placeholderTextColor={c.faint}
        style={[styles.input, { color: c.text }]}
        returnKeyType="search"
        autoCorrect={false}
        autoCapitalize="none"
        clearButtonMode="never"
        accessibilityLabel="Search"
        {...rest}
      />
      {value ? (
        <Pressable onPress={() => onChangeText('')} hitSlop={12} accessibilityLabel="Clear search">
          <Ionicons name="close-circle" size={20} color={c.faint} />
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { flexDirection: 'row', alignItems: 'center', gap: space.sm, borderRadius: radius.md, paddingHorizontal: space.md, minHeight: 48 },
  input: { flex: 1, fontSize: 16, paddingVertical: space.md },
});
