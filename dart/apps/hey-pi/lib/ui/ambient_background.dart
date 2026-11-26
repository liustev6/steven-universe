import 'package:flutter/material.dart';

import 'theme.dart';

/// The calm full-screen look of the idle screens: a radial vignette
/// ([PanelPalette.sleepGlow] at the centre to [PanelPalette.sleepBackground] at
/// the edges), with everything on it (the sleep clock, the speech figure) drawn
/// [dimming] darker. Shared, so they match.
class AmbientBackground extends StatelessWidget {
  const AmbientBackground({super.key, required this.child});

  /// How much darker the page is drawn (0 = as designed, 1 = black).
  static const double dimming = 0.15;

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: RadialGradient(
          center: const Alignment(0, -0.05),
          radius: 1.1,
          colors: [palette.sleepGlow, palette.sleepBackground],
        ),
      ),
      // A black layer over everything dims the page evenly.
      child: DecoratedBox(
        position: DecorationPosition.foreground,
        decoration: const BoxDecoration(
          color: Color.fromRGBO(0, 0, 0, dimming),
        ),
        child: child,
      ),
    );
  }
}
