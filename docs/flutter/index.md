# Flutter Engineering Guidelines

These pages describe how we write, structure, and review Dart and Flutter code in this repository. They are a **shared baseline**, not a rulebook to be applied blindly: when a rule doesn't fit, deviate deliberately and write down why (in the PR, or in an [ADR](../adr) for significant decisions).

**Baseline:** Flutter 3.44 (stable) · Dart 3.12 · sound null safety · Material 3 · `flutter_bloc` for state management.

## Contents

| Page | What it covers |
|------|----------------|
| [Dart Best Practices](./dart-best-practices.md) | Language-level guidance: naming, imports, types, null safety, immutability and equality, records, patterns, sealed classes, class modifiers, extensions, collections, errors, async, isolates, documentation, testing. |
| [Flutter Best Practices](./flutter-best-practices.md) | Framework-level guidance: project setup, widget composition, widget lifecycle, `BuildContext`, keys, navigation, theming, localization, networking, platform integration, error handling, testing, security, dependencies. |
| [Flutter Performance](./flutter-performance.md) | Measuring in profile mode, rebuild cost, lists and scrolling, layout and paint cost, images, animations, isolates, startup, app size, memory, and advanced topics (Impeller, custom render objects, platform views, web, FFI, performance tests in CI). |
| [UI/UX Guidelines](./flutter-ui-ux-guidelines.md) | Design system and tokens, platform conventions, responsive and adaptive layout, touch targets, text scaling, colour and contrast, accessibility, loading/empty/error states, forms, motion, navigation, right-to-left and localized content. |
| [BLoC Best Practices](./bloc-best-practices.md) | The BLoC pattern and the `bloc` package: Bloc vs Cubit, designing and splitting blocs, events, states, concurrency, providing and consuming blocs, dependency injection, forms, localization, testing, and how to structure the application, based on the large-scale reference app thesis. |
| [Flutter Architecture](./flutter-architecture.md) | Separation of concerns, the layers and the dependency rule, where state lives, cross-feature communication, feature modules ("micro frontends") in a monorepo, composition root, navigation, error flow, enforcing boundaries, testing per layer. |

## Suggested reading order

1. **New to Dart or Flutter:** Dart → Flutter → BLoC → Architecture.
2. **Starting a new feature or app:** Architecture → BLoC → UI/UX.
3. **Working on jank, slow startup, or a large app binary:** Performance.
4. **Building a screen:** UI/UX → Flutter → Performance.
5. **Reviewing a PR:** jump straight to the *Review checklist* at the end of each page.

## How the pages are organised

Every page follows the same shape as the [.NET guidelines](../dotnet/index.md):

- **Do / Don't** bullets: the rule in one line.
- **Examples**: `❌` marks code to avoid, `✅` marks the preferred alternative.
- **Why** (where the reasoning isn't obvious from the rule): so you can judge edge cases yourself.
- **Review checklist**: a short list to use during code review.

The code samples leave out doc comments so they stay short. Real code must document every public class and member with `///` comments, as described in [Dart › Documentation comments](./dart-best-practices.md#documentation-comments).

General principles that are not specific to Flutter (SOLID, coupling and cohesion, ADRs) live in the .NET pages and are linked rather than repeated. For example, the *principle* of dependency inversion lives in [SOLID](../dotnet/solid-principles.md#dependency-inversion-principle-dip), the *Flutter mechanics* of injecting repositories live in [BLoC › Dependency injection](./bloc-best-practices.md#dependency-injection), and *how packages are wired* lives in [Architecture › Composition root](./flutter-architecture.md#composition-root).

## Citing a rule in a review

Headings are stable, so link to them directly:

> See [Flutter › BuildContext](./flutter-best-practices.md#buildcontext): this uses `context` after an `await` without checking `context.mounted`.

If a guideline is outdated or wrong, change it in a PR like any other code. Guidelines that nobody follows should be fixed or removed, not ignored.

## Precedence

When sources disagree, follow this order:

1. Analyzer diagnostics and lints enforced by the build (`analysis_options.yaml`).
2. Accepted ADRs.
3. These guidelines.
4. Personal preference.
