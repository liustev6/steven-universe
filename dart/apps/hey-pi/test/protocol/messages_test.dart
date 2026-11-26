import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/protocol/messages.dart';

void main() {
  test('speech, each state', () {
    for (final state in SpeechState.values) {
      expect(
        ServerMessage.decode('{"type":"speech","state":"${state.name}"}'),
        isA<SpeechMessage>().having((m) => m.state, 'state', state),
      );
    }
  });

  test('never throws: unknown, malformed, the wrong shape', () {
    for (final raw in [
      '{"type":"speech","state":"thinking"}', // not a speech state
      '{"type":"speech"}',
      '{"type":"assistant","phase":"idle"}', // the old protocol
      'not json',
      '[1, 2]',
    ]) {
      expect(ServerMessage.decode(raw), isA<UnknownMessage>(), reason: raw);
    }
  });

  test('the voice button: wake', () {
    expect(jsonDecode(const WakeMessage().encode()), {'type': 'wake'});
  });
}
