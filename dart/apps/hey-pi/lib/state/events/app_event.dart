/// Every event in the app. The definitions live in the `part` files below,
/// grouped by where they come from:
///
/// - `user_events.dart` — [UserEvent]: something the user did (a touch, a
///   tile). Every user event counts as activity: the dispatcher restarts the
///   idle timer before handling it.
/// - `system_events.dart` — [SystemEvent]: something the app itself raised
///   (a timer).
///
/// Every event has one source type ([UserEvent] or [SystemEvent], via
/// `extends`) and may also have effect types ([AppModeEvent], via
/// `implements`), e.g. `SleepClick extends UserEvent implements AppModeEvent`.
///
/// They're `part`s of this one library so [AppEvent] can stay `sealed`: the
/// dispatcher's `switch` must then handle every event, or it won't compile.
/// Handlers: `state/handler/user_handler.dart` and `system_handler.dart`.
library;

import '../../protocol/messages.dart';

part 'user_events.dart';
part 'system_events.dart';

sealed class AppEvent {
  const AppEvent();
}

/// Something the user did. Restarts the idle timer.
sealed class UserEvent extends AppEvent {
  const UserEvent();
}

/// Something the app raised itself (e.g. a timer firing).
sealed class SystemEvent extends AppEvent {
  const SystemEvent();
}

// Effect types: what an event affects. An event can have any number of these
// alongside its source type.

/// Changes the app's own mode (dashboard / idle): the app store.
sealed class AppModeEvent implements AppEvent {}

/// Changes what the app knows of pi-server: the server store.
sealed class ServerStateEvent implements AppEvent {}
