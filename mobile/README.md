# XentoLab (mobile)

App ID `pk.xento.karobar` (Android `applicationId`, iOS bundle identifier). Internal names (JS module
`Karobar`, Xcode project, storage keys) are unchanged and never shown to users.

React Native CLI app (no Expo) that lets anyone browse the Gold data: places, companies & brands, cities and
websites in Pakistan. It shows the data only; it never shows where the data came from. The API enforces this
(`/api/app`, see `backend/data_platform/api/routes_app.py`).

| Tab | What it does |
|---|---|
| Home | search, "What's near me?", 11 category tiles, popular cities, big brands, provinces |
| Search | one box for places, categories, companies, cities and websites; recent searches |
| Near me | places around your location, by distance, with distance / category / "has phone" filters |
| Saved | bookmarked places, companies and cities (stored on the phone) |

Detail screens have one-tap actions: Call, Directions (opens the phone's maps app), Website, Share, and
long-press to copy a phone number or address. Light and dark mode follow the phone.

## Run (Android)

```bash
make up                                  # API on :8000 (repo root)
cd mobile && nvm use && npm install
npm start                                # Metro, in its own terminal
npm run android                          # forwards :8000 to the device, builds, installs (needs an emulator or USB phone)
```

`npm run android` runs `adb reverse tcp:8000 tcp:8000`, so the app reaches the API at `localhost:8000` from an
emulator or a USB-connected phone. If you restart the emulator without rebuilding, run that `adb reverse` again.
For a release build, set `API_URL` in `src/config.ts` to the deployed API.

Requirements: JDK 17, Android SDK 37, NDK 27.1. On Apple Silicon use an arm64 emulator image.

## iOS

Needs Xcode (not installed on this machine yet): `bundle install && cd ios && bundle exec pod install && cd .. && npm run ios`.
Ionicons and the location permission text are already in `ios/Karobar/Info.plist`.

## Code

```
src/
  App.tsx                 providers: React Query, saved items, navigation theme
  config.ts               API address
  navigation/             stack + bottom tabs, typed routes
  screens/                Home, Search, Nearby, Saved, Place, Company, City, Website, browse lists
  components/             rows, chips, cards, paged list, detail building blocks
  lib/                    API client + types, theme, formatting, phone actions, location, storage
```

Checks: `npm run typecheck`, `npm run lint`.
