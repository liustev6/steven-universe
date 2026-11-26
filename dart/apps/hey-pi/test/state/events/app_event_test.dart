import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/protocol/messages.dart';
import 'package:hey_pi/state/events/app_event.dart';

void main() {
  test('each event has one source type and its effect types', () {
    final cases = <(AppEvent, {bool user, bool mode, bool server})>[
      (const UserUncategorizedClick(), user: true, mode: true, server: false),
      (const SleepClick(), user: true, mode: true, server: false),
      (const ThemeModeClick(), user: true, mode: false, server: false),
      (const VoiceClick(), user: true, mode: false, server: false),
      (const IdleTimeout(), user: false, mode: true, server: false),
      (
        const ServerSpeech(SpeechState.listening),
        user: false,
        mode: false,
        server: true,
      ),
      (const ServerDisconnected(), user: false, mode: false, server: true),
    ];
    for (final (event, :user, :mode, :server) in cases) {
      final name = event.runtimeType.toString();
      expect(event is UserEvent, user, reason: '$name is UserEvent');
      expect(event is SystemEvent, !user, reason: '$name is SystemEvent');
      expect(event is AppModeEvent, mode, reason: '$name is AppModeEvent');
      expect(
        event is ServerStateEvent,
        server,
        reason: '$name is ServerStateEvent',
      );
    }
  });
}
