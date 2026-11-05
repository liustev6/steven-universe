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
/// Fill and border colours come from the current mode's palette.
abstract final class GlassTokens {
  static const bool enableBlur = true;
  static const double blurSigma = 20;
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
    final palette = context.palette;
    final borderRadius = BorderRadius.circular(radius);
    Widget panel = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: borderRadius,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [palette.glassFillTop, palette.glassFillBottom],
        ),
        border: Border.all(color: palette.glassBorder),
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

/// Static backdrop for the dashboard: the palette's base colour with soft
/// colour fields for the glass to sit on. Wraps [child] in a [BackdropGroup]
/// so every [GlassPanel] inside shares one blur pass.
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
    final palette = context.palette;
    final [first, second, third] = palette.glows;
    final opacity = palette.glowOpacity;
    return DecoratedBox(
      decoration: BoxDecoration(color: palette.background),
      child: Stack(
        fit: StackFit.expand,
        children: [
          _Glow(const Alignment(-0.9, -0.8), first, opacity, 0.9),
          _Glow(const Alignment(0.95, -0.2), second, opacity, 0.75),
          _Glow(const Alignment(-0.2, 1.1), third, opacity, 0.6),
        ],
      ),
    );
  }
}

class _Glow extends StatelessWidget {
  const _Glow(this.center, this.color, this.opacity, this.radius);

  final Alignment center;
  final Color color;
  final double opacity;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: RadialGradient(
          center: center,
          radius: radius,
          colors: [
            color.withValues(alpha: opacity),
            color.withValues(alpha: 0),
          ],
        ),
      ),
    );
  }
}
