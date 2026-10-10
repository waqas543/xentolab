import { Alert, FlatList, Pressable, StyleSheet, View } from 'react-native';

import { GroupIcon, IconCircle, Message, Row } from '../components/ui';
import { Ionicons } from '../lib/icons';
import { useStore, type SavedItem } from '../lib/storage';
import { space, useColors } from '../lib/theme';
import { useNav } from '../navigation/types';

export function SavedScreen() {
  const c = useColors();
  const nav = useNav();
  const { saved, removeSaved } = useStore();

  const open = (item: SavedItem) => {
    if (item.kind === 'place') nav.push('Place', { id: item.id });
    else if (item.kind === 'company') nav.push('Company', { id: item.id });
    else nav.push('City', { id: item.id });
  };

  if (saved.length === 0) {
    return (
      <View style={{ flex: 1, backgroundColor: c.background }}>
        <Message
          icon="bookmark-outline"
          title="Nothing saved yet"
          text="Tap the bookmark on any place, company or city to keep it here for quick access."
          action="Find something"
          onAction={() => nav.navigate('Tabs', { screen: 'Search' })}
        />
      </View>
    );
  }

  return (
    <FlatList
      data={saved}
      keyExtractor={(item) => `${item.kind}:${item.id}`}
      style={{ backgroundColor: c.card }}
      renderItem={({ item, index }) => (
        <View style={styles.wrap}>
          <View style={styles.flex}>
            <Row
              icon={item.kind === 'city' ? <IconCircle name="location" /> : <GroupIcon group={item.group} />}
              title={item.title}
              subtitle={item.subtitle}
              detail={item.kind === 'company' ? 'Company' : item.kind === 'city' ? 'City' : null}
              onPress={() => open(item)}
              last={index === saved.length - 1}
            />
          </View>
          <Pressable
            hitSlop={8}
            accessibilityLabel={`Remove ${item.title}`}
            style={[styles.remove, { backgroundColor: c.card }]}
            onPress={() =>
              Alert.alert('Remove from saved?', item.title, [
                { text: 'Cancel', style: 'cancel' },
                { text: 'Remove', style: 'destructive', onPress: () => removeSaved(item.kind, item.id) },
              ])
            }>
            <Ionicons name="bookmark" size={22} color={c.primary} />
          </Pressable>
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  wrap: { flexDirection: 'row', alignItems: 'center' },
  flex: { flex: 1 },
  remove: { paddingHorizontal: space.lg, alignSelf: 'stretch', justifyContent: 'center' },
});
