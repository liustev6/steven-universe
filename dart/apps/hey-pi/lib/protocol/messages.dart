/// The hey-pi ⇄ pi-server protocol (docs/specs/flutter_ui.md "Protocol"): JSON over WebSocket, one
/// object per message, tagged by `type`. Mirrors pi-server's `app/protocol.py`: keep both in sync.
///
/// pi-server → hey-pi, on connect and on every change:
///
/// ```json
/// {"type": "speech", "state": "idle" | "listening" | "thinking" | "speaking"}
/// ```
///
/// hey-pi → pi-server, the voice button (as if the wake word were heard):
///
/// ```json
/// {"type": "wake"}
/// ```
///
/// Pure Dart (no Flutter imports), so it can be shared later.
library;

import 'dart:convert';

/// The voice assistant, as pi-server reports it.
enum SpeechState {
  /// No voice session: the app shows its own mode.
  idle,

  /// The session waits for you, or hears you.
  listening,

  /// You've finished: it works out its reply (the agent, its tools), until the reply's first sound.
  thinking,

  /// Its voice: from the reply's first sound until it has played.
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

/// What hey-pi sends pi-server.
sealed class ScreenMessage {
  const ScreenMessage();

  /// The message on the wire.
  String encode();
}

/// The voice button: what hearing the wake word does (a voice session if there's none, a barge-in
/// over a reply).
final class WakeMessage extends ScreenMessage {
  const WakeMessage();

  @override
  String encode() => jsonEncode({'type': 'wake'});
}
