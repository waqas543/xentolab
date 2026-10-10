import { DarkTheme, DefaultTheme, NavigationContainer } from '@react-navigation/native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StatusBar, useColorScheme } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { StoreProvider } from './lib/storage';
import { useColors } from './lib/theme';
import { RootNavigator } from './navigation/RootNavigator';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5 * 60_000, gcTime: 30 * 60_000, retry: 1 } },
});

export default function App() {
  const scheme = useColorScheme();
  const c = useColors();
  const base = scheme === 'dark' ? DarkTheme : DefaultTheme;
  const theme = { ...base, colors: { ...base.colors, primary: c.primary, background: c.background, card: c.card, text: c.text, border: c.border } };

  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <StoreProvider>
          <NavigationContainer theme={theme}>
            <StatusBar barStyle={scheme === 'dark' ? 'light-content' : 'dark-content'} />
            <RootNavigator />
          </NavigationContainer>
        </StoreProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}
