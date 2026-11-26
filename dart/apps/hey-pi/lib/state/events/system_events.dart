part of 'app_event.dart';

/// Things the app raised itself, or the router raised for pi-server's messages and the link to it.
/// These don't count as user activity.
/// Handlers: `handler/system_handler.dart`.

/// No touch for the idle timeout. Raised by the idle timer. Handler:
/// `handleIdleTimeout` in `handler/system_handler.dart`.
final class IdleTimeout extends SystemEvent implements AppModeEvent {
  const IdleTimeout();
}

// pi-server ------------------------------------------------------------------

/// pi-server's speech state (its `speech` message), raised by the router. Handler:
/// `handleServerSpeech`.
final class ServerSpeech extends SystemEvent implements ServerStateEvent {
  const ServerSpeech(this.speech);

  final SpeechState speech;
}

/// The link to pi-server dropped (or never came up), raised by the router: what pi-server said no
/// longer holds. Handler: `handleServerDisconnected`.
final class ServerDisconnected extends SystemEvent implements ServerStateEvent {
  const ServerDisconnected();
}
