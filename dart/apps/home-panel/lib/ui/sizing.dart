/// Layout constants in logical px. The device pixel ratio is pinned to 1.0
/// (flutter-pi `--pixel-ratio 1.0`), so logical px == screen px on the panel.
abstract final class Sizing {
  /// Panel: 1024 px across a 152.4 mm active area.
  static const double pxPerMm = 1024 / 152.4;

  /// Minimum tap target: 12 mm ≈ 81 px.
  static const double tapTarget = 12 * pxPerMm;

  static const double gap = 16;
  static const double radius = 28;
  static const double statusStripHeight = 44;
}
