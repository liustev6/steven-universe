import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_panel/weather/weather_data.dart';
import 'package:home_panel/weather/weather_provider.dart';
import 'package:home_panel/widgets/weather_widget.dart';

class _Fixed extends WeatherNotifier {
  _Fixed(this.value);
  final WeatherData? value;
  @override
  WeatherData? build() => value;
}

const _sf = WeatherData(
  locationName: 'San Francisco',
  temperatureC: 18.4,
  condition: WeatherCondition.partlyCloudy,
  isDay: true,
  todayLowC: 7.2,
  todayHighC: 23.8,
);

Future<void> _pumpTile(WidgetTester tester, WeatherData? data) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [weatherProvider.overrideWith(() => _Fixed(data))],
      child: const MaterialApp(
        home: Center(
          child: SizedBox(width: 236, height: 152, child: WeatherWidget()),
        ),
      ),
    ),
  );
}

void main() {
  group('WeatherData', () {
    test('round-trips through JSON', () {
      expect(WeatherData.fromJson(_sf.toJson()), _sf);
    });

    test('reads the wire format', () {
      final data = WeatherData.fromJson({
        'location': {'name': 'Toronto'},
        'current': {'temperature_c': -3, 'condition': 'snow', 'is_day': false},
      });
      expect(data.locationName, 'Toronto');
      expect(data.temperatureC, -3.0);
      expect(data.condition, WeatherCondition.snow);
      expect(data.isDay, isFalse);
      expect(data.todayLowC, isNull);
      expect(data.todayHighC, isNull);
    });

    test("reads today's low and high when present", () {
      final data = WeatherData.fromJson({
        'location': {'name': 'X'},
        'current': {'temperature_c': 10},
        'today': {'low_c': 7, 'high_c': 24.5},
      });
      expect((data.todayLowC, data.todayHighC), (7.0, 24.5));
    });

    test('an unknown condition becomes unknown instead of failing', () {
      final data = WeatherData.fromJson({
        'location': {'name': 'X'},
        'current': {'temperature_c': 1, 'condition': 'volcanic_ash'},
      });
      expect(data.condition, WeatherCondition.unknown);
    });

    test('missing required fields are a FormatException', () {
      expect(
        () => WeatherData.fromJson({
          'location': {'name': 'X'},
        }),
        throwsFormatException,
      );
    });
  });

  testWidgets('mock weather changes every 5 seconds', (tester) async {
    final container = ProviderContainer(
      overrides: [
        weatherProvider.overrideWith(() => MockWeather(random: math.Random(1))),
      ],
    );
    var updates = 0;
    container.listen(weatherProvider, (_, _) => updates++);

    final first = container.read(weatherProvider)!;
    expect(first.temperatureC, inInclusiveRange(-10, 35));
    expect(first.condition, isNot(WeatherCondition.unknown));
    expect(first.todayLowC, lessThan(first.temperatureC));
    expect(first.todayHighC, greaterThan(first.temperatureC));

    await tester.pump(const Duration(seconds: 4));
    expect(updates, 0);
    await tester.pump(const Duration(seconds: 1));
    expect(updates, 1);
    await tester.pump(const Duration(seconds: 10));
    expect(updates, 3);

    // Dispose inside the test so the mock's timer is cancelled before
    // flutter_test checks for pending timers.
    container.dispose();
  });

  testWidgets('the tile shows city, temperature with icon, and low/high', (
    tester,
  ) async {
    await _pumpTile(tester, _sf);
    expect(find.text('San Francisco'), findsOneWidget);
    expect(find.text('18°C'), findsOneWidget);
    expect(find.bySemanticsLabel('Partly Cloudy'), findsOneWidget);
    expect(find.text('7°C / 24°C'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('the tile shows dashes before the first update', (tester) async {
    await _pumpTile(tester, null);
    expect(find.text('--°C'), findsOneWidget);
    expect(find.byType(WeatherIcon), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('every condition draws by day and by night', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Wrap(
          children: [
            for (final c in WeatherCondition.values)
              for (final isDay in [true, false])
                WeatherIcon(c, isDay: isDay, size: 40),
          ],
        ),
      ),
    );
    expect(
      find.byType(WeatherIcon),
      findsNWidgets(WeatherCondition.values.length * 2),
    );
    expect(tester.takeException(), isNull);
  });
}
