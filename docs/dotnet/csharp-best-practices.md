# C# Best Practices

Language-level guidance for C# 14 on .NET 10. For platform concerns (DI, configuration, logging, packaging) see [.NET Best Practices](./dotnet-best-practices.md).

---

## Naming and formatting

**Do**
- Follow the standard .NET naming conventions:

  | Element | Convention | Example |
  |---------|------------|---------|
  | Namespace, type, method, property, event, constant | `PascalCase` | `OrderService`, `MaxRetries` |
  | Interface | `I` + `PascalCase` | `IClock` |
  | Type parameter | `T` + `PascalCase` | `TResult` |
  | Parameter, local | `camelCase` | `orderId` |
  | Private field | `_camelCase` | `_logger` |
  | Async method | suffix `Async` | `SaveAsync` |

- Use names that describe intent: `RetryCount` over `rc`, `IsExpired` over `CheckFlag`.
- Use domain vocabulary consistently. If the business calls it a *job*, don't call it a *task* in one place and a *work item* in another.
- Let `.editorconfig` enforce formatting and run `dotnet format` in CI.

**Don't**
- Use Hungarian notation (`strName`, `iCount`) or abbreviations that aren't universally known.
- Use `#region` to hide large classes. If a class needs regions, it probably needs to be split.
- Mix spaces and tabs or argue about brace style in reviews. The formatter decides.

---

## Files, namespaces, and type design

**Do**
- Use **file-scoped namespaces** and put **one top-level type per file**, with the file named after the type.
- Make namespaces match folder structure.
- Make classes **`sealed` by default**. Unseal them only when inheritance is an intended extension point.
- Use the **narrowest visibility** that works: `private` before `internal` before `public`. In libraries, everything that isn't part of the public contract should be `internal`.
- Prefer **composition** over inheritance ([SOLID › Composition over inheritance](./solid-principles.md#composition-over-inheritance)).

```csharp
// ✅
namespace Contoso.Billing;

public sealed class InvoiceNumberGenerator(TimeProvider clock)
{
    public string Next() => $"INV-{clock.GetUtcNow():yyyyMMdd}-{Random.Shared.Next(1000, 9999)}";
}
```

**Don't**
- Create "Utils", "Helpers", or "Common" classes full of unrelated static methods. Give the behaviour a home on the type it concerns, or in a focused, well-named type.
- Expose public mutable fields.

**Why:** Sealed, internal types are cheaper to change. Nobody outside can depend on them, and the JIT can devirtualise calls to them.

---

## Nullable reference types

**Do**
- Enable `<Nullable>enable</Nullable>` for every project, and treat nullable warnings as errors.
- Model "may be absent" explicitly with `T?`, and validate at boundaries.
- Use guard helpers for public API arguments:

```csharp
public void Register(Device device, string name)
{
    ArgumentNullException.ThrowIfNull(device);
    ArgumentException.ThrowIfNullOrWhiteSpace(name);
    ArgumentOutOfRangeException.ThrowIfNegative(device.Port);
    // ...
}
```

- Use nullability attributes (`[NotNullWhen]`, `[MaybeNullWhen]`, `[MemberNotNull]`) so `Try*` patterns flow correctly.

**Don't**
- Silence warnings with the null-forgiving operator `!` unless you can prove the value is non-null and the compiler can't. Leave a comment when you use it.
- Return `null` for collections. Return an empty collection instead.

---

## Records, immutability, and object initialisation

**Do**
- Use `record` / `record struct` for **value-like data**: DTOs, messages, value objects, results.
- Prefer immutable types: `init` accessors, `required` members, and `with` expressions for copies.
- Use `readonly struct` / `readonly record struct` for small (≤ ~16 bytes) value types.

```csharp
public sealed record DeviceSettings
{
    public required string PortName { get; init; }
    public int BaudRate { get; init; } = 115_200;
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(5);
}

var fast = settings with { Timeout = TimeSpan.FromSeconds(1) };
```

**Don't**
- Use records for entities with identity and lifecycle. Value equality is wrong for them.
- Add behaviour-heavy logic to DTOs.

---

## Collections

**Do**
- Expose the **least capable** interface that callers need: `IReadOnlyList<T>`, `IReadOnlyCollection<T>`, `IEnumerable<T>`, or `IReadOnlyDictionary<TKey, TValue>`.
- Use **collection expressions**: `int[] ids = [1, 2, 3];`, `List<string> names = [];`, `[..first, ..second]`.
- Use `FrozenDictionary` / `FrozenSet` for lookup tables that are built once and read often.
- Keep mutable collections private, and return read-only views.

```csharp
public sealed class Batch
{
    private readonly List<Item> _items = [];
    public IReadOnlyList<Item> Items => _items;

    public void Add(Item item) => _items.Add(item);
}
```

**Don't**
- Expose `List<T>` publicly, or `ArrayList`/`Hashtable` (non-generic collections) anywhere.
- Enumerate an `IEnumerable<T>` more than once without materialising it first.

---

## Modern language features

Use modern features when they make code **clearer**, not just because they are newer.

### Pattern matching and switch expressions

```csharp
// ✅
public static string Describe(Status status) => status switch
{
    Status.Ready                 => "Ready",
    Status.Busy                  => "Busy",
    Status.Error { Code: >= 500 } => "Hardware fault",
    Status.Error e               => $"Error {e.Code}",
    _ => throw new UnreachableException($"Unknown status {status}")
};

if (message is { Length: > 0 } text) Process(text);
```

Prefer `is null` / `is not null` over `== null`, because they can't be overridden by operator overloads.

### Primary constructors

**Do:** use them for **dependency injection** into services, and for small types.

```csharp
public sealed class ReportService(IReportStore store, ILogger<ReportService> logger)
{
    public Task<Report?> GetAsync(Guid id, CancellationToken ct) => store.FindAsync(id, ct);
}
```

**Don't:** use them when you need to validate or transform parameters, or when the parameter should be `readonly`. Primary-constructor parameters are mutable captures. If that matters, assign the parameter to a `private readonly` field.

### C# 14 features

- **`field` keyword**: removes the backing field boilerplate when a property needs only light logic.

  ```csharp
  public string Name
  {
      get;
      set => field = value?.Trim() ?? throw new ArgumentNullException(nameof(value));
  }
  ```

- **Extension members** (`extension` blocks): group extension methods **and extension properties** for one receiver type. Use them for genuinely general-purpose helpers, and don't use them to bolt domain logic onto types you own.

  ```csharp
  public static class EnumerableExtensions
  {
      extension<T>(IEnumerable<T> source)
      {
          public bool IsEmpty => !source.Any();
      }
  }
  ```

- **Null-conditional assignment** (`order?.Status = Status.Closed;`): fine for optional objects, but don't use it to hide a null that should be an error.

### Other features

- Use `var` when the type is obvious from the right-hand side, and an explicit type otherwise.
- Use `nameof(...)` instead of string literals for member names.
- Use raw string literals (`"""`) for JSON, SQL, or other multi-line text.
- Use `const` for true compile-time constants and `static readonly` for everything else.

---

## Exceptions and error handling

**Do**
- Throw exceptions for **exceptional** situations: broken invariants, programmer errors, and unrecoverable infrastructure failures.
- Model **expected** outcomes (validation failed, not found, device busy) as return values: `bool Try*(out T)`, a nullable result, or a `Result<T>` type.
- Throw the **most specific** built-in exception that fits (`ArgumentException`, `InvalidOperationException`, `TimeoutException`, `NotSupportedException`). Create custom exception types only when callers need to catch them specifically.
- Preserve the original exception: `throw;` (not `throw ex;`), or wrap it with an inner exception.
- Catch exceptions only where you can **handle** them (retry, translate, or compensate) or at the outermost boundary (to log and fail gracefully).

```csharp
// ✅ Expected failure as a value
public sealed record Result<T>
{
    public T? Value { get; }
    public string? Error { get; }
    public bool IsSuccess => Error is null;

    private Result(T? value, string? error) => (Value, Error) = (value, error);
    public static Result<T> Ok(T value) => new(value, null);
    public static Result<T> Fail(string error) => new(default, error);
}
```

Pair it with a non-generic `Result` (with `Result.Ok()` and `Result.Fail(error)`) for operations that return no value. Use one shared result type across the codebase; don't define a new one per module.

```csharp
// ❌ Swallowing
try { device.Reset(); } catch { }

// ❌ Losing the stack trace
catch (Exception ex) { throw ex; }

// ✅ Translate at a boundary, keep the cause
catch (IOException ex)
{
    throw new DeviceCommunicationException($"Failed to talk to {portName}.", ex);
}
```

**Don't**
- Use exceptions for control flow.
- Catch `Exception` (or `catch {}`) except at a top-level boundary, and never without logging it.
- Return error codes like `-1` or magic strings. Use types.

---

## Async/await

**Do**
- Go **async all the way**. When a method calls I/O, it should be `async` and return `Task`/`Task<T>`/`ValueTask<T>`.
- Accept a **`CancellationToken`** as the last parameter on every async method that does I/O or can take time, and pass it down.
- Use `Task.WhenAll` for independent concurrent operations.
- Use `await using` for `IAsyncDisposable` resources.
- Use `IAsyncEnumerable<T>` with `[EnumeratorCancellation]` to stream results.
- In **library code**, use `ConfigureAwait(false)`, because there is no UI or synchronisation context to return to. In application code (ASP.NET Core, worker services) it is unnecessary.
- Use `SemaphoreSlim` (not `lock`) to serialise async access to a shared resource.

```csharp
public async Task<Reading> ReadAsync(CancellationToken cancellationToken = default)
{
    await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
    try
    {
        var raw = await _transport.ReceiveAsync(cancellationToken).ConfigureAwait(false);
        return Reading.Parse(raw);
    }
    finally
    {
        _gate.Release();
    }
}
```

**Don't**
- Block on async code: `.Result`, `.Wait()`, `.GetAwaiter().GetResult()`. They cause deadlocks and thread-pool starvation.
- Write `async void`, except for event handlers. Unhandled exceptions in `async void` crash the process.
- Wrap synchronous code in `Task.Run` inside a library just to "make it async". Let the caller decide.
- Fire-and-forget tasks without observing their exceptions. Use a hosted service or a channel instead.
- Use `ValueTask` unless profiling shows a benefit, and never await a `ValueTask` twice.
- Use `Thread.Sleep` in async code. Use `await Task.Delay(..., ct)`, or a `PeriodicTimer` for loops.

---

## Disposal and resource management

**Do**
- Wrap every `IDisposable` you create in a `using` declaration (`using var stream = ...;`) or a `using` block.
- Implement `IDisposable` / `IAsyncDisposable` on types that **own** disposable fields. Use the simple pattern for sealed classes:

```csharp
public sealed class Connection : IAsyncDisposable
{
    private readonly Stream _stream;
    private bool _disposed;

    public async ValueTask DisposeAsync()
    {
        if (_disposed) return;
        _disposed = true;
        await _stream.DisposeAsync();
    }
}
```

- Use `ObjectDisposedException.ThrowIf(_disposed, this);` in members that must not run after disposal.
- Wrap unmanaged handles in `SafeHandle`. Don't write finalizers yourself ([.NET › Native interop](./dotnet-best-practices.md#native-interop)).

**Don't**
- Dispose objects you didn't create (for example, injected dependencies). The owner disposes.
- Rely on the garbage collector to release ports, files, sockets, or handles.

---

## LINQ

**Do**
- Use LINQ for clarity on collections. Keep queries short, and name intermediate results when a chain gets long.
- Materialise once (`ToList()`, `ToArray()`) when results are enumerated more than once.
- Prefer `Any()` over `Count() > 0`, and `TryGetNonEnumeratedCount` when it is relevant.

**Don't**
- Put side effects inside `Select`/`Where`.
- Use LINQ in measured hot paths where allocations matter. Use a plain loop there.

---

## Performance

**Do**
- **Measure first**, with BenchmarkDotNet or a profiler. Optimise only proven hot paths.
- Use `Span<T>`, `ReadOnlySpan<T>`, and `Memory<T>` for parsing and buffer handling without copies.
- Use `ArrayPool<T>.Shared` for large temporary buffers, and `StringBuilder` or interpolated string handlers for building strings in loops.
- Use `static` lambdas when no state needs capturing, to avoid closure allocations.
- Use source-generated `Regex` (`[GeneratedRegex]`) and `System.Text.Json` source generation in performance-sensitive code.

**Don't**
- Sacrifice readability for micro-optimisations that nobody measured.
- Concatenate strings in loops.

---

## Comments and documentation

**Do**
- Write **XML documentation** (`///`) on **every class and every function**, whatever its visibility: public, internal, private, and test code alike. Also document interfaces, records, structs, enums, properties, and constructors.
- Make missing docs fail the build. `<GenerateDocumentationFile>true</GenerateDocumentationFile>` with warning `CS1591` covers public members only. For internal and private members, add StyleCop.Analyzers rule `SA1600` and set `documentInternalElements` and `documentPrivateElements` to `true` in `stylecop.json`.
- Make doc comments describe **business logic**: the business purpose, the rules the member enforces, valid ranges and units, the business meaning of the return value, the exceptions it throws, and its thread-safety.
- Where a design applies a SOLID principle, name the principle and the reason in the type's `<remarks>` ([SOLID](./solid-principles.md)).
- Use inline comments (`//`) only to explain business logic that the code cannot express on its own, such as a business rule or a device constraint.

```csharp
/// <summary>Moves the stamp head to the given offset from the top edge of the page.</summary>
/// <param name="offsetMm">Offset in millimetres. Must be between 0 and 300, the printable area of the page.</param>
/// <param name="cancellationToken">Cancels the move before the device acknowledges it.</param>
/// <returns>A task that completes when the device confirms the new position.</returns>
/// <exception cref="ArgumentOutOfRangeException"><paramref name="offsetMm"/> is outside the printable area.</exception>
/// <remarks>This method is not thread-safe.</remarks>
public Task MoveToAsync(int offsetMm, CancellationToken cancellationToken = default);
```

**Don't**
- Write doc or inline comments that restate what the code already says through its structure and naming (`i++; // increment i`, `/// <summary>Gets the name.</summary>`).
- Write inline comments that explain a language construct or a mechanical step.
- Leave commented-out code. Git remembers it.
- Leave `TODO`, `FIXME`, or any other placeholder comment. Finish the work instead.
- Write forward-looking statements ("for now", "will be improved later"). A comment describes the code as it is.

---

## Review checklist

- [ ] Names reveal intent; the formatter and analyzers pass with no new suppressions.
- [ ] Types are `sealed`/`internal` unless there is a reason not to be.
- [ ] There are no nullable warnings, and `!` is used only where justified.
- [ ] DTOs and value objects are immutable records; collections are exposed as read-only interfaces.
- [ ] Exceptions are specific, never swallowed, and rethrown with `throw;`. Expected failures are values.
- [ ] Async all the way: no `.Result`/`.Wait()`/`async void`, and every I/O method takes a `CancellationToken`.
- [ ] Every created disposable is disposed, and nothing disposes what it doesn't own.
- [ ] Performance changes are backed by measurements.
- [ ] Every class and every function has XML docs that describe business logic, and inline comments explain business logic only.
- [ ] There are no `TODO` or placeholder comments, no commented-out code, and no forward-looking statements.
