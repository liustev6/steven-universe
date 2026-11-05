/// Layout constants in logical px. flutter-pi runs with a device pixel ratio
/// of ≈1.0 (`--dimensions 269,158`), so logical px ≈ screen px on the panel.
abstract final class Sizing {
  /// Panel: 1024 px across a 152.4 mm active area.
  static const double pxPerMm = 1024 / 152.4;

  /// Minimum tap target: 12 mm ≈ 81 px.
  static const double tapTarget = 12 * pxPerMm;

  static const double gap = 16;
  static const double radius = 28;

  /// Corner radius for tiles that are only one cell wide or tall.
  static const double smallRadius = 20;
}
