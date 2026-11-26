import 'dart:math' as math;

import 'package:flutter/widgets.dart';
import 'package:path_parsing/path_parsing.dart';

import '../protocol/messages.dart';

/// The voice assistant's character, listening, thinking or speaking, shaded with [shade]
/// like the clock's time digits.
///
/// The design source is `assets/svgs/assistant-listening.svg`,
/// `assistant-thinking.svg` and `assistant-speaking.svg` (viewBox 360×320). They animate with CSS
/// (`@keyframes`, `var(--outline-color)`, `d: path()`), which Flutter's SVG
/// renderer doesn't run, so this painter draws the same paths and plays the same
/// keyframes (CSS `ease-in-out` between each pair). Change the SVGs, then
/// mirror the change here.
class AssistantFigure extends StatefulWidget {
  const AssistantFigure(this.speech, {super.key, required this.shade});

  final SpeechState speech;

  /// Every stroke and fill (the SVGs' `--outline-color`), as a gradient from
  /// the top of the drawing (the antennae) to its bottom (the chin).
  final Gradient shade;

  /// The drawing's top and bottom in the viewBox (strokes included), where
  /// [shade] starts and ends, as the clock's spans its digits.
  static const double drawingTop = 39, drawingBottom = 258;

  /// One loop of every animation: the thinking head's 4.8 s; the rest loop
  /// every 2.4 s, twice in it.
  static const loop = Duration(milliseconds: 4800);

  @override
  State<AssistantFigure> createState() => _AssistantFigureState();
}

class _AssistantFigureState extends State<AssistantFigure>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: AssistantFigure.loop,
  );
  bool _still = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // The SVGs' `prefers-reduced-motion`: no movement, waves at half.
    _still = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    if (_still) {
      _controller.stop();
    } else if (!_controller.isAnimating) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AspectRatio(
    aspectRatio: _viewBox.width / _viewBox.height,
    child: ShaderMask(
      blendMode: BlendMode.srcIn,
      shaderCallback: (box) => widget.shade.createShader(
        Rect.fromLTRB(
          box.left,
          box.top + box.height * AssistantFigure.drawingTop / _viewBox.height,
          box.right,
          box.top +
              box.height * AssistantFigure.drawingBottom / _viewBox.height,
        ),
      ),
      child: CustomPaint(
        painter: _FigurePainter(
          widget.speech,
          const Color(0xFFFFFFFF), // the mask colours it
          _controller,
          still: _still,
        ),
      ),
    ),
  );
}

const _viewBox = Size(360, 320);

class _FigurePainter extends CustomPainter {
  _FigurePainter(this.speech, this.color, this.time, {required this.still})
    : super(repaint: time);

  final SpeechState speech;
  final Color color;
  final Animation<double> time;
  final bool still;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / _viewBox.width, size.height / _viewBox.height);
    final slow = still ? null : time.value; // the 4.8 s loop
    final t = slow == null ? null : (slow * 2) % 1; // the 2.4 s loop
    switch (speech) {
      case SpeechState.listening:
        _listening(canvas, t);
      case SpeechState.thinking:
        _thinking(canvas, t, slow);
      case SpeechState.speaking:
        _speaking(canvas, t);
      case SpeechState.idle:
        break;
    }
  }

  // assistant-listening.svg ---------------------------------------------------

  void _listening(Canvas c, double? t) {
    // .va-listen-head: the head tilts towards the cupped ear.
    _transformed(c, const Offset(180, 180), deg: _head.at(t), () {
      _stroke(c, _lAntennae, 14);
      _stroke(c, _lHead, 14);
      _stroke(c, _lEyes, 14);
      _stroke(c, _lMouth, 12);
      _stroke(c, _lEar, 14);
    });
    // .va-listen-hand: the hand cupping the ear.
    _transformed(
      c,
      const Offset(303, 181),
      dx: _cupX.at(t),
      dy: _cupY.at(t),
      deg: _cupDeg.at(t),
      () {
        _stroke(c, _lCupHand, 14);
        _stroke(c, _lCupThumb, 10);
      },
    );
    // .va-listen-free: the other hand responds.
    _transformed(
      c,
      const Offset(58, 213),
      dy: _freeY.at(t),
      deg: _freeDeg.at(t),
      () => _hand(
        c,
        const Rect.fromLTWH(39, 195, 20, 37),
        -18,
        const Offset(49, 213),
      ),
    );
    // .va-listen-wave: incoming voice; the second 0.16 s later.
    _waves(c, t, [_lWaveNear, _lWaveFar]);
  }

  // assistant-thinking.svg ----------------------------------------------------

  void _thinking(Canvas c, double? t, double? slow) {
    // .va-thinking-head: tilted -3°, pondering to -6° and back (4.8 s); the
    // hands go with it.
    _transformed(
      c,
      const Offset(176, 190),
      deg: slow == null ? -3 : _ponder.at(slow),
      () {
        _stroke(c, _sAntennae, 14);
        _stroke(c, _sHead, 14);
        // .va-thinking-eyes: a glance up and to the right.
        _transformed(
          c,
          Offset.zero,
          dx: _glanceX.at(slow),
          dy: _glanceY.at(slow),
          () => _stroke(c, _tEyes, 14),
        );
        _stroke(c, _tMouth, 12);
        _hand(
          c,
          const Rect.fromLTWH(44, 205, 20, 34),
          -12,
          const Offset(54, 222),
        );
        c.drawPath(_tChinHand, Paint()..color = color); // under the chin
      },
    );
    // .va-thinking-dot: three thought dots pulse, 0.25 s apart.
    for (final (i, (centre, radius)) in _tDots.indexed) {
      final opacity = t == null ? 0.65 : _thought.at((t - i * 250 / 2400) % 1);
      c.drawCircle(
        centre,
        radius,
        Paint()..color = color.withValues(alpha: color.a * opacity),
      );
    }
  }

  // assistant-speaking.svg ----------------------------------------------------

  void _speaking(Canvas c, double? t) {
    _stroke(c, _sAntennae, 14);
    _stroke(c, _sHead, 14);
    _hand(c, const Rect.fromLTWH(44, 199, 20, 38), -15, const Offset(54, 218));
    // .va-speak-hand: a gentle gesture.
    _transformed(
      c,
      const Offset(302, 215),
      dy: _gestureY.at(t),
      deg: _gestureDeg.at(t),
      () => _hand(
        c,
        const Rect.fromLTWH(292, 198, 20, 38),
        15,
        const Offset(302, 217),
      ),
    );
    _stroke(c, _sEyes, 14);
    // .va-speak-mouth: a bottom semicircle that opens and closes (its ry).
    final ry = t == null ? 20.0 : _mouthRy.at(t);
    _stroke(
      c,
      Path()
        ..moveTo(156, 190)
        ..lineTo(196, 190)
        ..arcToPoint(const Offset(156, 190), radius: Radius.elliptical(20, ry))
        ..close(),
      12,
    );
    // .va-speak-wave: outgoing voice, three waves, 0.16 s apart.
    _waves(c, t, [_sWaveNear, _sWaveMid, _sWaveFar]);
  }

  // Drawing -------------------------------------------------------------------

  void _stroke(Canvas c, Path path, double width) => c.drawPath(
    path,
    Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = width
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..color = color,
  );

  /// A solid hand: a fully rounded rect, rotated [deg] about [pivot].
  void _hand(Canvas c, Rect rect, double deg, Offset pivot) =>
      _transformed(c, pivot, deg: deg, () {
        c.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(10)),
          Paint()..color = color,
        );
      });

  /// Voice waves, nearest first; each one 0.16 s after the one before.
  void _waves(Canvas c, double? t, List<Path> waves) {
    const delay = 160 / 2400;
    for (final (i, path) in waves.indexed) {
      final offset = i * delay;
      final opacity = t == null ? 0.5 : _wave.at((t - offset) % 1);
      c.drawPath(
        path,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 7.5
          ..strokeCap = StrokeCap.round
          ..color = color.withValues(alpha: color.a * opacity),
      );
    }
  }

  /// CSS `transform: translate(dx, dy) rotate(deg)` with `transform-origin`.
  static void _transformed(
    Canvas c,
    Offset origin,
    void Function() draw, {
    double dx = 0,
    double dy = 0,
    double deg = 0,
  }) {
    c
      ..save()
      ..translate(origin.dx + dx, origin.dy + dy)
      ..rotate(deg * math.pi / 180)
      ..translate(-origin.dx, -origin.dy);
    draw();
    c.restore();
  }

  @override
  bool shouldRepaint(_FigurePainter old) =>
      old.speech != speech || old.color != color || old.still != still;
}

/// One animated property: CSS keyframes as (offset 0–1, value), eased with
/// `ease-in-out` between each pair. No time (reduced motion): the value without
/// the animation, 0.
class _Track {
  const _Track(this.frames);

  final List<(double, double)> frames;

  double at(double? t) {
    if (t == null) return 0;
    for (var i = 1; i < frames.length; i++) {
      final (t1, v1) = frames[i];
      if (t <= t1) {
        final (t0, v0) = frames[i - 1];
        final eased = Curves.easeInOut.transform((t - t0) / (t1 - t0));
        return v0 + (v1 - v0) * eased;
      }
    }
    return frames.last.$2;
  }
}

// The SVGs' keyframes.
const _head = _Track([
  (0, 0),
  (.30, 3),
  (.52, 1),
  (.70, 2.5),
  (1, 0),
]); // va-attend
const _cupX = _Track([(0, 0), (.30, -2), (.60, -1), (1, 0)]); // va-cup
const _cupY = _Track([(0, 0), (.30, -4), (.60, -2), (1, 0)]);
const _cupDeg = _Track([(0, 0), (.30, -5), (.60, -2), (1, 0)]);
const _freeY = _Track([
  (0, 0),
  (.30, -7),
  (.52, -2),
  (.70, -5),
  (1, 0),
]); // va-respond
const _freeDeg = _Track([(0, -8), (.30, 5), (.52, -4), (.70, 2), (1, -8)]);
const _gestureY = _Track([(0, 0), (.38, -4), (.65, -1), (1, 0)]); // va-gesture
const _gestureDeg = _Track([(0, 8), (.38, -5), (.65, 3), (1, 8)]);
const _mouthRy = _Track([
  (0, 5),
  (.24, 17),
  (.42, 8),
  (.60, 20),
  (.85, 5),
  (1, 5),
]); // va-talk
const _wave = _Track([
  (0, .12),
  (.24, .85),
  (.42, .3),
  (.60, .85),
  (.85, .12),
  (1, .12),
]); // va-incoming, va-outgoing

const _ponder = _Track([
  (0, -3),
  (.45, -6),
  (.65, -6),
  (1, -3),
]); // va-ponder (4.8 s)
const _glanceX = _Track([
  (0, 0),
  (.35, 4),
  (.65, 4),
  (1, 0),
]); // va-glance (4.8 s)
const _glanceY = _Track([(0, 0), (.35, -3), (.65, -3), (1, 0)]);
const _thought = _Track([
  (0, .25),
  (.30, 1),
  (.70, .25),
  (1, .25),
]); // va-thought

// The SVGs' paths (built once, on first use).
final _lAntennae = _path('m139 77-19-31m81 31 19-31');
final _lHead = _path(
  'M271 124q0-47-47-47H116q-47 0-47 47v80q0 47 47 47h108q47 0 47-47v-17',
);
final _lEyes = _path('M111 150q15-15 30 0 M201 150q15-15 30 0');
final _lMouth = _path('M149 181c4 23 20 23 29 5 9 18 25 18 29-5');
final _lEar = _path(
  'M271 124c12-7 24-4 28 6 5 11 1 23-8 30-7 6-6 13-9 19-2 5-6 8-11 8',
);
final _lCupHand = _path('M319 158c5 25-3 43-23 48');
final _lCupThumb = _path('m307 188-1-13');
final _lWaveNear = _path('M315 117q8 7 9 17');
final _lWaveFar = _path('M326 105q13 11 16 27');

// Thinking: the antennae and head are the speaking ones.
final _tEyes = _path('M137 139v10M225 132v10');
final _tMouth = _path('M164 191q12-4 24 0');
final _tChinHand = _path(
  'M205 258c-2-6 3-11 9-10l9 2 6-18c2-7 13-4 11 4l-4 17c-1 11-10 19-21 18-8-1-13-6-10-13Z',
);
const _tDots = [
  (Offset(278, 62), 4.0),
  (Offset(298, 49), 5.0),
  (Offset(323, 43), 6.0),
];

final _sAntennae = _path('m145 77-19-31m81 31 19-31');
final _sHead = _path(
  'M122 77h108q47 0 47 47v80q0 47-47 47H122q-47 0-47-47v-80q0-47 47-47',
);
final _sEyes = _path('M117 149q15-15 30 0 M205 149q15-15 30 0');
final _sWaveNear = _path('M297 117q8 7 9 17');
final _sWaveMid = _path('M308 105q13 11 16 27');
final _sWaveFar = _path('M319 93q18 15 23 37');

Path _path(String d) {
  final path = Path();
  writeSvgPathDataToPath(d, _PathBuilder(path));
  return path;
}

class _PathBuilder extends PathProxy {
  _PathBuilder(this.path);

  final Path path;

  @override
  void moveTo(double x, double y) => path.moveTo(x, y);

  @override
  void lineTo(double x, double y) => path.lineTo(x, y);

  @override
  void cubicTo(
    double x1,
    double y1,
    double x2,
    double y2,
    double x3,
    double y3,
  ) => path.cubicTo(x1, y1, x2, y2, x3, y3);

  @override
  void close() => path.close();
}
