import 'dart:ui' show ImageFilter;

import 'package:flutter/material.dart';

import 'sizing.dart';
import 'theme.dart';

/// Lightweight "glass" look built from plain Flutter: a translucent gradient
/// fill, a hairline border and a top highlight over a static background.
///
/// The only GPU-heavy part is the backdrop blur. All panels share one blur
/// pass via [BackdropGroup] (see [GlassBackground]), and [enableBlur] turns it
/// off entirely if the Pi can't keep up. Without blur it still reads as glass
/// because the background is soft and out of focus already.
abstract final class GlassTokens {
  static const bool enableBlur = true;
  static const double blurSigma = 20;

  static const fillTop = Color(0x26FFFFFF);
  static const fillBottom = Color(0x0AFFFFFF);
  static const border = Color(0x33FFFFFF);
}

class GlassPanel extends StatelessWidget {
  const GlassPanel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(Sizing.gap + 4),
    this.radius = Sizing.radius,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final borderRadius = BorderRadius.circular(radius);
    Widget panel = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: borderRadius,
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [GlassTokens.fillTop, GlassTokens.fillBottom],
        ),
        border: Border.all(color: GlassTokens.border),
      ),
      child: Padding(padding: padding, child: child),
    );
    if (GlassTokens.enableBlur) {
      panel = BackdropFilter.grouped(
        filter: ImageFilter.blur(
          sigmaX: GlassTokens.blurSigma,
          sigmaY: GlassTokens.blurSigma,
        ),
        child: panel,
      );
    }
    return ClipRRect(borderRadius: borderRadius, child: panel);
  }
}

/// Static backdrop for the dashboard: dark base with a few soft color fields
/// for the glass to sit on. Wraps [child] in a [BackdropGroup] so every
/// [GlassPanel] inside shares one blur pass.
class GlassBackground extends StatelessWidget {
  const GlassBackground({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        const RepaintBoundary(child: _ColorFields()),
        BackdropGroup(child: child),
      ],
    );
  }
}

class _ColorFields extends StatelessWidget {
  const _ColorFields();

  @override
  Widget build(BuildContext context) {
    return const DecoratedBox(
      decoration: BoxDecoration(color: PanelColors.background),
      child: Stack(
        fit: StackFit.expand,
        children: [
          _Glow(Alignment(-0.9, -0.8), PanelColors.glowIndigo, 0.9),
          _Glow(Alignment(0.95, -0.2), PanelColors.glowTeal, 0.75),
          _Glow(Alignment(-0.2, 1.1), PanelColors.glowRose, 0.6),
        ],
      ),
    );
  }
}

class _Glow extends StatelessWidget {
  const _Glow(this.center, this.color, this.radius);

  final Alignment center;
  final Color color;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: RadialGradient(
          center: center,
          radius: radius,
          colors: [color.withValues(alpha: 0.45), color.withValues(alpha: 0)],
        ),
      ),
    );
  }
}
