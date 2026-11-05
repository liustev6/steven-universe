import 'dart:async';
import 'dart:math' as math;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'weather_data.dart';

/// Current weather for the dashboard, or null until the first update.
///
/// For now this is [MockWeather]. Once the pi-agent connection exists, a
/// notifier fed by `widget_update` messages replaces it; widgets keep
/// watching this same provider.
final weatherProvider = NotifierProvider<WeatherNotifier, WeatherData?>(
  MockWeather.new,
);

/// Base type for anything that supplies [weatherProvider].
abstract class WeatherNotifier extends Notifier<WeatherData?> {}

/// Test data: a random temperature and condition every [interval].
class MockWeather extends WeatherNotifier {
  MockWeather({math.Random? random, this.interval = const Duration(seconds: 5)})
    : _random = random ?? math.Random();

  final math.Random _random;
  final Duration interval;

  static const locationName = 'San Francisco';

  @override
  WeatherData? build() {
    final timer = Timer.periodic(interval, (_) => state = _next());
    ref.onDispose(timer.cancel);
    return _next();
  }

  WeatherData _next() {
    final conditions = WeatherCondition.values
        .where((c) => c != WeatherCondition.unknown)
        .toList();
    final hour = DateTime.now().hour;
    // −10.0 to 35.0 °C, one decimal.
    final temperature = (_random.nextInt(451) - 100) / 10;
    return WeatherData(
      locationName: locationName,
      temperatureC: temperature,
      condition: conditions[_random.nextInt(conditions.length)],
      isDay: hour >= 6 && hour < 18,
      // Today's range always contains the current temperature.
      todayLowC: temperature - 1 - _random.nextInt(8),
      todayHighC: temperature + 1 + _random.nextInt(8),
    );
  }
}
