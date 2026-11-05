import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../app_state.dart';
import '../events/app_event.dart';
import 'system_handler.dart';
import 'user_handler.dart';

/// The dispatcher. Widgets send events here:
///
/// ```dart
/// ref.read(eventDispatcherProvider).dispatch(const SleepClick());
/// ```
///
/// It must exist from app start so the idle timer runs; `PanelRoot` watches
/// it.
final eventDispatcherProvider = Provider<EventDispatcher>((ref) {
  final dispatcher = EventDispatcher(ref);
  ref.onDispose(dispatcher.dispose);
  return dispatcher;
});

/// Routes each [AppEvent] to its handler, and owns the idle timer: every
/// [UserEvent] restarts it, and when it runs out it raises [IdleTimeout].
/// [AppEvent] is sealed, so adding an event without a handler here is a
/// compile error.
class EventDispatcher {
  EventDispatcher(Ref ref) {
    _ctx = HandlerContext(ref, dispatch: dispatch);
    // The idle countdown starts at boot, before any touch.
    _restartIdleTimer();
  }

  /// No user event for this long raises [IdleTimeout] (→ sleep).
  static const idleTimeout = Duration(minutes: 2);

  late final HandlerContext _ctx;
  Timer? _idleTimer;

  void dispatch(AppEvent event) {
    // Any user event counts as activity.
    if (event is UserEvent) _restartIdleTimer();
    _route(event);
  }

  void _route(AppEvent event) => switch (event) {
    // user events → user_handler.dart
    UserUncategorizedClick() => handleUserUncategorizedClick(event, _ctx),
    SleepClick() => handleSleepClick(event, _ctx),
    ThemeModeClick() => handleThemeModeClick(event, _ctx),
    // system events → system_handler.dart
    IdleTimeout() => handleIdleTimeout(event, _ctx),
  };

  void _restartIdleTimer() {
    _idleTimer?.cancel();
    _idleTimer = Timer(idleTimeout, () => dispatch(const IdleTimeout()));
  }

  void dispose() => _idleTimer?.cancel();
}

/// Everything a handler may use: read the state, change it through the
/// store's setters, and raise further events.
class HandlerContext {
  HandlerContext(this._ref, {required this.dispatch});

  final Ref _ref;

  /// Raise another event (goes through the dispatcher like any other).
  final void Function(AppEvent event) dispatch;

  /// The current state.
  AppState get state => _ref.read(appStoreProvider);

  /// The store's setters.
  AppStore get store => _ref.read(appStoreProvider.notifier);
}
