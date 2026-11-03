# Dart Best Practices

Language-level guidance for Dart 3.12. For framework concerns (widgets, navigation, theming) see [Flutter Best Practices](./flutter-best-practices.md). The official reference is [Effective Dart](https://dart.dev/effective-dart). This page keeps the rules we care about most and adds our own.

---

## Naming and formatting

**Do**
- Follow the standard Dart naming conventions:

  | Element | Convention | Example |
  |---------|------------|---------|
  | Class, enum, typedef, extension, type parameter | `UpperCamelCase` | `OrderRepository`, `T` |
  | Variable, parameter, method, named constant, enum value | `lowerCamelCase` | `orderId`, `maxRetries`, `Status.loading` |
  | Library, package, directory, file | `lowercase_with_underscores` | `order_repository.dart` |
  | Import prefix | `lowercase_with_underscores` | `import 'dart:math' as math;` |
  | Private member or top-level | leading `_` | `_cache` |

- Treat acronyms longer than two letters like words: `HttpClient`, `JsonParser`, `userId`, not `HTTPClient` or `userID`.
- Name booleans as questions or states: `isEmpty`, `hasError`, `canSubmit`.
- Use domain vocabulary consistently. If the business calls it a *booking*, don't call it a *reservation* elsewhere.
- Run `dart format` and let CI fail when the code is not formatted. Since Dart 3.7 the formatter decides trailing commas too, so don't argue about them in reviews.

**Don't**
- Use a `k` prefix for constants (`kMaxItems`). Use `maxItems`.
- Use Hungarian notation or abbreviations that aren't universally known.
- Suffix every class with `Impl`. Name the implementation after what makes it different: `HttpDishRepository`, `FakeDishRepository`.

---

## Analysis options

**Do**
- Start from `package:flutter_lints/flutter.yaml` (apps) or `package:lints/recommended.yaml` (pure Dart packages), and turn on the strict type checks:

```yaml
# analysis_options.yaml
include: package:flutter_lints/flutter.yaml

analyzer:
  language:
    strict-casts: true
    strict-inference: true
    strict-raw-types: true
  errors:
    unawaited_futures: error
    use_build_context_synchronously: error

linter:
  rules:
    - always_declare_return_types
    - avoid_dynamic_calls
    - discarded_futures
    - prefer_final_locals
    - prefer_single_quotes
    - unawaited_futures
    - use_build_context_synchronously
```

- Keep the analyzer at **zero warnings**. A warning that everyone ignores hides the next real one.

**Don't**
- Silence a lint with `// ignore:` without a comment that explains why.

**Why:** The strict modes turn silent `dynamic` holes into compile errors. `strict-casts` alone catches most "worked in debug, crashed in production" JSON bugs.

---

## Libraries, files, and imports

**Do**
- Use `package:` imports for anything in `lib/`. Use relative imports only inside the same package, and never reach into `lib/` from `test/` with a relative path.
- Put implementation files under `lib/src/` and expose the public API from one library file (`lib/orders.dart`) with explicit `export` statements.
- Use `show` on exports so the public surface is a deliberate list:

```dart
// lib/orders.dart
export 'src/order.dart' show Order, OrderLine;
export 'src/order_repository.dart' show OrderRepository;
```

- Keep one public top-level type per file, named after the file. Small private helpers may live in the same file. Sealed hierarchies are the exception: a sealed class and its subtypes must be in the same library, so they share one file.

**Don't**
- Import another package's `src/` folder (`package:orders/src/...`). It is private by convention, and the lint `implementation_imports` flags it.
- Use `part` / `part of` for hand-written code. Use it only for generated code (`*.g.dart`, `*.freezed.dart`).

---

## Types and type inference

**Do**
- Annotate the types of public APIs: fields, parameters, return types, and top-level variables.
- Use `final` for locals that are not reassigned, and let the type be inferred: `final orders = <Order>[];`.
- Use `const` for values known at compile time.
- Use `Object?` when you truly accept anything, and narrow it with pattern matching.

```dart
// ❌ dynamic switches off the type checker
dynamic parse(dynamic json) => json['id'];

// ✅ Explicit types, checked narrowing
int parseId(Map<String, Object?> json) => switch (json['id']) {
  final int id => id,
  _ => throw const FormatException('Missing or invalid "id"'),
};
```

**Don't**
- Use `dynamic` except at real interop boundaries, and convert it to a typed value immediately.
- Use `var` for fields. Fields are either `final` or have an explicit type.
- Write `as` casts to make the compiler quiet. A failed `as` is a runtime crash. Use a pattern or an `is` check instead.

---

## Null safety

**Do**
- Make "might be absent" explicit with `T?`, and handle it where the value enters the program.
- Use `required` for named parameters that must be supplied.
- Use `?.`, `??`, and `??=` to handle absent values concisely.
- Promote nullable locals with a check or a pattern. Copy a nullable field into a local first, because fields don't promote:

```dart
// ✅
final user = _user;
if (user == null) return;
greet(user.name);

// ✅ Same thing with a pattern
if (_user case final user?) greet(user.name);
```

**Don't**
- Use the `!` operator unless you can prove the value is non-null and the compiler can't. Leave a comment when you use it.
- Use `late` to dodge initialisation. `late` is fine when a value is set in `initState` or a constructor body before use. Elsewhere it moves a compile error to a runtime crash.
- Return `null` for collections. Return an empty collection.

---

## Immutability and equality

**Do**
- Make model, state, and event classes **immutable**: every field `final`, a `const` constructor where possible, and a `copyWith` for changes.
- Give value-like classes **value equality**. By default Dart compares objects by reference, so two `Dish` objects with the same data are *not* equal. Pick one approach per package:

  | Approach | Good for | Notes |
  |----------|----------|-------|
  | Records `(int, String)` / `({int id, String name})` | Small, local tuples | Built-in value equality. No methods, no names for the type. |
  | `package:equatable` | Hand-written models and bloc states | List `props`; collections inside are compared deeply. |
  | `package:freezed` | Larger models, unions, JSON | Generates `==`, `hashCode`, `copyWith`, `toString`, JSON. Adds a build step. |
  | Hand-written `==` / `hashCode` | Hot paths, no dependencies | Always override both together. Use `Object.hash`. |

- Expose collections as unmodifiable views, or copy them in the constructor:

```dart
// ✅
final class Order extends Equatable {
  Order({required this.id, required List<OrderLine> lines})
      : lines = List.unmodifiable(lines);

  final String id;
  final List<OrderLine> lines;

  Order copyWith({List<OrderLine>? lines}) =>
      Order(id: id, lines: lines ?? this.lines);

  @override
  List<Object?> get props => [id, lines];
}
```

**Don't**
- Mutate an object and emit or return the same instance as "new". Code that compares old and new (blocs, `shouldRepaint`, `didUpdateWidget`) will see no change. See [BLoC › States](./bloc-best-practices.md#states).
- Override `==` without `hashCode`, or base equality on mutable fields.

**Why:** Immutable objects can't be changed behind your back, so functions that receive them have no hidden side effects, and an object that was valid when created stays valid. Value equality matches how people think about data: two bookings with the same token are the same booking, wherever they live in memory.

---

## Records, patterns, and sealed classes

**Do**
- Use **sealed classes** for closed sets of alternatives (states, events, results). The compiler then checks that a `switch` handles every case:

```dart
sealed class PaymentResult {}
final class PaymentSucceeded extends PaymentResult {
  PaymentSucceeded(this.receiptId);
  final String receiptId;
}
final class PaymentDeclined extends PaymentResult {
  PaymentDeclined(this.reason);
  final String reason;
}

String describe(PaymentResult result) => switch (result) {
  PaymentSucceeded(:final receiptId) => 'Paid, receipt $receiptId',
  PaymentDeclined(:final reason) => 'Declined: $reason',
};
```

- Use **records** to return several values without inventing a class: `(int count, Duration elapsed) measure()`.
- Use **destructuring** to pull values out of records, lists, maps, and objects.
- Use `if-case` to validate JSON shapes in one step:

```dart
if (json case {'id': final int id, 'name': final String name}) {
  return Dish(id: id, name: name);
}
throw FormatException('Invalid dish: $json');
```

**Don't**
- Add a `default` / `_` branch to a `switch` over a sealed type. It hides the compile error you want when a new case is added.
- Use records for public data that has meaning in the domain. A named class documents itself and can grow methods.

---

## Class modifiers

Dart 3 class modifiers say how a class may be used outside its library.

| Modifier | Outside the library you can... | Use it for |
|----------|-------------------------------|------------|
| `final class` | Only use it (no extend, no implement) | Concrete models, states, implementations. Our default for public concrete classes. |
| `interface class` | Implement it, not extend it | Contracts like repositories. |
| `abstract interface class` | Implement it only, not instantiate it | Pure contracts (the most common choice for repository interfaces). |
| `base class` | Extend it, not implement it | Base classes that need their own constructor and invariants to run. |
| `sealed class` | Nothing; subtypes live in the same library | Closed unions (see above). |
| `mixin class` | Use as both a class and a mixin | Rare. |

**Do**
- Mark public concrete classes in packages `final` unless extension is an intended feature. Exception: classes that tests in other libraries must mock or fake (blocs, use cases). A `final` class can't be implemented outside its library, so `class MockDishBloc extends MockBloc<...> implements DishBloc` would not compile. Leave those without a modifier.
- Declare repository contracts as `abstract interface class`.

**Why:** It is the same reasoning as sealing classes in C#. A class that nobody outside can extend is cheaper to change.

---

## Extension methods and extension types

**Do**
- Use extension methods to add small, obvious helpers to types you don't own: `extension on DateTime { bool get isWeekend => ... }`.
- Use **extension types** to give a primitive a domain meaning at zero runtime cost:

```dart
extension type const BookingToken(String value) {}
extension type const DishId(int value) {}

// The compiler now rejects placeOrder(dishId, token) with swapped arguments.
Future<void> placeOrder(BookingToken token, List<DishId> dishes) async {}
```

**Don't**
- Hide business logic in extensions on `String` or `Map`. If the behaviour belongs to a concept, give the concept a class.
- Treat an extension type as a security boundary. It can be cast back to its underlying type.

---

## Collections

**Do**
- Use literals and type arguments: `<String>[]`, `<String, int>{}`.
- Use collection `if`, `for`, spreads, and null-aware elements to build collections declaratively:

```dart
final items = [
  header,
  if (showBanner) banner,
  for (final dish in dishes) DishTile(dish: dish),
  ?optionalFooter, // null-aware element: added only when non-null
];
```

- Use `isEmpty` / `isNotEmpty` instead of `length == 0`.
- Use `whereType<T>()` to filter by type, and `firstOrNull` / `lastOrNull` (built into the core library since Dart 3) instead of `try { first } catch`.
- Remember that `where`, `map`, and friends return **lazy** iterables. Call `toList()` once if you iterate more than once.

**Don't**
- Use `forEach` with a function literal. Use a `for` loop, which also allows `await`, `break`, and `return`.
- Iterate a lazy iterable inside a loop body or a `build` method repeatedly.

---

## Error handling

**Do**
- Throw `Exception` subtypes for failures the caller is expected to handle (network down, invalid input). Throw `Error` subtypes (`ArgumentError`, `StateError`) for programmer mistakes.
- Define specific exception types per boundary (`DishSearchException`, `ApiTimeoutException`) and catch them by type with `on`:

```dart
try {
  return await _client.searchDishes(query);
} on TimeoutException catch (_, stackTrace) {
  Error.throwWithStackTrace(DishSearchException('Server timed out'), stackTrace);
} on http.ClientException {
  // package:http's own type: also works on the web, unlike dart:io's SocketException
  throw const DishSearchException('No connection');
}
```

- Use `rethrow` to keep the original stack trace.
- Validate arguments of public APIs at the start of the function.

**Don't**
- Catch `Error` (or use a bare `catch`) to keep the app running. An `Error` means the code is wrong.
- Throw strings or maps. Throw typed objects.
- Swallow exceptions without logging. If you deliberately ignore one, comment why.

---

## Async and streams

**Do**
- Use `async` / `await` rather than chains of `.then()`.
- Await every `Future` or mark it explicitly with `unawaited(...)` when fire-and-forget is intended. The `unawaited_futures` and `discarded_futures` lints enforce this.
- Run independent work in parallel:

```dart
// ✅ Both requests start at once
final (menu, bookings) = await (repo.fetchMenu(), repo.fetchBookings()).wait;
```

- Add timeouts to network and device calls: `.timeout(const Duration(seconds: 10))`.
- Cancel every `StreamSubscription` you create, in `dispose` / `close`.
- Prefer `await for` for consuming a stream inside an async function, and `async*` / `yield` for producing one.

**Don't**
- Make a function `async` and return `void`. Return `Future<void>` so callers can await it and errors aren't lost.
- Use `Future.delayed` to "wait until something is ready". Wait for the actual signal.
- Expose a `StreamController` publicly. Expose `controller.stream` and keep the controller private.

---

## Isolates

Dart runs your code on one thread per isolate. Long synchronous work blocks everything on that isolate, including Flutter's UI.

**Do**
- Move CPU-heavy work (large JSON, parsing, compression, image processing) to another isolate with `Isolate.run`:

```dart
final dishes = await Isolate.run(() => parseDishes(responseBody));
```

- Pass small, plain data into and out of isolates. Copying large object graphs costs time too.
- Use a long-lived isolate with ports only when you send many messages (for example a background parser).

**Don't**
- Spawn an isolate for work that takes less than a few milliseconds. The startup cost is larger than the saving.

See [Performance › Isolates](./flutter-performance.md#isolates-and-heavy-work) for how to measure this.

---

## Documentation comments

**Do**
- Document every public class and member with `///`. Start with a one-sentence summary that ends with a period.
- Reference identifiers with square brackets: `/// Returns the [Dish]es matching [search].`
- Explain *why* and *when*, not what the next line literally does.
- Document thrown exceptions and side effects.

**Don't**
- Use `/** */` block doc comments.
- Leave commented-out code. Git remembers it.

---

## Testing

**Do**
- Use `package:test` for pure Dart and `flutter_test` for anything that touches widgets.
- Name tests after behaviour: `test('returns empty list when no dish matches', ...)`.
- Prefer **fakes** (small hand-written implementations of an interface) over mocks when the contract is simple. Use `package:mocktail` when you need to verify interactions.
- Keep tests deterministic: inject clocks, random sources, and IDs instead of calling `DateTime.now()` inside logic.

**Don't**
- Test private members. Test through the public API.
- Share mutable state between tests. Use `setUp` to build fresh objects.

---

## Review checklist

- [ ] `dart format` and `dart analyze` are clean, with strict modes on.
- [ ] No `dynamic`, unchecked `as` casts, or unexplained `!`.
- [ ] Models, states, and events are immutable and have value equality.
- [ ] Closed sets of alternatives are `sealed`, and switches over them have no catch-all branch.
- [ ] Public concrete classes in packages are `final`; contracts are `abstract interface class`.
- [ ] No imports from another package's `src/`.
- [ ] Every `Future` is awaited or explicitly `unawaited`; every subscription is cancelled.
- [ ] Exceptions are typed and caught by type; no bare `catch` that hides errors.
- [ ] Heavy synchronous work runs in an isolate.
- [ ] Public APIs have `///` doc comments.
