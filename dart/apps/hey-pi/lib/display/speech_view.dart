import 'package:flutter/material.dart';

import '../protocol/messages.dart';
import '../ui/ambient_background.dart';
import '../ui/theme.dart';
import 'assistant_figure.dart';

/// The screen while the voice assistant listens, thinks or speaks: its character on the
/// idle screens' background, shaded and dimmed like the sleep clock.
class SpeechView extends StatelessWidget {
  const SpeechView(this.speech, {super.key});

  final SpeechState speech;

  /// The character's height (its design is 360×320).
  static const double figureHeight = 480;

  @override
  Widget build(BuildContext context) => AmbientBackground(
    child: Center(
      child: SizedBox(
        height: figureHeight,
        child: Semantics(
          label: switch (speech) {
            SpeechState.listening => 'Listening',
            SpeechState.thinking => 'Thinking',
            _ => 'Speaking',
          },
          child: AssistantFigure(speech, shade: context.palette.assistant),
        ),
      ),
    ),
  );
}
