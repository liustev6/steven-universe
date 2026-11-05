import 'package:flutter/material.dart';

abstract final class PanelColors {
  static const background = Color(0xFF070B14);

  /// White: headers and primary text (titles, city, temperature, icons).
  static const white = Color(0xFFF2F5FA);

  /// [white] at 60%: secondary text.
  static const textMuted = Color(0x99F2F5FA);

  /// White with shade: the clock's colour, white fading to light grey from
  /// top to bottom. It's a gradient, so text uses it by wrapping the `Text`
  /// in a `ShaderMask` (`blendMode: BlendMode.srcIn`,
  /// `shaderCallback: PanelColors.whiteWithShade.createShader`) rather than
  /// through a `color:`.
  static const whiteWithShade = LinearGradient(
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
    colors: [Color(0xFFF4F4F7), Color(0xFFB4B6C1)],
  );

  /// Sleep view background: dark navy vignette.
  static const sleepBackground = Color(0xFF0B0D12);
  static const sleepGlow = Color(0xFF1C1F2A);

  /// Clock (sleep view and dashboard card) details: AM/PM, seconds, date.
  /// The time itself uses [whiteWithShade].
  static const clockSecondary = Color(0xFF9497A3);

  /// Soft color fields behind the glass panels.
  static const glowIndigo = Color(0xFF4F46E5);
  static const glowTeal = Color(0xFF0EA5A4);
  static const glowRose = Color(0xFFDB2777);

  static const offline = Color(0xFFF87171);
}

ThemeData buildPanelTheme() {
  return ThemeData(
    brightness: Brightness.dark,
    fontFamily: 'Inter',
    scaffoldBackgroundColor: PanelColors.background,
    colorScheme: const ColorScheme.dark(
      surface: PanelColors.background,
      onSurface: PanelColors.white,
      primary: PanelColors.glowTeal,
    ),
    textTheme: const TextTheme(
      titleMedium: TextStyle(
        fontSize: 22,
        fontWeight: FontWeight.w500,
        color: PanelColors.white,
      ),
      bodyMedium: TextStyle(fontSize: 18, color: PanelColors.textMuted),
    ),
  );
}
