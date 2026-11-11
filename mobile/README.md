# XentoLab (mobile)

App ID: Android `applicationId` `pk.xento.xentolab` (the Play Console listing), iOS bundle identifier
`pk.xento.karobar`. The Kotlin package/`namespace` stays `pk.xento.karobar`. Internal names (JS module
`Karobar`, Xcode project, storage keys) are unchanged and never shown to users.

React Native CLI app (no Expo) that lets anyone browse the Gold data, places, companies & brands, cities and
websites, and turns it into answers: is it open and any good, how crowded is this market, how does this city
compare, how strong is this brand. It never shows where the data came from. The API enforces this (`/api/app`,
see `backend/xento_api/routes.py` and `insights.py`).

| Tab | What it does |
|---|---|
| Home | search, "What's near me?", market insights, at-a-glance numbers, category tiles, popular cities, big brands, regions |
| Search | one box for places, categories, companies, cities and websites; recent searches |
| Near me | places around your location, by distance, with distance / category / "has phone" filters |
| Saved | bookmarked places, companies and cities (stored on the phone) |

Detail screens have one-tap actions: Call, Directions (opens the phone's maps app), Website, Share, and
long-press to copy a phone number or address. Light and dark mode follow the phone.

### Insights

| Screen | What it answers |
|---|---|
| Market (`/insights/categories/{key}`) | a category nationally or in one city: size, rating, how reachable (phone / website), leading brands and their share, top rated, crowded cities and cities with room to grow |
| City (`/cities/{id}`, `insights`) | what the city stands out for, common businesses it has fewer of than usual, online presence and ratings against the national figure, top rated |
| Company (`/organizations/{id}`, `insights`) | rank and market share in its category, main competitors, best- and lowest-rated branches, strongest cities, big cities where it is missing |
| Place (`/businesses/{id}`) | open now / closes at (from `opening_hours`, on the phone's clock), stars, "rated higher than X% of … in this city", competition within 1 km |
| Lists | "Top rated" sort (rating weighted by review count) and an "Open 24 hours" filter |

Cities are compared by each category's **share** of their listed businesses against the national share, not by raw
counts or per-person figures: coverage differs between cities and gazetteer populations disagree. "Expected" is
what the national mix would give a city of that size.

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
  lib/                    API client + types, theme, formatting, opening hours, phone actions, location, storage
```

Checks: `npm run typecheck`, `npm run lint`, `npx jest --no-watchman` (unit tests in `src/lib/__tests__`).
