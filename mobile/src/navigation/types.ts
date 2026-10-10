import { useNavigation, type NavigatorScreenParams } from '@react-navigation/native';
import type { NativeStackNavigationProp, NativeStackScreenProps } from '@react-navigation/native-stack';

export type PlacesFilters = {
  title?: string;
  q?: string;
  group?: string;
  category?: string;
  city?: string;
  area?: string;
  province?: string;
  organization?: string;
  has_phone?: boolean;
};

export type TabParamList = {
  Home: undefined;
  Search: { q?: string } | undefined;
  Nearby: undefined;
  Saved: undefined;
};

export type RootStackParamList = {
  Tabs: NavigatorScreenParams<TabParamList> | undefined;
  Place: { id: string };
  Company: { id: string };
  City: { id: string };
  Website: { domain: string };
  Places: PlacesFilters;
  Companies: { q?: string } | undefined;
  Cities: undefined;
  Websites: { q?: string } | undefined;
};

export type ScreenProps<T extends keyof RootStackParamList> = NativeStackScreenProps<RootStackParamList, T>;

/** Navigation from anywhere (tabs included); stack actions bubble up to the root stack. */
export function useNav() {
  return useNavigation<NativeStackNavigationProp<RootStackParamList>>();
}
