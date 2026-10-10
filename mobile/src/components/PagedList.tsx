import { useInfiniteQuery } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';

import { get, type Page } from '../lib/api';
import { count } from '../lib/format';
import { space, useColors } from '../lib/theme';
import { ErrorView, ListFooter, Loading, Message } from './ui';

type Params = Record<string, string | number | boolean | undefined>;

/** An endless list over any paged /api/app endpoint, with pull-to-refresh and a result count. */
export function PagedList<T>({ path, params, renderItem, keyOf, header, noun = 'results', emptyTitle, emptyText, emptyAction }: {
  path: string;
  params: Params;
  renderItem: (item: T, last: boolean) => ReactElement;
  keyOf: (item: T) => string;
  header?: ReactElement;
  noun?: string;
  emptyTitle?: string;
  emptyText?: string;
  emptyAction?: { label: string; onPress: () => void };
}) {
  const c = useColors();
  const query = useInfiniteQuery({
    queryKey: [path, params],
    queryFn: ({ pageParam }) => get<Page<T>>(path, { ...params, offset: pageParam, limit: 25 }),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
  });
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  const total = query.data?.pages[0]?.total;

  let empty: ReactElement | null = null;
  if (query.isPending) empty = <Loading />;
  else if (query.isError) empty = <ErrorView error={query.error} onRetry={() => query.refetch()} />;
  else empty = <Message icon="search" title={emptyTitle ?? 'Nothing found'} text={emptyText ?? 'Try removing a filter or searching for something else.'} action={emptyAction?.label} onAction={emptyAction?.onPress} />;

  return (
    <FlatList
      data={items}
      keyExtractor={keyOf}
      renderItem={({ item, index }) => renderItem(item, index === items.length - 1)}
      ListHeaderComponent={
        <View>
          {header}
          {total !== undefined && total > 0 ? (
            <Text style={[styles.count, { color: c.muted }]}>{count(total)} {noun}</Text>
          ) : null}
        </View>
      }
      ListEmptyComponent={empty}
      ListFooterComponent={<ListFooter loading={query.isFetchingNextPage} total={total} shown={items.length} />}
      onEndReached={() => query.hasNextPage && !query.isFetchingNextPage && query.fetchNextPage()}
      onEndReachedThreshold={0.6}
      refreshControl={<RefreshControl refreshing={query.isRefetching && !query.isFetchingNextPage} onRefresh={() => { query.refetch(); }} tintColor={c.primary} colors={[c.primary]} />}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="on-drag"
      style={{ backgroundColor: c.card }}
    />
  );
}

const styles = StyleSheet.create({
  count: { fontSize: 13, fontWeight: '600', paddingHorizontal: space.lg, paddingTop: space.md, paddingBottom: space.xs, textTransform: 'uppercase', letterSpacing: 0.5 },
});
