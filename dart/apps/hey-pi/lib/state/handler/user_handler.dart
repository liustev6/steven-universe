import 'package:flutter/material.dart' show ThemeMode;

import '../../protocol/messages.dart';
import '../events/app_event.dart';
import '../types/app_mode.dart';
import '../store/handler_context.dart';

/// Handlers for every [UserEvent] (events/user_events.dart). The dispatcher
/// has already restarted the idle timer before any of these run.

// Display ------------------------------------------------------------------

/// A touch: wake the panel if it's asleep.
void handleUserUncategorizedClick(
  UserUncategorizedClick event,
  HandlerContext ctx,
) {
  if (ctx.state.mode == AppMode.idle) {
    ctx.store.setMode(AppMode.dashboard);
  }
}

/// The Sleep tile: go to sleep now.
void handleSleepClick(SleepClick event, HandlerContext ctx) {
  ctx.store.setMode(AppMode.idle);
}

// Settings -----------------------------------------------------------------

/// The theme tile: flip dark ↔ light.
void handleThemeModeClick(ThemeModeClick event, HandlerContext ctx) {
  ctx.store.setThemeMode(
    ctx.state.themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark,
  );
}

// Voice -----------------------------------------------------------------------

/// The voice tile: pi-server opens a voice session (or barges in over a reply). The screen
/// follows its `speech` messages, like for the wake word; nothing changes here.
void handleVoiceClick(VoiceClick event, HandlerContext ctx) {
  ctx.send(const WakeMessage());
}
