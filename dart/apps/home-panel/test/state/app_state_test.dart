import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_panel/app.dart';
import 'package:home_panel/state/events/app_event.dart';
import 'package:home_panel/state/handler/event_dispatcher.dart';
import 'package:home_panel/state/app_state.dart';
import 'package:home_panel/state/types/display_state.dart';
import 'package:home_panel/ui/theme.dart';
import 'package:home_panel/widgets/theme_mode_tile.dart';

void main() {
  group('store', () {
    test('starts active and dark; setters only change their field', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final store = container.read(appStoreProvider.notifier);

      expect(container.read(appStoreProvider), AppState.initial);
      expect(AppState.initial.display.mode, DisplayMode.active);
      expect(AppState.initial.themeMode, ThemeMode.dark);

      store.setThemeMode(ThemeMode.light);
      store.setDisplay(const DisplayState(mode: DisplayMode.sleep));
      expect(
        container.read(appStoreProvider),
        const AppState(
          display: DisplayState(mode: DisplayMode.sleep),
          themeMode: ThemeMode.light,
        ),
      );
    });

    test('a slice subscriber only hears about its slice', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      var themeUpdates = 0;
      container.listen(
        appStoreProvider.select((s) => s.themeMode),
        (_, _) => themeUpdates++,
      );
      final store = container.read(appStoreProvider.notifier);
      store.setDisplay(const DisplayState(mode: DisplayMode.sleep));
      expect(themeUpdates, 0);
      store.setThemeMode(ThemeMode.light);
      expect(themeUpdates, 1);
    });
  });

  group('dispatcher', () {
    testWidgets('events update the store', (tester) async {
      final container = ProviderContainer();
      final dispatcher = container.read(eventDispatcherProvider);
      AppState state() => container.read(appStoreProvider);

      dispatcher.dispatch(const ThemeModeClick());
      expect(state().themeMode, ThemeMode.light);
      dispatcher.dispatch(const ThemeModeClick());
      expect(state().themeMode, ThemeMode.dark);

      dispatcher.dispatch(const SleepClick());
      expect(state().display.mode, DisplayMode.sleep);

      dispatcher.dispatch(const UserUncategorizedClick());
      expect(state().display.mode, DisplayMode.active);

      // Cancel the dispatcher's timers before flutter_test checks for them.
      container.dispose();
    });

    testWidgets('every user event restarts the idle timer', (tester) async {
      final container = ProviderContainer();
      final dispatcher = container.read(eventDispatcherProvider);
      DisplayMode mode() => container.read(appStoreProvider).display.mode;

      // A settings event (not a touch) 90 s in pushes the sleep back: still
      // awake 3 minutes after start, asleep 2 minutes after the event.
      await tester.pump(const Duration(seconds: 90));
      dispatcher.dispatch(const ThemeModeClick());
      await tester.pump(const Duration(seconds: 90));
      expect(mode(), DisplayMode.active);
      await tester.pump(const Duration(seconds: 30));
      expect(mode(), DisplayMode.sleep);

      container.dispose();
    });

    testWidgets('the idle timer sleeps the panel; touches restart it', (
      tester,
    ) async {
      final container = ProviderContainer();
      final dispatcher = container.read(eventDispatcherProvider);
      DisplayMode mode() => container.read(appStoreProvider).display.mode;

      await tester.pump(const Duration(minutes: 1));
      dispatcher.dispatch(const UserUncategorizedClick());
      await tester.pump(const Duration(seconds: 90));
      expect(mode(), DisplayMode.active);
      await tester.pump(const Duration(seconds: 30));
      expect(mode(), DisplayMode.sleep);

      container.dispose();
    });
  });

  testWidgets('the theme tile switches the dashboard to light and back', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const ProviderScope(child: HomePanelApp()));

    PanelPalette palette() =>
        tester.element(find.byType(ThemeModeTile)).palette;

    expect(palette(), PanelPalette.dark);
    expect(find.bySemanticsLabel('Dark mode'), findsOneWidget);

    await tester.tap(find.byType(ThemeModeTile));
    await tester.pumpAndSettle();
    expect(palette(), PanelPalette.light);
    expect(find.bySemanticsLabel('Light mode'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.byType(ThemeModeTile));
    await tester.pumpAndSettle();
    expect(palette(), PanelPalette.dark);
  });
}
