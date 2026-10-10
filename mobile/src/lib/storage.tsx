import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

/** Saved items and recent searches live on the phone only. */

export type SavedKind = 'place' | 'company' | 'city';
export type SavedItem = { kind: SavedKind; id: string; title: string; subtitle?: string; group?: string | null; savedAt: number };

const SAVED_KEY = 'karobar.saved.v1';
const RECENT_KEY = 'karobar.recent.v1';

type Store = {
  saved: SavedItem[];
  isSaved: (kind: SavedKind, id: string) => boolean;
  toggleSaved: (item: Omit<SavedItem, 'savedAt'>) => boolean;
  removeSaved: (kind: SavedKind, id: string) => void;
  recent: string[];
  addRecent: (query: string) => void;
  clearRecent: () => void;
};

const StoreContext = createContext<Store | null>(null);

async function load<T>(key: string, fallback: T): Promise<T> {
  try {
    const raw = await AsyncStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function persist(key: string, value: unknown) {
  AsyncStorage.setItem(key, JSON.stringify(value)).catch(() => {});
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [saved, setSaved] = useState<SavedItem[]>([]);
  const [recent, setRecent] = useState<string[]>([]);

  useEffect(() => {
    load<SavedItem[]>(SAVED_KEY, []).then(setSaved);
    load<string[]>(RECENT_KEY, []).then(setRecent);
  }, []);

  const isSaved = useCallback((kind: SavedKind, id: string) => saved.some((s) => s.kind === kind && s.id === id), [saved]);

  const toggleSaved = useCallback(
    (item: Omit<SavedItem, 'savedAt'>) => {
      const exists = saved.some((s) => s.kind === item.kind && s.id === item.id);
      const next = exists
        ? saved.filter((s) => !(s.kind === item.kind && s.id === item.id))
        : [{ ...item, savedAt: Date.now() }, ...saved];
      setSaved(next);
      persist(SAVED_KEY, next);
      return !exists;
    },
    [saved],
  );

  const removeSaved = useCallback((kind: SavedKind, id: string) => {
    setSaved((current) => {
      const next = current.filter((s) => !(s.kind === kind && s.id === id));
      persist(SAVED_KEY, next);
      return next;
    });
  }, []);

  const addRecent = useCallback((query: string) => {
    const text = query.trim();
    if (text.length < 2) return;
    setRecent((current) => {
      const next = [text, ...current.filter((q) => q.toLowerCase() !== text.toLowerCase())].slice(0, 8);
      persist(RECENT_KEY, next);
      return next;
    });
  }, []);

  const clearRecent = useCallback(() => {
    setRecent([]);
    persist(RECENT_KEY, []);
  }, []);

  const value = useMemo(
    () => ({ saved, isSaved, toggleSaved, removeSaved, recent, addRecent, clearRecent }),
    [saved, isSaved, toggleSaved, removeSaved, recent, addRecent, clearRecent],
  );
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const store = useContext(StoreContext);
  if (!store) throw new Error('useStore must be used inside StoreProvider');
  return store;
}
