import { Ionicons } from '../lib/icons';
import { Pressable } from 'react-native';

import { useStore, type SavedItem } from '../lib/storage';
import { useColors } from '../lib/theme';

/** Header bookmark toggle for detail screens. */
export function SaveButton({ item }: { item: Omit<SavedItem, 'savedAt'> | null }) {
  const c = useColors();
  const { isSaved, toggleSaved } = useStore();
  if (!item) return null;
  const saved = isSaved(item.kind, item.id);
  return (
    <Pressable
      hitSlop={12}
      accessibilityRole="button"
      accessibilityLabel={saved ? 'Remove from saved' : 'Save'}
      onPress={() => toggleSaved(item)}>
      <Ionicons name={saved ? 'bookmark' : 'bookmark-outline'} size={24} color={c.primary} />
    </Pressable>
  );
}
