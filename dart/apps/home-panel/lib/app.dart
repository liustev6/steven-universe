import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'display/dashboard_view.dart';
import 'display/sleep_view.dart';
import 'display/touch_guard.dart';
import 'state/display_machine.dart';
import 'state/panel_providers.dart';
import 'ui/theme.dart';

class HomePanelApp extends StatelessWidget {
  const HomePanelApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Home Panel',
      debugShowCheckedModeBanner: false,
      theme: buildPanelTheme(),
      home: const PanelRoot(),
    );
  }
}

/// Switches between the dashboard and the sleep view.
class PanelRoot extends ConsumerWidget {
  const PanelRoot({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(displayProvider.select((s) => s.mode));
    return Scaffold(
      backgroundColor: PanelColors.sleepBackground,
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
