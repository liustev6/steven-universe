import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../layout/cell_grid.dart';
import '../layout/panel_widget.dart';
import '../ui/glass.dart';
import '../ui/theme.dart';
import '../weather/weather_data.dart';
import '../weather/weather_provider.dart';

/// Current-weather glass card. The inside is laid out on a [CellGrid] of
/// 12 rows × 3 columns (counted from 0). The city and the temperature are
/// vertically centred in their areas (left-aligned), low/high sits at the
/// top left, and the icon is centred:
///
/// ```
///             columns 0–1            │ column 2
/// rows 0–2    location arrow + city (all columns)
/// rows 3–9    current temperature    │ rows 3–11  condition icon
/// rows 10–11  today's low / high (all columns)
/// ```
/// Only the temperature is bold.
class WeatherWidget extends PanelWidget {
  const WeatherWidget({super.key});

  @override
  int get columns => 3;

  @override
  int get rows => 2;

  /// Will open an hourly/daily forecast page.
  @override
  bool get expandable => true;

  // The card's inner grid (not to be confused with [columns]/[rows], the
  // card's size on the dashboard).
  static const _innerRows = 12;
  static const _innerColumns = 3;
  static const _rowGap = 0.0;
  static const _columnGap = 2.0;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final weather = ref.watch(weatherProvider);
    final body = Theme.of(context).textTheme.bodyMedium!;
    return GlassPanel(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final grid = CellGrid(
            constraints.biggest,
            rows: _innerRows,
            columns: _innerColumns,
            rowGap: _rowGap,
            columnGap: _columnGap,
          );
          return Stack(
            children: [
              // Rows 0–2, all columns: location arrow + city.
              Positioned.fromRect(
                rect: grid.rect(0, 0, columnSpan: 3, rowSpan: 3),
                // Arrow and name centred on each other, and vertically in
                // their area.
                child: Row(
                  children: [
                    const Icon(
                      Icons.near_me,
                      size: 20,
                      color: PanelColors.white,
                    ),
                    const SizedBox(width: 6),
                    // Fixed size; long names end with "…".
                    Flexible(
                      child: Text(
                        weather?.locationName ?? '—',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: body.copyWith(
                          fontSize: 18,
                          color: PanelColors.white,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              // Rows 3–9, columns 0–1: current temperature.
              Positioned.fromRect(
                rect: grid.rect(0, 3, columnSpan: 2, rowSpan: 7),
                child: FittedBox(
                  fit: BoxFit.scaleDown,
                  alignment: Alignment.centerLeft,
                  // Same white-with-shade as the clock's time.
                  child: ShaderMask(
                    blendMode: BlendMode.srcIn,
                    shaderCallback: PanelColors.whiteWithShade.createShader,
                    child: Text(
                      weather == null
                          ? '--°C'
                          : '${weather.temperatureC.round()}°C',
                      style: TextStyle(
                        fontSize: 42,
                        fontWeight: FontWeight.w600,
                        height: 1,
                        letterSpacing: -2,
                        color: PanelColors.white,
                        fontFeatures: const [FontFeature.tabularFigures()],
                      ),
                    ),
                  ),
                ),
              ),
              // Rows 3–11, column 2: condition icon.
              if (weather != null)
                Positioned.fromRect(
                  rect: grid.rect(2, 3, rowSpan: 9),
                  child: Center(
                    child: WeatherIcon(
                      weather.condition,
                      isDay: weather.isDay,
                      size: math.min(grid.column, grid.row * 8),
                    ),
                  ),
                ),
              // Rows 10–11, all columns: today's low / high.
              Positioned.fromRect(
                rect: grid.rect(0, 10, columnSpan: 3, rowSpan: 2),
                child: Align(
                  alignment: Alignment.topLeft,
                  child: Text(
                    _lowHigh(weather),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: body.copyWith(
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  /// "7°C / 24°C", or empty until the forecast is known.
  static String _lowHigh(WeatherData? w) {
    final low = w?.todayLowC;
    final high = w?.todayHighC;
    if (low == null || high == null) return '';
    return '${low.round()}°C / ${high.round()}°C';
  }
}

/// Condition icon, coloured like Apple's multicolour weather symbols. White
/// parts (clouds, fog, wind) use [PanelColors.whiteWithShade], like the
/// clock; sun, moon, rain and snow keep their colours. Where Material Icons
/// has no matching symbol, two icons are layered (sun behind a cloud, a
/// cloud over rain drops).
class WeatherIcon extends StatelessWidget {
  const WeatherIcon(
    this.condition, {
    super.key,
    required this.isDay,
    this.size = 30,
  });

  final WeatherCondition condition;
  final bool isDay;
  final double size;

  static const _sun = Color(0xFFFFD60A);
  static const _moon = Color(0xFFF5E6A3);
  static const _rain = Color(0xFF64D2FF);
  static const _snow = Color(0xFFE0F2FE);

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: condition.label,
      child: ExcludeSemantics(
        child: SizedBox.square(dimension: size, child: _glyph()),
      ),
    );
  }

  Widget _glyph() => switch (condition) {
    WeatherCondition.clear =>
      isDay
          ? _single(Icons.sunny, _sun)
          : _single(Icons.nightlight_round, _moon),
    // Sun (or moon) peeking out from behind a cloud.
    WeatherCondition.partlyCloudy => _behindCloud(
      isDay ? Icons.sunny : Icons.nightlight_round,
      isDay ? _sun : _moon,
    ),
    WeatherCondition.cloudy => _white(Icons.cloud, size),
    WeatherCondition.fog => _white(Icons.foggy, size),
    WeatherCondition.drizzle => _single(Icons.grain, _rain),
    // Cloud with rain drops below it.
    WeatherCondition.rain => _cloudOver(Icons.grain, _rain),
    WeatherCondition.snow => _single(Icons.ac_unit, _snow),
    WeatherCondition.sleet => _single(Icons.cloudy_snowing, _snow),
    WeatherCondition.thunderstorm => _single(Icons.thunderstorm, _sun),
    WeatherCondition.windy => _white(Icons.air, size),
    WeatherCondition.unknown => _white(Icons.cloud_queue, size),
  };

  Widget _single(IconData icon, Color color) =>
      Icon(icon, size: size, color: color);

  /// A white icon part, shaded white → light grey like the clock.
  Widget _white(IconData icon, double size) => ShaderMask(
    blendMode: BlendMode.srcIn,
    shaderCallback: PanelColors.whiteWithShade.createShader,
    child: Icon(icon, size: size, color: PanelColors.white),
  );

  /// [icon] small at the top right, partly covered by a cloud.
  Widget _behindCloud(IconData icon, Color color) => Stack(
    children: [
      Positioned(
        top: 0,
        right: 0,
        child: Icon(icon, size: size * 0.62, color: color),
      ),
      Positioned(left: 0, bottom: 0, child: _white(Icons.cloud, size * 0.85)),
    ],
  );

  /// A cloud at the top with [icon] (e.g. drops) below it.
  Widget _cloudOver(IconData icon, Color color) => Stack(
    children: [
      Positioned(
        left: size * 0.22,
        bottom: 0,
        child: Icon(icon, size: size * 0.5, color: color),
      ),
      Positioned(
        top: 0,
        left: size * 0.08,
        child: _white(Icons.cloud, size * 0.8),
      ),
    ],
  );
}
