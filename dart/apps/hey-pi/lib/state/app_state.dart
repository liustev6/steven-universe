import 'package:flutter/material.dart' show ThemeMode;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'types/display_state.dart';

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
  const AppState({required this.display, required this.themeMode});

  static const initial = AppState(
    display: DisplayState.initial,
    themeMode: ThemeMode.dark,
  );

  /// Dashboard or sleep, and whether touches are blocked.
  final DisplayState display;

  /// Light or dark mode, for both the dashboard and the sleep view.
  final ThemeMode themeMode;

  AppState copyWith({DisplayState? display, ThemeMode? themeMode}) => AppState(
    display: display ?? this.display,
    themeMode: themeMode ?? this.themeMode,
  );

  @override
  bool operator ==(Object other) =>
      other is AppState &&
      other.display == display &&
      other.themeMode == themeMode;

  @override
  int get hashCode => Object.hash(display, themeMode);

  @override
  String toString() => 'AppState($display, $themeMode)';
}

/// The store: [AppState] and its setters.
class AppStore extends Notifier<AppState> {
  @override
  AppState build() => AppState.initial;

  void setDisplay(DisplayState display) =>
      state = state.copyWith(display: display);

  void setThemeMode(ThemeMode themeMode) =>
      state = state.copyWith(themeMode: themeMode);
}
