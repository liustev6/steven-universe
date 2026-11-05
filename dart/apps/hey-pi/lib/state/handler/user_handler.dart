import 'package:flutter/material.dart' show ThemeMode;

import '../events/app_event.dart';
import '../types/display_state.dart';
import 'event_dispatcher.dart';

/// Handlers for every [UserEvent] (events/user_events.dart). The dispatcher
/// has already restarted the idle timer before any of these run.

// Display ------------------------------------------------------------------

/// A touch: wake the panel if it's asleep.
void handleUserUncategorizedClick(
  UserUncategorizedClick event,
  HandlerContext ctx,
) {
  if (ctx.state.display.mode == DisplayMode.sleep) {
    ctx.store.setDisplay(const DisplayState(mode: DisplayMode.active));
  }
}

/// The Sleep tile: go to sleep now.
void handleSleepClick(SleepClick event, HandlerContext ctx) {
  ctx.store.setDisplay(const DisplayState(mode: DisplayMode.sleep));
}

// Settings -----------------------------------------------------------------

/// The theme tile: flip dark ↔ light.
void handleThemeModeClick(ThemeModeClick event, HandlerContext ctx) {
  ctx.store.setThemeMode(
    ctx.state.themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark,
  );
}
