# Flutter UI/UX Guidelines

How our screens should look, behave, and feel, and how to build that in Flutter. The goal is an interface that is **clear, consistent, accessible, and forgiving**. Code-level theming mechanics are in [Flutter › Theming](./flutter-best-practices.md#theming); this page covers what goes into the design.

---

## Principles

| Principle | What it means in practice |
|-----------|---------------------------|
| **Clarity** | Each screen has one main purpose. The most important action is the most visible one. |
| **Consistency** | The same thing looks and behaves the same everywhere. Use the design system, not one-off styles. |
| **Feedback** | Every action gets a visible response within 100 ms: a pressed state, a spinner, a message. |
| **Forgiveness** | Destructive actions can be undone or are confirmed. Errors explain how to fix them. Input is never lost. |
| **Accessibility** | Usable with a screen reader, with large text, with a keyboard, and without relying on colour. |
| **Respect for the platform** | Navigation, gestures, and system dialogs behave the way users of that platform expect. |

---

## Design system and tokens

**Do**
- Use **Material 3** as the base component set, themed to our brand. Build a shared `ui_kit` package for our own components ([Architecture › Feature modules](./flutter-architecture.md#feature-modules-micro-frontends)).
- Define **design tokens** once, and use only tokens in feature code:

  | Token group | Example values | Lives in |
  |-------------|----------------|----------|
  | Colour roles | `primary`, `onPrimary`, `surface`, `error`, `outline` | `ColorScheme` |
  | Typography | `displayLarge` … `labelSmall` | `TextTheme` |
  | Spacing | 4, 8, 12, 16, 24, 32, 48 | `ThemeExtension` |
  | Radii | 4, 8, 16, full | `ThemeExtension` / component themes |
  | Elevation | 0–5 levels | Component themes |
  | Motion | Short (100–200 ms), medium (250–400 ms), long (450–600 ms) | Constants in `ui_kit` |

- Use a **4/8 dp spacing grid**. Spacing between related items is smaller than spacing between groups.
- Use colour **roles**, not raw colours: a button uses `colorScheme.primary`, never "brand blue". This makes dark mode and high-contrast themes work automatically.
- Provide both a **light and a dark theme**, and follow the system setting by default (`ThemeMode.system`).

**Don't**
- Introduce a new colour, font size, or spacing value in a feature. Add a token to the design system if it's really needed.
- Style components individually where a component theme (`FilledButtonThemeData`, `InputDecorationTheme`, `CardThemeData`) can do it once.

---

## Platform conventions

Flutter draws every pixel itself, so it does not get platform behaviour for free in all cases. Decide per app how native it should feel, and be consistent.

**Do**
- Keep platform-specific behaviour that users rely on:
  - Back navigation: Android system back and predictive back; iOS swipe-from-edge to go back.
  - Scroll physics: Flutter already uses bouncing on iOS and clamping/stretch on Android.
  - Dialogs, date pickers, and switches: use the `.adaptive` constructors where they exist (`Switch.adaptive`, `Slider.adaptive`, `CircularProgressIndicator.adaptive`, `AlertDialog.adaptive`).
  - Text selection, copy/paste, and keyboard shortcuts on desktop and web.
- Use platform-appropriate icons where meaning differs (share, back arrow).
- On desktop and web: support mouse hover states, right-click where useful, keyboard navigation, and resizable windows.

**Don't**
- Build a fully custom look that ignores both Material and Cupertino conventions without a design reason.
- Disable the system back gesture to "keep users in the flow".

---

## Responsive and adaptive layout

*Responsive* means the layout fits the available space. *Adaptive* means the app changes how it works for the device (input type, navigation pattern).

**Do**
- Design for these width classes (Material window size classes) and test at least the first three:

  | Class | Width | Typical navigation | Typical layout |
  |-------|-------|--------------------|----------------|
  | Compact | < 600 dp | `NavigationBar` (bottom) | Single column |
  | Medium | 600–839 dp | `NavigationRail` | One or two panes |
  | Expanded | 840–1199 dp | `NavigationRail` or drawer | List–detail side by side |
  | Large / extra-large | ≥ 1200 dp | Permanent navigation drawer | Multi-pane, max content width |

- Decide layout from the **space the widget gets** (`LayoutBuilder`), and screen-level structure from the window size (`MediaQuery.sizeOf`). Don't decide by device type ("is tablet").
- Limit reading width to about 600–700 dp for text-heavy content; centre it on wide screens.
- Wrap content in `SafeArea` (or use `Scaffold`, which handles most insets) so it doesn't sit under notches, the status bar, or the home indicator.
- Support both orientations unless there is a real reason not to, and keep state when the device rotates or the window resizes.

```dart
// Screen level: the window size decides the structure.
class MenuLayout extends StatelessWidget {
  const MenuLayout({super.key});

  @override
  Widget build(BuildContext context) => MediaQuery.sizeOf(context).width >= 840
      ? const Row(children: [Expanded(child: DishList()), Expanded(child: DishDetail())])
      : const DishList();
}

// Pane level: the space this widget actually gets decides the layout.
class DishGrid extends StatelessWidget {
  const DishGrid({required this.dishes, super.key});
  final List<Dish> dishes;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, constraints) => GridView.builder(
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: (constraints.maxWidth / 240).floor().clamp(1, 6),
          ),
          itemCount: dishes.length,
          itemBuilder: (context, i) => DishTile(dish: dishes[i]),
        ),
      );
}
```

**Don't**
- Use fixed pixel widths and heights for containers that hold text or that should grow.
- Check `Platform.isAndroid` to decide layout. Platform and screen size are different questions.

---

## Touch targets and spacing

**Do**
- Make every interactive element at least **48×48 dp** (Material). iOS asks for at least 44×44 pt. Material buttons already meet 48 dp through `materialTapTargetSize`; custom `GestureDetector`s don't.
- Leave at least 8 dp between adjacent targets.
- Put primary actions where thumbs reach on phones (bottom half of the screen).

**Don't**
- Make small icons tappable without padding around them. Use `IconButton`, which provides the target size and a tooltip.

---

## Typography and text scaling

**Do**
- Use the `TextTheme` roles (`headlineSmall`, `titleMedium`, `bodyLarge`, `labelLarge`) so hierarchy is consistent.
- Bundle the UI font and a monospace font for log text and code, with their licence files, so text looks the same offline.
- Support the user's text size setting. Test every screen at **200 % text scale**. Text must wrap or scroll, not be cut off.
- Let text containers grow: use `minHeight` constraints instead of fixed heights, and allow wrapping instead of `maxLines: 1` for important content.
- Use `overflow: TextOverflow.ellipsis` only for secondary text where the full value is available elsewhere.

**Don't**
- Clamp or disable text scaling app-wide. If one layout really can't handle large text, fix that layout.
- Put text in images.

---

## Colour and contrast

**Do**
- Meet WCAG 2.2 AA contrast:
  - **4.5 : 1** for normal text.
  - **3 : 1** for large text (about 18 pt regular or 14 pt bold and above) and for UI parts such as input borders, icons, and focus indicators.
- Check both light and dark themes.
- Use `colorScheme.outline` for control borders. It must reach 3 : 1 against the surface. Use `colorScheme.outlineVariant` for dividers.
- Pair colour with another signal: an icon, a label, or a pattern. An error is red **and** has an error icon and text.

**Don't**
- Use colour alone to show status, selection, or validation.
- Put text on photos without a scrim or a solid background behind it.

---

## Accessibility

**Do**
- Give every meaningful non-text element a label. `IconButton(tooltip: ...)` provides one; for custom widgets use `Semantics(label: ..., button: true)`.
- Mark decorative images as excluded: `Image.asset(..., excludeFromSemantics: true)`.
- Merge related pieces into one announcement with `MergeSemantics` (for example a list tile with title, subtitle, and price).
- Keep a logical focus order; support keyboard and switch access (`Focus`, `FocusTraversalGroup`, `Shortcuts`, `Actions`). Every interactive element must show a visible focus state.
- Announce important changes that aren't otherwise visible to screen readers (for example "Order placed") using `SemanticsService.sendAnnouncement(View.of(context), 'Order placed', Directionality.of(context))` or a live region (`Semantics(liveRegion: true)`). `SemanticsService` comes from `package:flutter/semantics.dart`, which `material.dart` doesn't export. The older `SemanticsService.announce` is deprecated.
- Test with TalkBack (Android) and VoiceOver (iOS/macOS) before release.
- Check guidelines automatically in widget tests:

```dart
testWidgets('menu page meets accessibility guidelines', (tester) async {
  // Dispose in `finally`, not addTearDown: flutter_test checks for open handles before tear-downs run
  final handle = tester.ensureSemantics();
  try {
    await tester.pumpApp(const MenuPage());

    await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
    await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
    await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    await expectLater(tester, meetsGuideline(textContrastGuideline));
  } finally {
    handle.dispose();
  }
});
```

**Don't**
- Rely on gestures alone (swipe to delete, long press) for an action. Offer a visible alternative such as a menu item.
- Use time limits that the user can't extend.

---

## Loading, empty, error, and success states

Every screen that loads data has at least four states. Design all of them, not only the happy path. A bloc's states map directly onto them ([BLoC › States](./bloc-best-practices.md#states)).

| State | Show | Avoid |
|-------|------|-------|
| **Loading** | Skeleton placeholders shaped like the content for content screens; a small progress indicator for short actions. Delay showing it by about 200–300 ms so fast responses don't flicker. | Full-screen spinners that hide content the user already has. |
| **Empty** | What would be here, why it's empty, and the action that fills it ("No bookings yet. Book a table"). | A blank screen. |
| **Error** | What went wrong in plain words, and a way forward: **Retry**, edit the input, or contact support. Keep data that's already loaded visible. | Raw exception text, error codes without explanation, dead ends. |
| **Success** | Confirmation that matches the weight of the action: a snackbar for small actions, a dedicated screen for big ones (a booking with its token). | Silent success, where the user can't tell whether it worked. |

**Do**
- Show **optimistic updates** for quick, likely-to-succeed actions (favourites, toggles), and roll back with a message if the server rejects them.
- Disable a submit button while its request runs, and show progress on the button itself.
- Use snackbars with an **Undo** action instead of confirmation dialogs for reversible actions.

**Don't**
- Block the whole UI with a modal dialog for background work.
- Lose scroll position or form input when a refresh fails.

---

## Forms and input

**Do**
- Set the right keyboard and actions: `keyboardType: TextInputType.emailAddress`, `textInputAction: TextInputAction.next`, and `onFieldSubmitted` to move focus.
- Enable autofill with `autofillHints` (`AutofillHints.email`, `AutofillHints.newPassword`) inside an `AutofillGroup`.
- Keep labels visible (floating `labelText`), not placeholder-only fields.
- Validate when the user leaves a field or submits, then re-validate **while typing** once a field has shown an error, so the error clears as soon as it's fixed.
- Write error messages that say how to fix the problem: "Enter an email like name@example.com", not "Invalid input".
- Keep the focused field visible above the keyboard (`Scaffold` does this; check custom layouts).
- Put validation rules in the bloc/cubit, not in the widget ([BLoC › Forms](./bloc-best-practices.md#forms-and-validation)).

**Don't**
- Clear the user's input when validation fails.
- Disable the submit button without explaining what's missing. Either keep it enabled and show errors on press, or show what's needed next to it.

---

## Motion

**Do**
- Use motion to explain: where something came from (`Hero`, container transforms), what changed (`AnimatedSwitcher`), and what is interactive (pressed states).
- Keep durations short: 100–200 ms for small elements and state changes, 250–400 ms for screen transitions. Use standard easing curves (`Curves.easeInOutCubicEmphasized` or the `ui_kit` motion tokens).
- Respect reduced motion. When `MediaQuery.disableAnimationsOf(context)` is `true`, replace movement with instant changes or simple fades.

**Don't**
- Animate for decoration on every screen.
- Use animations that flash more than three times per second.

---

## Navigation UX

**Do**
- Make the current location obvious: the selected tab, the page title, breadcrumbs on wide layouts.
- Keep each tab's stack and scroll position when switching tabs.
- Make every important screen reachable by a **deep link**, and make back from a deep link go somewhere sensible (the parent screen, not out of the app).
- Ask before discarding unsaved changes on back.

**Don't**
- Use more than five destinations in a bottom navigation bar.
- Hide primary navigation behind a hamburger menu on phones when there are only a few destinations.

---

## Localized and right-to-left content

**Do**
- Leave room for longer text: German and Finnish strings are often 30–40 % longer than English.
- Use direction-aware widgets so layouts mirror in right-to-left languages: `EdgeInsetsDirectional`, `AlignmentDirectional`, `PositionedDirectional`, and `start`/`end` instead of `left`/`right`.
- Mirror directional icons (back, forward) in right-to-left; don't mirror icons like clocks or media play.
- Format dates, times, numbers, and currencies for the user's locale (`package:intl`).

**Don't**
- Assume a fixed date format, decimal separator, or first day of the week.

---

## Review checklist

- [ ] Only design tokens (theme colours, text styles, spacing) are used; no one-off styles.
- [ ] Works in light and dark themes, at 200 % text scale, and in compact and expanded widths.
- [ ] Interactive elements are at least 48×48 dp and have semantics labels.
- [ ] Contrast meets 4.5 : 1 for text and 3 : 1 for UI parts; colour is never the only signal.
- [ ] Loading, empty, error, and success states are all designed and implemented.
- [ ] Errors explain how to recover; input is never lost.
- [ ] Forms set keyboard type, input action, and autofill hints; validation lives in the bloc.
- [ ] Animations are short and respect reduced motion.
- [ ] Back, deep links, and tab state behave as users expect on each platform.
- [ ] Layout uses directional padding and alignment for right-to-left support.
- [ ] Accessibility guideline checks run in widget tests.
