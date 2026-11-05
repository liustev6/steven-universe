part of 'app_event.dart';

/// Things the app raised itself. These don't count as user activity.
/// Handlers: `handler/system_handler.dart`.

/// No touch for the idle timeout. Raised by the idle timer. Handler:
/// `handleIdleTimeout` in `handler/system_handler.dart`.
final class IdleTimeout extends SystemEvent implements DisplayEvent {
  const IdleTimeout();
}
