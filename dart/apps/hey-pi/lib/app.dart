import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'connection/message_router.dart';
import 'display/dashboard_view.dart';
import 'display/speech_view.dart';
import 'display/sleep_view.dart';
import 'display/touch_guard.dart';
import 'state/handler/event_dispatcher.dart';
import 'protocol/messages.dart';
import 'state/store/app_store.dart';
import 'state/store/ui_mode.dart';
import 'ui/theme.dart';

class HeyPiApp extends ConsumerWidget {
  const HeyPiApp({super.key});

  static final _light = buildPanelTheme(Brightness.light);
  static final _dark = buildPanelTheme(Brightness.dark);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final themeMode = ref.watch(appStoreProvider.select((s) => s.themeMode));
    return MaterialApp(
      title: 'Hey Pi',
      debugShowCheckedModeBanner: false,
      theme: _light,
      darkTheme: _dark,
      themeMode: themeMode,
      home: const PanelRoot(),
    );
  }
}

/// Shows what [uiModeProvider] says: pi-server's speech state over the app's own mode.
class PanelRoot extends ConsumerWidget {
  const PanelRoot({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Creates the dispatcher at startup (its idle timer must run from boot)
    // and keeps it alive; and the link to pi-server, alive from boot too.
    ref.watch(eventDispatcherProvider);
    ref.watch(serverLinkProvider);
    final mode = ref.watch(uiModeProvider);
    return Scaffold(
      body: TouchGuard(
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: switch (mode) {
            UiMode.dashboard => const DashboardView(),
            UiMode.idle => const SleepView(),
            UiMode.listening => const SpeechView(SpeechState.listening),
            UiMode.thinking => const SpeechView(SpeechState.thinking),
            UiMode.speaking => const SpeechView(SpeechState.speaking),
          },
        ),
      ),
    );
  }
}
