import 'package:flutter/material.dart' show ThemeMode;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../types/app_mode.dart';

/// The app's single store, in the spirit of Zustand: state plus plain
/// setters, nothing else. What should happen when something occurs (a touch,
/// a timeout, a button) is defined in `state/events/` and handled in
/// `state/handler/`, which works out the new values and calls these setters.
///
/// Widgets subscribe to just the slice they need:
///
/// ```dart
/// final mode = ref.watch(appStoreProvider.select((s) => s.themeMode));
/// ```
final appStoreProvider = NotifierProvider<AppStore, AppState>(AppStore.new);

class AppState {
  const AppState({required this.mode, required this.themeMode});

  static const initial = AppState(
    mode: AppMode.dashboard,
    themeMode: ThemeMode.dark,
  );

  /// The app's own mode: dashboard, or idle after no touch (app-only: never sent).
  final AppMode mode;

  /// Light or dark mode, for both the dashboard and the sleep view.
  final ThemeMode themeMode;

  AppState copyWith({AppMode? mode, ThemeMode? themeMode}) =>
      AppState(mode: mode ?? this.mode, themeMode: themeMode ?? this.themeMode);

  @override
  bool operator ==(Object other) =>
      other is AppState && other.mode == mode && other.themeMode == themeMode;

  @override
  int get hashCode => Object.hash(mode, themeMode);

  @override
  String toString() => 'AppState($mode, $themeMode)';
}

/// The store: [AppState] and its setters.
class AppStore extends Notifier<AppState> {
  @override
  AppState build() => AppState.initial;

  void setMode(AppMode mode) => state = state.copyWith(mode: mode);

  void setThemeMode(ThemeMode themeMode) =>
      state = state.copyWith(themeMode: themeMode);
}
