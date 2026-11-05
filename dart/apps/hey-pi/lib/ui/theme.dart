import 'package:flutter/material.dart';

/// Design tokens: the raw colours. Widgets don't use these directly; they
/// read the current mode's [PanelPalette] (`context.palette`), which picks
/// the right token for dark or light mode.
abstract final class PanelColors {
  /// Primary: the shaded white (the clock's colour), white fading to light
  /// grey top to bottom. Headline text and the white parts of icons.
  static const primary = LinearGradient(
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
    colors: [Color(0xFFF4F4F7), Color(0xFFB4B6C1)],
  );

  /// Primary for light mode: shaded near-black.
  static const primaryInverse = LinearGradient(
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
    colors: [Color(0xFF1C1F2A), Color(0xFF4A4F5E)],
  );

  /// Secondary: the subtitle grey (the clock's date, AM/PM, seconds).
  static const secondary = Color(0xFF9497A3);

  /// Secondary for light mode.
  static const secondaryInverse = Color(0xFF5B6070);

  static const offline = Color(0xFFF87171);
}

/// The colours for one mode. Read it with `context.palette`.
@immutable
class PanelPalette extends ThemeExtension<PanelPalette> {
  const PanelPalette({
    required this.primary,
    required this.primaryColor,
    required this.secondary,
    required this.background,
    required this.glows,
    required this.glowOpacity,
    required this.sleepGlow,
    required this.sleepBackground,
    required this.glassFillTop,
    required this.glassFillBottom,
    required this.glassBorder,
  });

  /// Headline text and white icon parts. A gradient: apply it with
  /// `ShaderMask(blendMode: BlendMode.srcIn, shaderCallback:
  /// palette.primary.createShader, child: ...)`.
  final LinearGradient primary;

  /// Flat version of [primary], for text and icons that aren't shaded.
  final Color primaryColor;

  /// Subtitles, secondary text and quiet icons.
  final Color secondary;

  /// Dashboard background: a flat [background] with soft colour fields
  /// ([glows], at [glowOpacity]) for the glass to sit on.
  final Color background;
  final List<Color> glows;
  final double glowOpacity;

  /// Sleep view background: a plain radial vignette from [sleepGlow]
  /// (centre) to [sleepBackground] (edges), calmer than the dashboard.
  final Color sleepGlow;
  final Color sleepBackground;

  /// Glass panels. The glass contrasts with the background: a light (white)
  /// tint over the dark background in dark mode, a dark tint over the pale
  /// background in light mode.
  final Color glassFillTop;
  final Color glassFillBottom;
  final Color glassBorder;

  static const dark = PanelPalette(
    primary: PanelColors.primary,
    primaryColor: Color(0xFFF2F5FA),
    secondary: PanelColors.secondary,
    background: Color(0xFF070B14),
    glows: [Color(0xFF4F46E5), Color(0xFF0EA5A4), Color(0xFFDB2777)],
    glowOpacity: 0.45,
    sleepGlow: Color(0xFF1C1F2A),
    sleepBackground: Color(0xFF0B0D12),
    // White tint: lighter than the background.
    glassFillTop: Color(0x26FFFFFF),
    glassFillBottom: Color(0x0AFFFFFF),
    glassBorder: Color(0x33FFFFFF),
  );

  static const light = PanelPalette(
    primary: PanelColors.primaryInverse,
    primaryColor: Color(0xFF1C1F2A),
    secondary: PanelColors.secondaryInverse,
    background: Color(0xFFE8ECF4),
    glows: [Color(0xFF818CF8), Color(0xFF2DD4BF), Color(0xFFF472B6)],
    glowOpacity: 0.35,
    sleepGlow: Color(0xFFF7F8FC),
    sleepBackground: Color(0xFFDFE4ED),
    // Near-black (#1C1F2A) tint: darker than the background.
    glassFillTop: Color(0x0F1C1F2A),
    glassFillBottom: Color(0x051C1F2A),
    glassBorder: Color(0x1A1C1F2A),
  );

  @override
  PanelPalette copyWith() => this;

  /// Mode switches are instant, so no blending between palettes.
  @override
  PanelPalette lerp(PanelPalette? other, double t) =>
      t < 0.5 || other == null ? this : other;
}

extension PanelPaletteContext on BuildContext {
  /// The current mode's colours (dark if no panel theme is installed).
  PanelPalette get palette =>
      Theme.of(this).extension<PanelPalette>() ?? PanelPalette.dark;
}

ThemeData buildPanelTheme(Brightness brightness) {
  final palette = brightness == Brightness.dark
      ? PanelPalette.dark
      : PanelPalette.light;
  final scheme = brightness == Brightness.dark
      ? const ColorScheme.dark()
      : const ColorScheme.light();
  return ThemeData(
    brightness: brightness,
    fontFamily: 'Inter',
    scaffoldBackgroundColor: palette.background,
    colorScheme: scheme.copyWith(
      surface: palette.background,
      onSurface: palette.primaryColor,
    ),
    textTheme: TextTheme(
      titleMedium: TextStyle(
        fontSize: 22,
        fontWeight: FontWeight.w500,
        color: palette.primaryColor,
      ),
      bodyMedium: TextStyle(fontSize: 18, color: palette.secondary),
    ),
    extensions: [palette],
  );
}
