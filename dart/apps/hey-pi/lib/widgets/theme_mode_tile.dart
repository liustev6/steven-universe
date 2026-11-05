import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/events/app_event.dart';
import '../state/handler/event_dispatcher.dart';
import '../layout/panel_widget.dart';
import '../state/app_state.dart';
import '../ui/glass.dart';
import '../ui/sizing.dart';
import '../ui/theme.dart';

/// 1×1 tile that switches the dashboard between dark and light mode. Shows
/// the same half-filled circle in both modes (not a moon, so it doesn't look
/// like the Sleep tile); the icon follows the mode's colours.
class ThemeModeTile extends PanelWidget {
  const ThemeModeTile({super.key});

  @override
  int get columns => 1;

  @override
  int get rows => 1;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isDark =
        ref.watch(appStoreProvider.select((s) => s.themeMode)) ==
        ThemeMode.dark;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () =>
          ref.read(eventDispatcherProvider).dispatch(const ThemeModeClick()),
      child: GlassPanel(
        radius: Sizing.smallRadius,
        padding: EdgeInsets.zero,
        child: Center(
          child: Icon(
            Icons.contrast,
            color: context.palette.primaryColor,
            size: 26,
            semanticLabel: isDark ? 'Dark mode' : 'Light mode',
          ),
        ),
      ),
    );
  }
}
