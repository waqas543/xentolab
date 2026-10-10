import Geolocation from '@react-native-community/geolocation';
import { PermissionsAndroid, Platform } from 'react-native';

export type Coords = { lat: number; lng: number };
export type Located = { status: 'ok'; coords: Coords } | { status: 'denied'; canAsk: boolean } | { status: 'failed' };

Geolocation.setRNConfiguration({ skipPermissionRequests: true, authorizationLevel: 'whenInUse', locationProvider: 'auto' });

async function askPermission(): Promise<'granted' | 'denied' | 'blocked'> {
  if (Platform.OS === 'android') {
    const { ACCESS_FINE_LOCATION: FINE, ACCESS_COARSE_LOCATION: COARSE } = PermissionsAndroid.PERMISSIONS;
    // Android 12+ lets people pick "approximate"; that grants only COARSE, which is plenty for "near me".
    const result = await PermissionsAndroid.requestMultiple([FINE, COARSE]);
    if (result[FINE] === 'granted' || result[COARSE] === 'granted') return 'granted';
    return result[COARSE] === 'never_ask_again' ? 'blocked' : 'denied';
  }
  return new Promise((resolve) => {
    Geolocation.requestAuthorization(
      () => resolve('granted'),
      (error) => resolve(error.code === error.PERMISSION_DENIED ? 'blocked' : 'denied'),
    );
  });
}

function position(): Promise<Coords> {
  return new Promise((resolve, reject) => {
    Geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude }),
      reject,
      { enableHighAccuracy: false, timeout: 15_000, maximumAge: 5 * 60_000 },
    );
  });
}

export async function locate(): Promise<Located> {
  const permission = await askPermission();
  if (permission !== 'granted') return { status: 'denied', canAsk: permission === 'denied' };
  try {
    return { status: 'ok', coords: await position() };
  } catch {
    return { status: 'failed' };
  }
}
