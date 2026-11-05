import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'display/dashboard_view.dart';
import 'display/sleep_view.dart';
import 'display/touch_guard.dart';
import 'state/handler/event_dispatcher.dart';
import 'state/app_state.dart';
import 'state/types/display_state.dart';
import 'ui/theme.dart';

class HomePanelApp extends ConsumerWidget {
  const HomePanelApp({super.key});

  static final _light = buildPanelTheme(Brightness.light);
  static final _dark = buildPanelTheme(Brightness.dark);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final themeMode = ref.watch(appStoreProvider.select((s) => s.themeMode));
    return MaterialApp(
      title: 'Home Panel',
      debugShowCheckedModeBanner: false,
      theme: _light,
      darkTheme: _dark,
      themeMode: themeMode,
      home: const PanelRoot(),
    );
  }
}

/// Switches between the dashboard and the sleep view.
class PanelRoot extends ConsumerWidget {
  const PanelRoot({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Creates the dispatcher at startup (its idle timer must run from boot)
    // and keeps it alive.
    ref.watch(eventDispatcherProvider);
    final mode = ref.watch(appStoreProvider.select((s) => s.display.mode));
    return Scaffold(
      body: TouchGuard(
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: switch (mode) {
            DisplayMode.active => const DashboardView(),
            DisplayMode.sleep => const SleepView(),
          },
        ),
      ),
    );
  }
}
