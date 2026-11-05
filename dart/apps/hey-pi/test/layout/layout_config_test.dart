import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/display/dashboard_view.dart';
import 'package:hey_pi/layout/layout_config.dart';
import 'package:hey_pi/layout/panel_grid.dart';
import 'package:hey_pi/widgets/assistant_widget.dart';
import 'package:hey_pi/widgets/clock_widget.dart';
import 'package:hey_pi/widgets/home_controls_widget.dart';
import 'package:hey_pi/widgets/placeholder_tile.dart';
import 'package:hey_pi/widgets/sleep_tile.dart';
import 'package:hey_pi/widgets/theme_mode_tile.dart';
import 'package:hey_pi/widgets/weather_widget.dart';

void main() {
  test('the dashboard layout fits the 12 × 7 grid with nothing skipped', () {
    final valid = validPlacements(dashboardLayout, columns: 12, rows: 7);
    expect(valid, hasLength(dashboardLayout.length));
  });

  test('the dashboard layout fills every one of the 84 cells', () {
    final cells = dashboardLayout.fold<int>(
      0,
      (sum, p) => sum + p.columnSpan * p.rowSpan,
    );
    expect(cells, 12 * 7);
  });

  testWidgets('the dashboard shows every widget at 1024×600 without overflow', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(home: Scaffold(body: DashboardView())),
      ),
    );
    expect(find.byType(ClockWidget), findsOneWidget);
    expect(find.byType(WeatherWidget), findsOneWidget);
    expect(find.byType(HomeControlsWidget), findsOneWidget);
    expect(find.byType(AssistantWidget), findsOneWidget);
    expect(find.byType(SleepTile), findsOneWidget);
    expect(find.byType(ThemeModeTile), findsOneWidget);
    expect(
      find.byType(PlaceholderTile),
      findsNWidgets(
        dashboardLayout.where((p) => p.widget is PlaceholderTile).length,
      ),
    );
    expect(tester.takeException(), isNull);
  });
}
