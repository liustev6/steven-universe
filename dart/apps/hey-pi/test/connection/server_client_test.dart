import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/connection/server_client.dart';
import 'package:hey_pi/protocol/messages.dart';

import 'fakes.dart';

void main() {
  test(
    'connects, and delivers what pi-server says, skipping what it can\'t read',
    () async {
      final server = FakeServer();
      final c = fastClient(server)..start();
      final got = <ServerMessage>[];
      c.messages.listen(got.add);
      await until(() => c.current == LinkStatus.connected);
      server.sockets.single
        ..sayRaw('garbage')
        ..say({'type': 'speech', 'state': 'listening'})
        ..say({'type': 'snapshot'}); // the old protocol: unknown now
      await until(() => got.isNotEmpty);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(got, [
        isA<SpeechMessage>().having(
          (m) => m.state,
          'state',
          SpeechState.listening,
        ),
      ]);
      await c.dispose();
    },
  );

  test('a dropped socket reconnects; a refused connect is retried', () async {
    final server = FakeServer();
    final c = fastClient(server)..start();
    final seen = <LinkStatus>[];
    c.status.listen(seen.add);
    await until(() => c.current == LinkStatus.connected);
    await server.sockets.first.drop();
    server.refuse = 2;
    await until(
      () => server.sockets.length == 2 && c.current == LinkStatus.connected,
    );
    expect(
      seen,
      containsAllInOrder([
        LinkStatus.disconnected,
        LinkStatus.connecting,
        LinkStatus.connected,
      ]),
    );
    await c.dispose();
  });

  test('dispose stops it for good', () async {
    final server = FakeServer();
    final c = fastClient(server)..start();
    await until(() => c.current == LinkStatus.connected);
    await c.dispose();
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(server.sockets, hasLength(1));
  });
}
