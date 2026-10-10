/**
 * Address of the XentoLab API.
 *
 * In development the app talks to the API on your computer through `localhost`: `npm run android` runs
 * `adb reverse tcp:8000 tcp:8000`, which forwards the port for emulators and USB-connected phones alike
 * (the iOS simulator shares the Mac's network). For a release build, point this at the deployed API.
 */
export const API_URL = 'http://localhost:8000';
