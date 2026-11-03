# Flutter Performance

How to keep Flutter apps smooth, fast to start, and small. The first half covers everyday rules. The second half, [Advanced topics](#advanced-topics), covers what you need when the everyday rules are not enough.

The rule above all others: **measure first, in profile mode, on a real low-end device.** Debug mode is many times slower than release and tells you nothing about real performance.

---

## How a frame is made

Knowing where time goes makes the rest of the page easier to follow.

| Phase | Runs on | What happens | Typical cause of slowness |
|-------|---------|--------------|---------------------------|
| **Build** | UI thread | `build` methods produce the widget tree | Rebuilding too much, expensive work in `build` |
| **Layout** | UI thread | Render objects measure and position themselves | Intrinsic sizing, deep nesting, `shrinkWrap` lists |
| **Paint** | UI thread | Render objects record drawing commands into layers | Repainting large areas every frame |
| **Raster** | Raster thread | The engine (Impeller) turns layers into GPU work | Offscreen layers (`Opacity`, clips, blur), huge images |

At 60 Hz each frame has **16 ms**; at 120 Hz it has **8 ms**. Both the UI thread and the raster thread must finish within that budget. A frame that misses it is dropped, and the user sees *jank*.

---

## Measuring

**Do**
- Run with `flutter run --profile` on a physical device, ideally the slowest one you support.
- Use **Flutter DevTools**:
  - *Performance* view: frame chart (UI vs raster time per frame), timeline events, and "Track widget builds" to see what rebuilds.
  - *CPU profiler*: which Dart functions take the time.
  - *Memory* view: heap size, allocations, leaks.
  - *Widget rebuild stats* in the inspector: how often each widget rebuilds.
- Turn on the performance overlay (`MaterialApp(showPerformanceOverlay: true)` or `P` in the terminal) for a quick look at both threads.
- Add your own timeline events around suspicious code:

```dart
import 'dart:developer';

final dishes = Timeline.timeSync('parseDishes', () => parseDishes(body));
```

- Write the result of an investigation into the PR: device, before, after.

**Don't**
- Judge performance in debug mode or on a simulator.
- Optimise code you haven't measured. Most code is not on the hot path.

---

## Reduce rebuild cost

Rebuilds are cheap one at a time, but rebuilding a large subtree every frame adds up.

**Do**
- Use `const` constructors. A `const` widget is created once and Flutter skips rebuilding it.
- **Push state down.** Put the changing part in its own small widget so only that widget rebuilds:

```dart
// ❌ The whole page rebuilds on every tick of the timer
class _PageState extends State<Page> {
  int _seconds = 0;
  // ...
  @override
  Widget build(BuildContext context) =>
      Column(children: [const HugeHeader(), Text('$_seconds'), const HugeList()]);
}

// ✅ Only the small timer widget owns and rebuilds the changing value
Widget build(BuildContext context) =>
    const Column(children: [HugeHeader(), ElapsedSeconds(), HugeList()]);
```

- Rebuild only on the state you use: `context.select`, `BlocSelector`, and `buildWhen` on `BlocBuilder` ([BLoC › Consuming blocs](./bloc-best-practices.md#consuming-blocs-in-widgets)).
- Pass the static part of an animated tree as `child` to `AnimatedBuilder`, `ValueListenableBuilder`, or `ListenableBuilder`, so it is built once:

```dart
AnimatedBuilder(
  animation: _controller,
  child: const DishImage(), // built once
  builder: (context, child) =>
      Transform.rotate(angle: _controller.value * 2 * pi, child: child),
);
```

- Use `ValueNotifier` + `ValueListenableBuilder` for a single, local, purely visual value.

**Don't**
- Call `setState` on a high-level widget for a change that only affects a leaf.
- Create objects in `build` that could be created once (regular expressions, formatters, controllers, large lists).

---

## Lists and scrolling

**Do**
- Use the **builder** constructors (`ListView.builder`, `GridView.builder`, `SliverList.builder`) for anything longer than a screen. They build only the visible items.
- Give items a fixed size when you can: `itemExtent` or `prototypeItem`. Then the list can jump and calculate scroll positions without laying out every item.
- Combine different scrolling content (header, grid, list) in one `CustomScrollView` with slivers, instead of nesting scroll views.
- Paginate long data sets; load the next page near the end of the list.

**Don't**
- Use `shrinkWrap: true` on long lists, or put a `ListView` inside a `Column` inside a `SingleChildScrollView`. It lays out every item, even the invisible ones.
- Use `ListView(children: [...])` for long or unbounded lists.

---

## Layout cost

**Do**
- Prefer widgets with simple, one-pass layout: `Row`, `Column`, `Stack`, `Padding`, `SizedBox`, `Align`.
- Use `LayoutBuilder` when a widget needs to adapt to the space it is given, and `MediaQuery.sizeOf` when it needs the screen size.

**Don't**
- Use `IntrinsicHeight` / `IntrinsicWidth` in lists or other hot paths. They lay out children twice, and nested intrinsics can grow much worse.
- Nest many `Flexible`/`Expanded` levels where one `Row` with fixed sizes would do.

---

## Paint and raster cost

Some effects force the engine to draw into an **offscreen buffer** first and then blend it in. That is expensive, especially when it happens every frame.

**Do**
- Fade with `FadeTransition` or `AnimatedOpacity`, not by animating an `Opacity` widget's value in `setState`.
- For a single coloured shape or image, apply transparency to the colour (`color.withValues(alpha: 0.5)`) or use `Image(opacity: const AlwaysStoppedAnimation(0.5))` instead of wrapping it in `Opacity`.
- Wrap a part that repaints often (an animation, a video, a progress indicator) in a `RepaintBoundary`, so the rest of the screen doesn't repaint with it. Check with `debugRepaintRainbowEnabled = true`: areas that change colour every frame are repainting.
- In a `CustomPainter`, make `shouldRepaint` return `true` only when the inputs really changed.
- Prefer `Clip.hardEdge` (or no clip) over `Clip.antiAliasWithSaveLayer`.

**Don't**
- Use `BackdropFilter`, `ShaderMask`, or `ColorFilter` over large or scrolling areas without measuring.
- Add `RepaintBoundary` everywhere. Each one costs memory; add it where the repaint rainbow shows a benefit.

---

## Images

**Do**
- Decode images at the size they are displayed. A 4000×3000 photo shown as a 100×100 thumbnail still costs 48 MB of memory when decoded at full size:

```dart
// ✅ Decodes at about display size
Image.network(
  dish.imageUrl,
  cacheWidth: (100 * MediaQuery.devicePixelRatioOf(context)).round(),
);
```

- Use `precacheImage` for images that must appear instantly on the next screen.
- Use a disk cache (for example `cached_network_image`) for network images that are shown repeatedly.
- Serve modern, compressed formats (WebP) from the backend, at sizes close to what the app needs.

**Don't**
- Embed images as base64 strings in JSON or models. They cost memory twice and can't be cached by the image cache.

---

## Animations

**Do**
- Prefer **implicit animations** (`AnimatedContainer`, `AnimatedOpacity`, `AnimatedSwitcher`) for simple transitions; they manage their own controller.
- For explicit animations, create the `AnimationController` with `vsync: this` (using `SingleTickerProviderStateMixin`) and dispose it.
- Animate **transforms and opacity** (`Transform`, `FadeTransition`, `SlideTransition`, `ScaleTransition`). They skip layout. Animating `width`, `height`, or padding relayouts every frame.
- Respect the system setting for reduced motion ([UI/UX › Motion](./flutter-ui-ux-guidelines.md#motion)).

**Don't**
- Drive animations with `Timer` + `setState`.
- Leave animations running on screens that are not visible. `TickerMode` pauses tickers in hidden subtrees (the navigator and `IndexedStack`-based shells do this for you).

---

## Isolates and heavy work

The UI thread runs your Dart code. Anything that takes longer than a few milliseconds there costs frames.

**Do**
- Move heavy synchronous work to another isolate with `Isolate.run` ([Dart › Isolates](./dart-best-practices.md#isolates)). Typical candidates: decoding JSON larger than about 100 KB, parsing files, crypto, image manipulation, search over large in-memory lists.
- Confirm with the timeline: the work should disappear from the UI thread track.
- For work that must stay on the main isolate, split it into chunks and yield between them (`await Future<void>.delayed(Duration.zero)`), so frames can render in between.

**Don't**
- Send huge object graphs between isolates on every frame. Copying has a cost; send IDs or compact data.

---

## Startup time

**Do**
- Measure with `flutter run --profile --trace-startup`, which writes the time to first frame into `build/start_up_info.json`.
- Show the first frame as early as possible. Initialise only what the first screen needs before `runApp`, and start the rest after.
- Initialise plugins and SDKs (analytics, crash reporting, remote config) lazily or in parallel with `Future.wait`.
- Keep the native splash screen simple (`flutter_native_splash`) and don't add an extra Dart splash screen with a fixed delay.

**Don't**
- `await` network calls before `runApp`.
- Load large assets (JSON, databases) synchronously during startup.

---

## App size

**Do**
- Inspect what takes space: `flutter build apk --analyze-size --target-platform android-arm64` (or `appbundle`, `ios`), then open the generated file in DevTools' *App size* tool.
- Build release apps with `--split-debug-info=<dir> --obfuscate`. It removes debug symbols from the binary.
- Ship Android App Bundles, so the store delivers only the code for the user's device.
- Compress and resize assets; remove unused ones and unused packages.
- Use deferred imports (`import '...' deferred as feature;`) for rarely used, large features on web and Android (deferred components).

**Don't**
- Bundle every icon font weight or every language of a large font when you use a few.

---

## Memory

**Do**
- Dispose everything you create: controllers, streams, subscriptions, blocs created outside `BlocProvider`, `ImageStream` listeners.
- Use the DevTools *Memory* view to take heap snapshots before and after opening and closing a screen several times. Objects from the closed screen should be gone.
- Run widget tests with the leak tracker (`package:leak_tracker_flutter_testing`) to catch undisposed objects early.
- Lower `PaintingBinding.instance.imageCache.maximumSizeBytes` on memory-constrained devices if images dominate memory.

**Don't**
- Keep references to `BuildContext`, `State`, or widgets in long-lived objects (singletons, blocs, static fields). That keeps the whole screen in memory.

---

## Advanced topics

### Impeller and shader jank

Impeller is the default renderer on iOS, and on Android devices that support Vulkan. Older Android devices without Vulkan fall back to an OpenGL backend, so test on one if you support them. Impeller's shaders are precompiled when the Flutter engine is built, not when they are first used, so the "first-run jank" caused by shader compilation under the old Skia renderer is gone. Two consequences:

- Don't spend time on SkSL shader warm-up for Impeller targets; it doesn't apply.
- If you still see raster jank, look at offscreen layers, blur, clipping, and image size, using the raster thread track in DevTools.

### Fragment shaders

For effects that would otherwise need many layers (gradients that animate, noise, ripples), write a GLSL fragment shader, declare it under `flutter: shaders:` in `pubspec.yaml`, and load it with `FragmentProgram.fromAsset`. One shader pass is usually cheaper than a stack of `ShaderMask` and `BackdropFilter` widgets.

### Custom render objects

When a layout can't be expressed efficiently with existing widgets (a chart, a timeline, a custom flow), write a `RenderBox` with `LeafRenderObjectWidget` or `MultiChildRenderObjectWidget`, or use `CustomPaint` / `Flow`. Rules:

- Call `markNeedsLayout` only when layout inputs change, and `markNeedsPaint` when only appearance changes.
- Set `isRepaintBoundary` to `true` on render objects that repaint independently.
- Implement `computeDryLayout` so parents can ask for your size without laying you out.

### Very large lists and tables

- Use `SliverFixedExtentList` or `SliverVariedExtentList` when item heights are known up front; the list can then jump to any position without measuring items.
- Use the `two_dimensional_scrollables` package (`TableView`) for grids of up to a few thousand rows. It builds only the visible cells in both directions.
- For logs with very many rows, use a vertical `ListView.builder` with a fixed `itemExtent`, placed inside a horizontal `SingleChildScrollView`, and keep the column header above the list. `TableView` walks every row when the table rebuilds or resizes. On a million-row table, that costs about a second per selection change. A fixed-extent list only builds the rows on screen.
- Keep item widgets cheap: no `IntrinsicHeight`, no images decoded at full size, no per-item blocs unless the item really has independent logic.

### Platform views

Embedding native views (maps, web views, native ads) has a real cost: the engine must compose native and Flutter content together, and on Android the composition mode affects both performance and correctness. Use them only where there is no Flutter alternative, avoid placing them in scrolling lists, and measure on the oldest Android version you support.

### Web

- Build with `flutter build web --wasm` where the target browsers support WebAssembly garbage collection; it falls back to JavaScript automatically elsewhere.
- Initial download size matters most on the web. Use deferred imports for large features and analyse the bundle.
- Text-heavy, document-like pages with SEO needs are a poor fit for Flutter web; render those with a web framework.

### Native code through FFI

For CPU-bound work that already exists as a C/C++/Rust library (codecs, compression, cryptography), call it through `dart:ffi`. It avoids the overhead of platform channels. Run long FFI calls in a background isolate, since a blocking native call blocks the isolate that made it.

### Keeping widgets alive vs not building them

| Technique | Builds hidden widget | Keeps its state | Cost |
|-----------|----------------------|-----------------|------|
| Don't include it in the tree | No | No | None |
| `Offstage` / `Visibility(maintainState: true)` | Yes | Yes | Memory, and build/layout when its inputs change |
| `IndexedStack` | Yes (all children) | Yes | All tabs in memory at once |
| `AutomaticKeepAliveClientMixin` in lists | Yes (kept items) | Yes | Memory per kept item |

Keep things alive only when rebuilding them is noticeably slow or loses state the user cares about (scroll position, form input).

### Performance tests in CI

Catch regressions automatically with an integration test that records frame timings:

```dart
// integration_test/scroll_perf_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('menu scrolls smoothly', (tester) async {
    // App as in BLoC › Dependency injection, with fakes so the network doesn't skew timings
    await tester.pumpWidget(App(
      dishRepository: FakeDishRepository(),
      orderRepository: InMemoryOrderRepository(),
    ));
    await binding.watchPerformance(() async {
      await tester.fling(find.byType(Scrollable).first, const Offset(0, -3000), 5000);
      await tester.pumpAndSettle();
    }, reportKey: 'menu_scroll');
  });
}
```

Run it with `flutter drive --profile --driver=test_driver/perf_driver.dart --target=integration_test/scroll_perf_test.dart` on a fixed device, store the summary, and fail the build when frame times get worse beyond a set threshold.

---

## Review checklist

- [ ] Performance claims are backed by profile-mode measurements on a real device.
- [ ] `const` is used where possible; changing state lives in small widgets.
- [ ] Blocs are consumed with `select` / `BlocSelector` / `buildWhen` where only part of the state matters.
- [ ] Long lists use builder constructors, without `shrinkWrap`; fixed item sizes are declared when known.
- [ ] No `IntrinsicHeight`/`IntrinsicWidth` in lists; no animated `Opacity`; blur and masks are measured.
- [ ] Images are decoded near their display size.
- [ ] Animations use transforms or opacity transitions and dispose their controllers.
- [ ] Heavy synchronous work runs in an isolate.
- [ ] Nothing slow is awaited before `runApp`.
- [ ] No long-lived references to `BuildContext`, `State`, or widgets.
