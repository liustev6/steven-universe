/// Weather data as home-hub will send it, inside
/// `{"type": "widget_update", "widget": "weather", "data": {...}}`.
///
/// Pure Dart (no Flutter imports), so it can move into the shared protocol
/// file later. Temperatures are always Celsius.
///
/// ```json
/// {"location": {"name": "San Francisco"},
///  "current": {"temperature_c": 18.4, "condition": "partly_cloudy", "is_day": true},
///  "today": {"low_c": 7.2, "high_c": 23.8}}
/// ```
/// `today` is optional.
library;

/// Provider-independent weather conditions. home-hub maps its weather
/// provider's codes (e.g. Open-Meteo / WMO codes) onto these, so changing
/// providers never touches the app.
enum WeatherCondition {
  clear('clear', 'Clear'),
  partlyCloudy('partly_cloudy', 'Partly Cloudy'),
  cloudy('cloudy', 'Cloudy'),
  fog('fog', 'Fog'),
  drizzle('drizzle', 'Drizzle'),
  rain('rain', 'Rain'),
  snow('snow', 'Snow'),
  sleet('sleet', 'Sleet'),
  thunderstorm('thunderstorm', 'Thunderstorm'),
  windy('windy', 'Windy'),

  /// Anything the app doesn't recognise (e.g. a newer backend).
  unknown('unknown', 'Unknown');

  const WeatherCondition(this.wire, this.label);

  /// The value used in JSON.
  final String wire;

  /// Human-readable name.
  final String label;

  static WeatherCondition fromWire(Object? value) => values.firstWhere(
    (c) => c.wire == value,
    orElse: () => WeatherCondition.unknown,
  );
}

class WeatherData {
  const WeatherData({
    required this.locationName,
    required this.temperatureC,
    required this.condition,
    required this.isDay,
    this.todayLowC,
    this.todayHighC,
  });

  /// Parses the `data` object of a weather `widget_update`. Throws
  /// [FormatException] if required fields are missing; an unrecognised
  /// condition becomes [WeatherCondition.unknown].
  factory WeatherData.fromJson(Map<String, Object?> json) {
    final location = json['location'];
    final current = json['current'];
    if (location is! Map || current is! Map) {
      throw const FormatException('weather: needs "location" and "current"');
    }
    final name = location['name'];
    final temperature = current['temperature_c'];
    if (name is! String || temperature is! num) {
      throw const FormatException(
        'weather: needs location.name and current.temperature_c',
      );
    }
    final today = json['today'];
    double? number(Object? v) => v is num ? v.toDouble() : null;
    return WeatherData(
      locationName: name,
      temperatureC: temperature.toDouble(),
      condition: WeatherCondition.fromWire(current['condition']),
      isDay: current['is_day'] != false,
      todayLowC: today is Map ? number(today['low_c']) : null,
      todayHighC: today is Map ? number(today['high_c']) : null,
    );
  }

  final String locationName;
  final double temperatureC;
  final WeatherCondition condition;

  /// Day or night at the location (picks sun vs moon icons).
  final bool isDay;

  /// Today's forecast low and high, if known.
  final double? todayLowC;
  final double? todayHighC;

  Map<String, Object?> toJson() => {
    'location': {'name': locationName},
    'current': {
      'temperature_c': temperatureC,
      'condition': condition.wire,
      'is_day': isDay,
    },
    if (todayLowC != null || todayHighC != null)
      'today': {'low_c': todayLowC, 'high_c': todayHighC},
  };

  @override
  bool operator ==(Object other) =>
      other is WeatherData &&
      other.locationName == locationName &&
      other.temperatureC == temperatureC &&
      other.condition == condition &&
      other.isDay == isDay &&
      other.todayLowC == todayLowC &&
      other.todayHighC == todayHighC;

  @override
  int get hashCode => Object.hash(
    locationName,
    temperatureC,
    condition,
    isDay,
    todayLowC,
    todayHighC,
  );
}
