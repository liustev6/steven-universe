/// Fakes for the connection tests: pi-server's side of the socket, driven by the test.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/connection/server_client.dart';

/// A socket the test drives: what pi-server says.
class FakeSocket implements Socket {
  final _incoming = StreamController<String>();

  void say(Map<String, Object?> message) => _incoming.add(jsonEncode(message));
  void sayRaw(String raw) => _incoming.add(raw);
  Future<void> drop() => _incoming.close();

  @override
  Stream<String> get incoming => _incoming.stream;

  @override
  Future<void> close() async {
    if (!_incoming.isClosed) await _incoming.close();
  }
}

/// pi-server, for as many connections as the client opens; a connect can be made to fail.
class FakeServer {
  final sockets = <FakeSocket>[];
  var refuse = 0;

  Future<Socket> connect(Uri url) async {
    if (refuse > 0) {
      refuse--;
      throw const RefusedForTest();
    }
    final s = FakeSocket();
    sockets.add(s);
    return s;
  }
}

class RefusedForTest implements Exception {
  const RefusedForTest();
}

ServerClient fastClient(FakeServer server) => ServerClient(
  url: Uri.parse('ws://pi.test/ws'),
  connect: server.connect,
  minBackoff: const Duration(milliseconds: 10),
  maxBackoff: const Duration(milliseconds: 40),
);

Future<void> until(bool Function() condition) async {
  for (var i = 0; i < 200 && !condition(); i++) {
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
  expect(condition(), isTrue);
}
