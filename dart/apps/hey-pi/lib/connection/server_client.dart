/// The socket to pi-server (docs/specs/flutter_ui.md "ServerClient"): `dart:io` WebSocket, a 5 s
/// ping to notice a hung pi-server, reconnect with backoff (0.5 s → 10 s, reset after a connect).
/// Receive-only: the protocol has nothing for the app to send. Pure Dart.
library;

import 'dart:async';
import 'dart:developer' show log;
import 'dart:io';

import '../protocol/messages.dart';

enum LinkStatus { connecting, connected, disconnected }

/// One open socket, as [ServerClient] reads it: replaced by a fake in tests.
abstract interface class Socket {
  Stream<String> get incoming;
  Future<void> close();
}

typedef Connector = Future<Socket> Function(Uri url);

/// A real `dart:io` WebSocket.
class IoSocket implements Socket {
  IoSocket(this._ws);

  final WebSocket _ws;

  static Future<Socket> connect(Uri url) async {
    final ws = await WebSocket.connect(url.toString())
        .timeout(const Duration(seconds: 5));
    ws.pingInterval = const Duration(seconds: 5); // a hung pi-server closes it
    return IoSocket(ws);
  }

  @override
  Stream<String> get incoming => _ws.where((m) => m is String).cast<String>();

  @override
  Future<void> close() => _ws.close();
}

class ServerClient {
  ServerClient({
    required this.url,
    Connector? connect,
    this.minBackoff = const Duration(milliseconds: 500),
    this.maxBackoff = const Duration(seconds: 10),
  }) : _connect = connect ?? IoSocket.connect;

  final Uri url;
  final Duration minBackoff, maxBackoff;
  final Connector _connect;

  final _messages = StreamController<ServerMessage>.broadcast();
  final _status = StreamController<LinkStatus>.broadcast();
  LinkStatus _current = LinkStatus.disconnected;
  Socket? _socket;
  bool _running = false, _disposed = false;
  Completer<void>? _wake; // cuts a backoff short (dispose)

  Stream<ServerMessage> get messages => _messages.stream;
  Stream<LinkStatus> get status => _status.stream;
  LinkStatus get current => _current;

  /// Connects, and keeps reconnecting until [dispose].
  void start() {
    if (_running || _disposed) return;
    _running = true;
    unawaited(_loop());
  }

  Future<void> dispose() async {
    _disposed = true;
    _wake?.complete();
    await _socket?.close();
    await _messages.close();
    await _status.close();
  }

  void _setStatus(LinkStatus s) {
    if (_current == s || _disposed) return;
    _current = s;
    _status.add(s);
  }

  Future<void> _loop() async {
    var backoff = minBackoff;
    while (!_disposed) {
      _setStatus(LinkStatus.connecting);
      try {
        final socket = await _connect(url);
        if (_disposed) {
          await socket.close();
          return;
        }
        _socket = socket;
        _setStatus(LinkStatus.connected);
        backoff = minBackoff;
        await for (final raw in socket.incoming) {
          final message = ServerMessage.decode(raw);
          if (message is UnknownMessage) {
            log('ignored a message (${message.reason})', name: 'pi-server');
          } else if (!_disposed) {
            _messages.add(message);
          }
        }
      } on Object catch (e) {
        if (!_disposed) log('$e', name: 'pi-server');
      }
      _socket = null;
      if (_disposed) return;
      _setStatus(LinkStatus.disconnected);
      _wake = Completer<void>();
      await Future.any([Future<void>.delayed(backoff), _wake!.future]);
      backoff = backoff * 2 > maxBackoff ? maxBackoff : backoff * 2;
    }
  }
}
