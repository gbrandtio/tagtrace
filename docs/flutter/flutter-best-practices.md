# Flutter Best Practices

Framework-level guidance for Flutter 3.44. Language rules live in [Dart Best Practices](./dart-best-practices.md). State management lives in [BLoC Best Practices](./bloc-best-practices.md), and how the app is split into layers and packages lives in [Flutter Architecture](./flutter-architecture.md).

---

## Project setup

**Do**
- Create apps with `flutter create --org <reverse-domain> <name>` and pure Dart packages with `dart create -t package <name>`.
- Pin the Flutter version for the whole team (for example with [FVM](https://fvm.app) and its `.fvmrc` file, or a version pinned in the CI configuration), and upgrade it deliberately in its own PR.
- Turn on the analysis options from [Dart › Analysis options](./dart-best-practices.md#analysis-options).
- Pass build-time configuration with `--dart-define-from-file`:

```bash
flutter run --flavor development --dart-define-from-file=config/development.json
```

```dart
// Read once, in the composition root
const apiBaseUrl = String.fromEnvironment('API_BASE_URL');
```

- Use **flavors** (Android product flavors, iOS schemes) when environments need different app IDs, icons, or names, and one `main_<flavor>.dart` per flavor that calls a shared `bootstrap()`.

**Don't**
- Hardcode URLs, feature switches, or keys in widgets. They belong in configuration read at the composition root.
- Commit secrets in `--dart-define` files. Anything compiled into the app can be extracted from it (see [Security](#security)).

---

## Widget composition

**Do**
- Build screens from **small widget classes**, each with one job. A `build` method that doesn't fit on one screen is a sign it should be split.
- Extract parts of a tree into **new widget classes**, not into methods that return `Widget`:

```dart
// ❌ A helper method: rebuilds with the parent, can't be const, has no own BuildContext
Widget _buildHeader() => Text(title, style: Theme.of(context).textTheme.titleLarge);

// ✅ A widget: can be const, rebuilds on its own, shows up in DevTools
class _Header extends StatelessWidget {
  const _Header({required this.title});
  final String title;

  @override
  Widget build(BuildContext context) =>
      Text(title, style: Theme.of(context).textTheme.titleLarge);
}
```

- Mark widget constructors `const` and create widgets with `const` wherever possible.
- Keep **one public widget per file**. Private helper widgets used only by that widget may stay in the same file. (Felix Angelov's rule in the [thesis interview](./bloc-best-practices.md#sources): it forces a clear split of responsibilities.)
- Prefer **composition over inheritance**. Don't extend `ElevatedButton` to make `PrimaryButton`; write a `PrimaryButton` widget that *returns* an `ElevatedButton`.
- Keep `build` **pure and cheap**. It can run every frame. No network calls, no object creation that should live longer than the build, no side effects.

**Don't**
- Put business logic in widgets. Widgets turn state into pixels and user input into events, nothing else ([BLoC › Rules](./bloc-best-practices.md#the-rules-of-the-bloc-pattern)).
- Nest `Container` for single purposes. Use the specific widget: `Padding`, `SizedBox`, `ColoredBox`, `DecoratedBox`, `Align`.

---

## Stateless vs stateful widgets

Use a `StatelessWidget` unless the widget must do one of three things (from the [thesis](./bloc-best-practices.md#sources), section 3.4.2):

1. Hold data that changes during the widget's lifetime and that is purely visual (an animation, a text field controller, a selected tab).
2. Do some setup when it first appears.
3. Clean up when it goes away.

App data that other parts of the app care about belongs in a bloc, not in a `State` object ([Architecture › Where state lives](./flutter-architecture.md#where-state-lives)).

**Do**
- Create long-lived objects (controllers, focus nodes, animation controllers, subscriptions) in `initState` or in a `late final` field initializer (which runs on first use, after `widget` is available), and release them in `dispose`:

```dart
class _SearchFieldState extends State<SearchField> {
  late final TextEditingController _controller = TextEditingController(text: widget.initial);
  final FocusNode _focusNode = FocusNode();

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      TextField(controller: _controller, focusNode: _focusNode);
}
```

- Use `didUpdateWidget` when the parent can pass a new configuration that the state must react to.
- Use `didChangeDependencies` (not `initState`) for work that depends on inherited widgets such as `Theme` or `MediaQuery`.

**Don't**
- Call `setState` after `dispose`. After any `await` inside a `State`, check `if (!mounted) return;`.
- Call `setState` in `build`.

---

## BuildContext

A `BuildContext` is the widget's position in the tree. It is how a widget finds its ancestors (theme, localizations, blocs, navigator).

**Do**
- Read what you need from `context` **before** an `await`, or check `context.mounted` after it:

```dart
// ✅
Future<void> _submit(BuildContext context) async {
  final messenger = ScaffoldMessenger.of(context);
  await context.read<BookingCubit>().submit();
  if (!context.mounted) return;
  Navigator.of(context).pop();
  messenger.showSnackBar(const SnackBar(content: Text('Booked')));
}
```

- Use the specific `MediaQuery` accessors (`MediaQuery.sizeOf(context)`, `MediaQuery.paddingOf(context)`, `MediaQuery.textScalerOf(context)`). They only rebuild your widget when that one value changes.
- Use a `Builder` when you need a context *below* a widget you just created (for example below a new `Scaffold` or `BlocProvider`).

**Don't**
- Store a `BuildContext` in a field, a bloc, or a service.
- Use `context` after an `await` without the `mounted` check. The lint `use_build_context_synchronously` must stay on.

---

## Keys

**Do**
- Give items in lists that can be **reordered, inserted, or removed** a `ValueKey` based on a stable ID: `DishTile(key: ValueKey(dish.id), dish: dish)`. Without it, Flutter may attach the state of one item to another.
- Use a `GlobalKey` only when you really need to reach a widget's state from outside (for example `FormState`), and create it once in a `State`, never in `build`.

**Don't**
- Use `UniqueKey()` in `build`. It forces the subtree to be rebuilt from scratch every time.
- Use the list index as a key for items that can move.

---

## Navigation

**Do**
- Use [`go_router`](https://pub.dev/packages/go_router) for apps with more than a handful of screens. It gives you URL-based routes, deep links, redirects (for login), and nested navigation.
- Define route paths as constants or typed routes in one place per feature, not as string literals scattered through widgets.
- Use `StatefulShellRoute.indexedStack` for bottom navigation, so each tab keeps its own navigation stack and scroll position.
- Use `PopScope` to intercept the back gesture (for example "discard changes?").
- Pass **IDs** in routes, not whole objects. The target screen loads what it needs, which keeps deep links working.

**Don't**
- Navigate from inside a bloc. A bloc emits a state; a `BlocListener` in the UI reacts by navigating ([BLoC › Consuming blocs](./bloc-best-practices.md#consuming-blocs-in-widgets)).
- Use the deprecated `WillPopScope`.

How feature packages contribute their own routes is described in [Architecture › Navigation](./flutter-architecture.md#navigation-across-features).

---

## Theming

**Do**
- Define the look once in `ThemeData` (light and dark), starting from `ColorScheme.fromSeed` or the design system's colour scheme.
- Read colours and text styles from the theme: `Theme.of(context).colorScheme.primary`, `Theme.of(context).textTheme.bodyMedium`.
- Put custom design tokens (spacing, brand colours, radii) in a `ThemeExtension`, so they switch with light and dark mode:

```dart
import 'dart:ui' show lerpDouble;

@immutable
final class AppSpacing extends ThemeExtension<AppSpacing> {
  const AppSpacing({required this.s, required this.m, required this.l});
  final double s, m, l;

  @override
  AppSpacing copyWith({double? s, double? m, double? l}) =>
      AppSpacing(s: s ?? this.s, m: m ?? this.m, l: l ?? this.l);

  @override
  AppSpacing lerp(AppSpacing? other, double t) => other == null
      ? this
      : AppSpacing(
          s: lerpDouble(s, other.s, t)!,
          m: lerpDouble(m, other.m, t)!,
          l: lerpDouble(l, other.l, t)!,
        );
}

// Registration: without this, the `!` below throws
final theme = ThemeData(
  colorScheme: ColorScheme.fromSeed(seedColor: brandSeed),
  extensions: const [AppSpacing(s: 8, m: 16, l: 24)],
);

// Usage
final spacing = Theme.of(context).extension<AppSpacing>()!;
```

**Don't**
- Hardcode colours (`Color(0xFF...)`, `Colors.blue`) or font sizes in feature widgets.
- Use the deprecated `Color.withOpacity`. Use `color.withValues(alpha: 0.5)`.

Design rules (what the theme should contain) are in [UI/UX › Design system](./flutter-ui-ux-guidelines.md#design-system-and-tokens).

---

## Localization

**Do**
- Use Flutter's built-in localization tooling: ARB files plus `flutter gen-l10n`, which generates a typed `AppLocalizations` class. Every user-visible string goes through it.
- Use ICU placeholders, plurals, and selects in ARB files instead of building sentences with `+`:

```json
{
  "dishCount": "{count, plural, =0{No dishes} =1{1 dish} other{{count} dishes}}",
  "@dishCount": { "placeholders": { "count": { "type": "int" } } }
}
```

- Format dates, numbers, and currencies with `package:intl` using the current locale.
- Hold the user's chosen locale in a small cubit and pass it to `MaterialApp.locale`. Don't push translated strings through blocs ([BLoC › Localization](./bloc-best-practices.md#localization)).

**Don't**
- Hardcode user-visible strings, even "temporarily".
- Concatenate translated fragments. Word order differs between languages.

---

## Assets and fonts

**Do**
- Declare assets per folder in `pubspec.yaml`, and provide `2.0x` / `3.0x` variants for raster images.
- Prefer vector assets (icons, SVG via `flutter_svg`, or an icon font) for simple graphics.
- Bundle fonts the app needs offline instead of downloading them at runtime.

**Don't**
- Ship images much larger than they are displayed. Resize them at build time, or decode them smaller ([Performance › Images](./flutter-performance.md#images)).

---

## Networking and serialization

**Do**
- Keep HTTP calls in the **data layer** (API clients used by repositories). Widgets and blocs never import `http` or `dio`.
- Map JSON to typed **data transfer objects** with `json_serializable` or `freezed`, then map those to domain models in the repository. The rest of the app never sees raw JSON.
- Set timeouts, and translate transport errors into the typed exceptions of [Dart › Error handling](./dart-best-practices.md#error-handling).
- Parse large responses in an isolate ([Dart › Isolates](./dart-best-practices.md#isolates)).

**Don't**
- Pass `Map<String, dynamic>` beyond the API client.
- Create a new HTTP client per request. Share one per API, created at the composition root.

---

## Platform integration

**Do**
- Check pub.dev for a maintained plugin before writing native code.
- When you do write native code, generate the channel code with [`pigeon`](https://pub.dev/packages/pigeon). It gives you type-safe calls on both sides instead of hand-written string method names.
- Hide the plugin behind your own interface in the data layer, so the rest of the app can be tested without a device.
- Branch on platform in **one** place (a bloc state or an injected service), not with `Platform.isIOS` scattered through widgets. (Rule 4 for widgets in the [BLoC rules](./bloc-best-practices.md#the-rules-of-the-bloc-pattern).)

**Don't**
- Import `dart:io` in code that must also run on the web. Use `defaultTargetPlatform` or `kIsWeb` for UI decisions.

---

## Error handling and logging

**Do**
- Install global handlers in `bootstrap()` and send errors to your crash reporter:

```dart
Future<void> bootstrap(Widget Function() builder) async {
  FlutterError.onError = (details) {
    FlutterError.presentError(details);
    crashReporter.record(details.exception, details.stack);
  };
  PlatformDispatcher.instance.onError = (error, stack) {
    crashReporter.record(error, stack);
    return true;
  };
  Bloc.observer = const AppBlocObserver();
  runApp(builder());
}
```

- Use one logging package (for example `package:logging`) with levels, and turn verbose logging off in release builds.
- Show a friendly error state for expected failures; let unexpected ones reach the global handler.

**Don't**
- Use `print`. Use the logger, or `debugPrint` for throwaway debugging that you remove before merging.
- Log personal data, tokens, or full request bodies.

---

## Testing

| Test type | What it checks | Tools | Speed |
|-----------|----------------|-------|-------|
| Unit | Blocs, repositories, models, pure logic | `test`, `bloc_test`, `mocktail` | Milliseconds |
| Widget | One widget or screen with fake dependencies | `flutter_test`: `testWidgets`, `pumpWidget`, finders | Fast, no device |
| Golden | Pixels of a widget against a stored image | `matchesGoldenFile` | Fast; run on one fixed platform in CI |
| Integration | Whole app on a device or emulator | `integration_test` | Slow; keep few |

**Do**
- Test blocs with `blocTest` ([BLoC › Testing](./bloc-best-practices.md#testing-blocs)) and widgets with a fake or mocked bloc injected through `BlocProvider.value`.
- Find widgets by `Key`, text, or semantics label, not by position in the tree.
- Use `pumpAndSettle` only when animations must finish; otherwise `pump()` with a duration is faster and won't hang on endless animations.
- Wrap widgets under test in the same `MaterialApp`, theme, and localization delegates as the real app, using a shared `pumpApp` helper.

**Don't**
- Hit the network in tests. Inject fakes at the repository boundary.
- Run golden tests on several operating systems; font rendering differs and the tests become flaky.

---

## Security

**Do**
- Treat the app binary as **public**. API keys, signing secrets, and admin endpoints must not be in it. Use a backend for anything that needs a real secret.
- Store tokens with `flutter_secure_storage` (Keychain / Keystore), not in `SharedPreferences`.
- Build release apps with `--obfuscate --split-debug-info=<dir>` and keep the symbol files to decode crash reports.
- Use HTTPS only, and consider certificate pinning for high-risk APIs (with a plan for rotating certificates).
- Validate deep link parameters like any other untrusted input.

**Don't**
- Log tokens or personal data, or put them in analytics events.
- Rely on obfuscation for security. It slows reverse engineering; it does not prevent it.

---

## Dependencies

**Do**
- Prefer packages that are maintained, have a high pub.dev score, and are published by a verified publisher.
- Commit `pubspec.lock` for apps. For packages, don't commit it, but test against the lowest allowed versions in CI (`flutter pub downgrade`).
- Run `flutter pub outdated` regularly, and upgrade in small, separate PRs.

**Don't**
- Add a package for something that is a few lines of code.
- Depend on a package from a Git branch in production code.

---

## Review checklist

- [ ] No business logic or I/O in widgets; `build` is pure.
- [ ] UI is split into small widget classes, not `Widget _buildX()` methods; `const` used where possible.
- [ ] Controllers, focus nodes, and subscriptions are disposed.
- [ ] No `context` use after `await` without a `mounted` check.
- [ ] List items that can move have stable `ValueKey`s.
- [ ] Navigation happens in `BlocListener`s or callbacks, not in blocs.
- [ ] No hardcoded colours, text styles, or user-visible strings.
- [ ] Network and platform code sits behind repository interfaces.
- [ ] No secrets compiled into the app; tokens in secure storage.
- [ ] Tests cover the change at the cheapest level that proves it works.
