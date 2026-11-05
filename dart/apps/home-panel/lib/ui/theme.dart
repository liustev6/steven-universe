import 'package:flutter/material.dart';

abstract final class PanelColors {
  static const background = Color(0xFF070B14);
  static const text = Color(0xFFF2F5FA);
  static const textMuted = Color(0x99F2F5FA);

  /// Sleep view: dark navy vignette, soft white time, grey details.
  static const sleepBackground = Color(0xFF0B0D12);
  static const sleepGlow = Color(0xFF1C1F2A);
  static const sleepTimeTop = Color(0xFFF4F4F7);
  static const sleepTimeBottom = Color(0xFFB4B6C1);
  static const sleepSecondary = Color(0xFF9497A3);

  /// Soft color fields behind the glass panels.
  static const glowIndigo = Color(0xFF4F46E5);
  static const glowTeal = Color(0xFF0EA5A4);
  static const glowRose = Color(0xFFDB2777);

  static const offline = Color(0xFFF87171);
}

ThemeData buildPanelTheme() {
  return ThemeData(
    brightness: Brightness.dark,
    scaffoldBackgroundColor: PanelColors.background,
    colorScheme: const ColorScheme.dark(
      surface: PanelColors.background,
      onSurface: PanelColors.text,
      primary: PanelColors.glowTeal,
    ),
    textTheme: const TextTheme(
      titleMedium: TextStyle(
        fontSize: 22,
        fontWeight: FontWeight.w500,
        color: PanelColors.text,
      ),
      bodyMedium: TextStyle(fontSize: 18, color: PanelColors.textMuted),
    ),
  );
}
