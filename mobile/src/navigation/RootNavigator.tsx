import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import { Ionicons, type IconName } from '../lib/icons';
import { useColors } from '../lib/theme';
import { CitiesScreen, CompaniesScreen, PlacesScreen, WebsitesScreen } from '../screens/BrowseScreens';
import { CityScreen } from '../screens/CityScreen';
import { CompanyScreen } from '../screens/CompanyScreen';
import { HomeScreen } from '../screens/HomeScreen';
import { NearbyScreen } from '../screens/NearbyScreen';
import { PlaceScreen } from '../screens/PlaceScreen';
import { SavedScreen } from '../screens/SavedScreen';
import { SearchScreen } from '../screens/SearchScreen';
import { WebsiteScreen } from '../screens/WebsiteScreen';
import type { RootStackParamList, TabParamList } from './types';

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<TabParamList>();

const TAB_ICONS: Record<keyof TabParamList, [IconName, IconName]> = {
  Home: ['home', 'home-outline'],
  Search: ['search', 'search-outline'],
  Nearby: ['navigate', 'navigate-outline'],
  Saved: ['bookmark', 'bookmark-outline'],
};

function Tabs() {
  const c = useColors();
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        tabBarActiveTintColor: c.primary,
        tabBarInactiveTintColor: c.faint,
        tabBarStyle: { backgroundColor: c.card, borderTopColor: c.border },
        tabBarLabelStyle: { fontSize: 12, fontWeight: '600' },
        tabBarIcon: ({ color, focused }) => <Ionicons name={TAB_ICONS[route.name][focused ? 0 : 1]} size={24} color={color} />,
        headerStyle: { backgroundColor: c.background },
        headerShadowVisible: false,
        headerTitleStyle: { color: c.text, fontWeight: '700' },
      })}>
      <Tab.Screen name="Home" component={HomeScreen} options={{ headerShown: false }} />
      <Tab.Screen name="Search" component={SearchScreen} options={{ headerShown: false }} />
      <Tab.Screen name="Nearby" component={NearbyScreen} options={{ title: 'Near me' }} />
      <Tab.Screen name="Saved" component={SavedScreen} />
    </Tab.Navigator>
  );
}

export function RootNavigator() {
  const c = useColors();
  return (
    <Stack.Navigator
      screenOptions={{
        headerBackButtonDisplayMode: 'minimal',
        headerShadowVisible: false,
        headerTintColor: c.primary,
        headerStyle: { backgroundColor: c.background },
        headerTitleStyle: { color: c.text },
        title: '',
      }}>
      <Stack.Screen name="Tabs" component={Tabs} options={{ headerShown: false, title: 'Home' }} />
      <Stack.Screen name="Place" component={PlaceScreen} />
      <Stack.Screen name="Company" component={CompanyScreen} />
      <Stack.Screen name="City" component={CityScreen} />
      <Stack.Screen name="Website" component={WebsiteScreen} options={{ title: 'Website' }} />
      <Stack.Screen name="Places" component={PlacesScreen} />
      <Stack.Screen name="Companies" component={CompaniesScreen} options={{ title: 'Companies & brands' }} />
      <Stack.Screen name="Cities" component={CitiesScreen} options={{ title: 'Cities & towns' }} />
      <Stack.Screen name="Websites" component={WebsitesScreen} options={{ title: 'Websites' }} />
    </Stack.Navigator>
  );
}
