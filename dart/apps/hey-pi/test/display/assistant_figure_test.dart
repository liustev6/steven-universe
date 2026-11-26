import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/display/assistant_figure.dart';
import 'package:hey_pi/display/speech_view.dart';
import 'package:hey_pi/protocol/messages.dart';
import 'package:hey_pi/ui/theme.dart';

Widget _app(Widget child, Brightness b, {bool still = false}) => MaterialApp(
  theme: buildPanelTheme(b),
  home: MediaQuery(
    data: MediaQueryData(size: const Size(1024, 600), disableAnimations: still),
    child: child,
  ),
);

void main() {
  for (final b in Brightness.values) {
    for (final s in [
      SpeechState.listening,
      SpeechState.thinking,
      SpeechState.speaking,
    ]) {
      testWidgets('$s in ${b.name} mode: the palette shade, animating', (
        tester,
      ) async {
        await tester.pumpWidget(_app(SpeechView(s), b));
        final figure = tester.widget<AssistantFigure>(
          find.byType(AssistantFigure),
        );
        final palette = b == Brightness.dark
            ? PanelPalette.dark
            : PanelPalette.light;
        expect(figure.shade, palette.assistant);
        expect(tester.hasRunningAnimations, isTrue);
        // Through a whole loop (4.8 s) and a bit.
        for (var i = 0; i < 25; i++) {
          await tester.pump(const Duration(milliseconds: 200));
        }
        expect(tester.takeException(), isNull);
      });
    }
  }

  testWidgets('reduced motion: drawn still', (tester) async {
    await tester.pumpWidget(
      _app(
        const SpeechView(SpeechState.speaking),
        Brightness.dark,
        still: true,
      ),
    );
    expect(tester.hasRunningAnimations, isFalse);
  });
}
