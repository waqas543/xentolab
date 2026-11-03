/**
 * Address of the XentoLab API (production server, deploy/compose.yaml).
 *
 * To work against the API on your computer instead, use 'http://localhost:8000': `npm run android` runs
 * `adb reverse tcp:8000 tcp:8000`, which forwards that port to emulators and USB-connected phones.
 *
 * Plain http works in debug builds only; release builds need an https address (e.g. https://api.xento.pk).
 */
// export const API_URL = 'http://localhost:8000';
export const API_URL = 'https://xentolab.xento.pk';
