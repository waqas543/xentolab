import Clipboard from '@react-native-clipboard/clipboard';
import { Alert, Linking, Platform, Share, ToastAndroid } from 'react-native';

import type { Place } from './api';

async function open(url: string) {
  try {
    await Linking.openURL(url);
  } catch {
    Alert.alert('Could not open', 'No app on this phone can open this link.');
  }
}

export const call = (phone: string) => open(`tel:${phone.replace(/[^\d+]/g, '')}`);
export const email = (address: string) => open(`mailto:${address}`);
export const browse = (url: string) => open(url);

export function directions(place: Pick<Place, 'name' | 'lat' | 'lng' | 'address' | 'city'>) {
  if (place.lat !== null && place.lng !== null) {
    const label = encodeURIComponent(place.name);
    return open(
      Platform.OS === 'ios'
        ? `http://maps.apple.com/?ll=${place.lat},${place.lng}&q=${label}`
        : `geo:${place.lat},${place.lng}?q=${place.lat},${place.lng}(${label})`,
    );
  }
  const query = encodeURIComponent([place.name, place.address, place.city].filter(Boolean).join(', '));
  return open(`https://www.google.com/maps/search/?api=1&query=${query}`);
}

export function sharePlace(place: Place) {
  const map = place.lat !== null ? `https://www.google.com/maps/search/?api=1&query=${place.lat},${place.lng}` : null;
  const lines = [place.name, place.category, place.address || [place.area, place.city].filter(Boolean).join(', '),
    place.phone && `Phone: ${place.phone}`, place.website, map];
  return Share.share({ message: lines.filter(Boolean).join('\n') });
}

export function copy(text: string, what = 'Copied') {
  if (!text) return;
  Clipboard.setString(text);
  if (Platform.OS === 'android') ToastAndroid.show(what, ToastAndroid.SHORT);
  else Alert.alert(what, text);
}
