import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/app.dart';
import 'package:hey_pi/display/dashboard_view.dart';
import 'package:hey_pi/display/sleep_view.dart';
import 'package:hey_pi/display/assistant_figure.dart';
import 'package:hey_pi/display/speech_view.dart';
import 'package:hey_pi/protocol/messages.dart';
import 'package:hey_pi/state/store/server_store.dart';
import 'package:hey_pi/widgets/sleep_tile.dart';

void main() {
  Future<void> pumpPanel(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const ProviderScope(child: HeyPiApp()));
  }

  Future<void> idleUntilAsleep(WidgetTester tester) async {
    await tester.pump(const Duration(minutes: 2));
    await tester.pumpAndSettle();
  }

  testWidgets('starts on the dashboard at 1024×600 without overflow', (
    tester,
  ) async {
    await pumpPanel(tester);
    expect(find.byType(DashboardView), findsOneWidget);
    expect(find.byType(SleepView), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('2 minutes without a touch switches to the sleep clock', (
    tester,
  ) async {
    await pumpPanel(tester);
    await tester.pump(const Duration(minutes: 1, seconds: 59));
    expect(find.byType(DashboardView), findsOneWidget);

    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.byType(SleepView), findsOneWidget);
    expect(find.byType(DashboardView), findsNothing);
  });

  testWidgets('a touch resets the idle timer', (tester) async {
    await pumpPanel(tester);
    await tester.pump(const Duration(minutes: 1, seconds: 30));
    await tester.tapAt(const Offset(500, 300));
    await tester.pump(const Duration(minutes: 1));
    expect(find.byType(DashboardView), findsOneWidget);

    await tester.pump(const Duration(minutes: 1));
    await tester.pumpAndSettle();
    expect(find.byType(SleepView), findsOneWidget);
  });

  testWidgets('a touch on the sleep clock wakes the panel', (tester) async {
    await pumpPanel(tester);
    await idleUntilAsleep(tester);

    await tester.tapAt(const Offset(500, 300));
    await tester.pumpAndSettle();
    expect(find.byType(DashboardView), findsOneWidget);
    expect(find.byType(SleepView), findsNothing);
  });

  testWidgets('sleep tile switches to the sleep clock', (tester) async {
    await pumpPanel(tester);
    await tester.tap(find.byType(SleepTile));
    await tester.pumpAndSettle();
    expect(find.byType(SleepView), findsOneWidget);
    expect(find.byType(DashboardView), findsNothing);
  });

  test('sleep clock shifts position every 3 minutes and cycles', () {
    final t0 = DateTime.utc(2026, 11, 4, 12, 0);
    expect(
      sleepClockShiftFor(t0),
      sleepClockShiftFor(t0.add(const Duration(minutes: 2))),
    );
    expect(
      sleepClockShiftFor(t0),
      isNot(sleepClockShiftFor(t0.add(const Duration(minutes: 3)))),
    );
    final cycle = Duration(minutes: 3 * sleepClockShifts.length);
    expect(sleepClockShiftFor(t0), sleepClockShiftFor(t0.add(cycle)));
  });

  testWidgets(
    'speech shows over the dashboard and over idle, then the app\'s mode is back',
    (tester) async {
      await pumpPanel(tester);
      final container = ProviderScope.containerOf(
        tester.element(find.byType(HeyPiApp)),
      );
      void speech(SpeechState s) =>
          container.read(serverStoreProvider.notifier).setSpeech(s);

      // The figure loops forever: pump, not pumpAndSettle, while it shows.
      speech(SpeechState.listening);
      await tester.pump();
      expect(figure(SpeechState.listening), findsOneWidget);
      speech(SpeechState.speaking);
      await tester.pump();
      expect(figure(SpeechState.speaking), findsOneWidget);
      speech(SpeechState.idle);
      await tester.pumpAndSettle();
      expect(find.byType(DashboardView), findsOneWidget);

      await idleUntilAsleep(tester); // the app's own mode: idle
      speech(SpeechState.listening);
      await tester.pump();
      expect(find.byType(SpeechView), findsOneWidget);
      speech(SpeechState.idle);
      await tester.pumpAndSettle();
      expect(find.byType(SleepView), findsOneWidget);
    },
  );
}

Finder figure(SpeechState speech) =>
    find.byWidgetPredicate((w) => w is AssistantFigure && w.speech == speech);
