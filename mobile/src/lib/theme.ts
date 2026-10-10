import { useColorScheme } from 'react-native';

import type { IconName } from './icons';

const light = {
  background: '#F6F7F9',
  card: '#FFFFFF',
  text: '#14171F',
  muted: '#5F6675',
  faint: '#8A91A0',
  border: '#E3E6EC',
  primary: '#0B7A5C',
  primarySoft: '#E3F3EE',
  onPrimary: '#FFFFFF',
  danger: '#C2410C',
  chip: '#EEF0F4',
};

const dark: typeof light = {
  background: '#0E1116',
  card: '#181C23',
  text: '#F2F4F7',
  muted: '#A6ADBB',
  faint: '#7A8292',
  border: '#272C35',
  primary: '#34C79A',
  primarySoft: '#13362C',
  onPrimary: '#06241B',
  danger: '#FB923C',
  chip: '#222731',
};

export type Colors = typeof light;

export function useColors(): Colors {
  return useColorScheme() === 'dark' ? dark : light;
}


/** Icon and tint for each business group, so categories are recognisable at a glance. */
export const GROUPS: Record<string, { icon: IconName; tint: string }> = {
  'Food & Drink': { icon: 'restaurant', tint: '#E8590C' },
  Retail: { icon: 'bag-handle', tint: '#D6336C' },
  Health: { icon: 'medkit', tint: '#E03131' },
  Education: { icon: 'school', tint: '#1971C2' },
  Finance: { icon: 'cash', tint: '#2B8A3E' },
  Automotive: { icon: 'car-sport', tint: '#5F3DC4' },
  Hospitality: { icon: 'bed', tint: '#0C8599' },
  'Professional Services': { icon: 'briefcase', tint: '#495057' },
  'Industry & Craft': { icon: 'hammer', tint: '#A0522D' },
  'Public & Non-profit': { icon: 'people', tint: '#1864AB' },
  'Other Services': { icon: 'construct', tint: '#868E96' },
};

export function groupStyle(group: string | null | undefined) {
  return (group && GROUPS[group]) || { icon: 'storefront' as IconName, tint: '#868E96' };
}

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const radius = { sm: 8, md: 12, lg: 16, pill: 999 };
