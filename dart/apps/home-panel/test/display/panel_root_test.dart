import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_panel/app.dart';
import 'package:home_panel/display/dashboard_view.dart';
import 'package:home_panel/display/sleep_view.dart';
import 'package:home_panel/display/touch_guard.dart';
import 'package:home_panel/widgets/status_strip.dart';

void main() {
  Future<void> pumpPanel(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const ProviderScope(child: HomePanelApp()));
  }

  bool inputBlocked(WidgetTester tester) {
    final ignore = tester.widget<IgnorePointer>(
      find
          .descendant(
            of: find.byType(TouchGuard),
            matching: find.byType(IgnorePointer),
          )
          .first,
    );
    return ignore.ignoring;
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

  testWidgets('a touch wakes the panel and the wake tap is swallowed', (
    tester,
  ) async {
    await pumpPanel(tester);
    await idleUntilAsleep(tester);
    expect(inputBlocked(tester), isTrue);

    await tester.tapAt(const Offset(500, 300));
    await tester.pump();
    expect(find.byType(DashboardView), findsOneWidget);
    expect(inputBlocked(tester), isTrue);

    await tester.pump(const Duration(milliseconds: 300));
    expect(inputBlocked(tester), isFalse);
    await tester.pumpAndSettle();
  });

  testWidgets('sleep button switches to the sleep clock', (tester) async {
    await pumpPanel(tester);
    await tester.tap(find.byType(SleepButton));
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
}
