/// The hey-pi ⇄ pi-server protocol (docs/specs/flutter_ui.md "Protocol"): JSON over WebSocket, one
/// object per message, tagged by `type`. Mirrors pi-server's `app/protocol.py`: keep both in sync.
///
/// One message, pi-server → hey-pi, on connect and on every change:
///
/// ```json
/// {"type": "speech", "state": "idle" | "listening" | "speaking"}
/// ```
///
/// hey-pi sends nothing. Pure Dart (no Flutter imports), so it can be shared later.
library;

import 'dart:convert';

/// The voice assistant, as pi-server reports it.
enum SpeechState {
  /// No voice session: the app shows its own mode.
  idle,

  /// The session waits for you, or hears you.
  listening,

  /// Its turn: from the end of what you said until its reply has played.
  speaking;

  static SpeechState? fromWire(Object? value) =>
      values.where((s) => s.name == value).firstOrNull;
}

sealed class ServerMessage {
  const ServerMessage();

  /// One message from the socket. Unknown or malformed: [UnknownMessage], never a throw.
  static ServerMessage decode(String raw) {
    final Object? json;
    try {
      json = jsonDecode(raw);
    } on FormatException {
      return UnknownMessage(raw, reason: 'not JSON');
    }
    if (json is! Map<String, Object?>) {
      return UnknownMessage(raw, reason: 'not an object');
    }
    return switch (json['type']) {
      'speech' => switch (SpeechState.fromWire(json['state'])) {
        final state? => SpeechMessage(state),
        null => UnknownMessage(raw, reason: 'unknown speech state'),
      },
      _ => UnknownMessage(raw, reason: 'unknown type ${json['type']}'),
    };
  }
}

/// pi-server's speech state: on connect, then on every change.
final class SpeechMessage extends ServerMessage {
  const SpeechMessage(this.state);

  final SpeechState state;
}

/// Not understood: logged and ignored, never fatal.
final class UnknownMessage extends ServerMessage {
  const UnknownMessage(this.raw, {required this.reason});

  final String raw;
  final String reason;
}
