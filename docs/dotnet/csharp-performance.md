# C# Performance

Language-level performance guidance for C# 14 on .NET 10: how to write code that does less work and creates less garbage. The everyday rules are in [C# Best Practices › Performance](./csharp-best-practices.md#performance). Runtime, GC, I/O, and deployment topics are in [.NET Performance](./dotnet-performance.md). Database access is in [EF Core Performance](./efcore-performance.md).

---

## How to approach performance

**Do**
- **Measure before you change anything.** Write a benchmark or take a profile, find the slow part, then fix that part only.
- Use **BenchmarkDotNet** for micro-benchmarks, and always turn on `[MemoryDiagnoser]`. It shows how much memory each call allocates, which is often the real cost.
- Benchmark with **realistic input**. A parser tested on ten short lines behaves differently from one fed a 2 GB file.
- Keep the benchmark next to the code it measures, and re-run it when that code changes.
- Write down the before and after numbers in the PR.

```csharp
[MemoryDiagnoser]
public class LineParserBenchmarks
{
    private string[] _lines = [];

    [GlobalSetup]
    public void Setup() => _lines = File.ReadAllLines("samples/journal.log");

    [Benchmark(Baseline = true)]
    public int Split() => _lines.Sum(l => l.Split(' ').Length);

    [Benchmark]
    public int Span() => _lines.Sum(l => l.AsSpan().Count(' ') + 1);
}
```

**Don't**
- Optimise code because it *looks* slow. Most code is not on a hot path, and readable code is cheaper to own.
- Measure in Debug builds or with a debugger attached. The numbers mean nothing.
- Trust a single run. Run-to-run noise is real, which is why BenchmarkDotNet repeats runs and reports the spread.

**Why:** Guessing is usually wrong. A profile often shows that the time goes somewhere nobody expected, such as a hidden allocation, a regex, or a log call.

---

## Allocations and the garbage collector

Every `new` of a class, every string, and every array is placed on the **heap**. The garbage collector (GC) later finds the objects nobody uses any more and frees them. That cleanup pauses or slows the program. **Fewer allocations means less GC work**, which usually matters more than raw CPU speed.

**Do**
- Watch for **hidden allocations**. These are the common ones:

  | Source | Example | Cheaper alternative |
  |--------|---------|---------------------|
  | Boxing | Passing an `int` or a struct as `object` or as a non-generic interface | Use generics: `Process<T>(T value)` |
  | Closures | A lambda that uses a local variable | `static` lambda with a state argument |
  | String slicing | `line.Substring(0, 10)` | `line.AsSpan(0, 10)` |
  | Splitting | `line.Split(',')` | `MemoryExtensions.Split` over a span |
  | LINQ | `items.Where(...).Select(...)` in a hot loop | A plain `foreach` |
  | `params` arrays | `Log("x", a, b, c)` with `params object[]` | `params ReadOnlySpan<T>` overloads |
  | Growing collections | `new List<T>()` then thousands of `Add` calls | Set the capacity up front |

- Reuse buffers for repeated work instead of creating a new one each time.

**Don't**
- Call `GC.Collect()` to "help". It almost always makes things slower.
- Hold on to large objects longer than needed (for example in a static field). They stay in memory, and large objects are freed only by the most expensive (gen 2) collections ([.NET › The garbage collector](./dotnet-performance.md#the-garbage-collector)).

---

## Strings

Strings in .NET can't be changed after they are created. Every "change" (trim, substring, replace, concatenate) creates a **new string**.

**Do**
- Work on `ReadOnlySpan<char>` when you only need to **look at** part of a string. A span is a view into existing memory, so it creates no new string.
- Turn a span into a `string` only at the end, when you really need to keep the value.
- Use `StringComparison.Ordinal` (or `OrdinalIgnoreCase`) for identifiers, keys, file names, and protocol text. It compares raw characters and is much faster than culture-aware comparison.
- Use `string.Create` when you know the final length, and `StringBuilder` when you don't.
- Use `CompositeFormat` when the same format string is used many times.
- Use UTF-8 literals (`"GET"u8`) when you compare against raw bytes. This avoids converting bytes to a string first.

```csharp
// ❌ New strings from Substring and ToUpper, and ToUpper depends on the current culture.
var level = line.Substring(24, 5).Trim();
if (level.ToUpper() == "ERROR") { /* ... */ }

// ✅ No new strings. 'level' is a view into 'line', not a copy.
ReadOnlySpan<char> level = line.AsSpan(24, 5).Trim();
if (level.Equals("ERROR", StringComparison.OrdinalIgnoreCase)) { /* ... */ }
```

**Don't**
- Use `ToLower()`/`ToUpper()` to compare text. It creates a new string and gives wrong results in some cultures (the Turkish "I" problem). Use a case-insensitive comparison instead.
- Build strings with `+=` inside a loop. Each step copies everything built so far.
- Use string interpolation in log calls ([.NET › Logging](./dotnet-best-practices.md#logging)).

---

## Span&lt;T&gt;, Memory&lt;T&gt;, and pooled buffers

**Do**
- Use `Span<T>` and `ReadOnlySpan<T>` as the parameter type for methods that read or fill a block of data. They accept arrays, parts of arrays, strings, and stack memory without copying.
- Use `Memory<T>` / `ReadOnlyMemory<T>` when the data must be kept across an `await` or stored in a field. A `Span<T>` can't do that, because it may point into the stack.
- Use `stackalloc` for **small**, fixed-size temporary buffers. Set a hard upper limit and fall back to a pooled array above it.
- Rent large temporary buffers from `ArrayPool<T>.Shared` and always return them in a `finally` block.
- Rely on C# 14 **first-class spans**: the compiler now treats arrays and strings as spans in more places (extension methods, generic methods, overload choice), so the faster span-based methods are picked more often without extra code.

```csharp
const int StackLimit = 256;

char[]? rented = null;
Span<char> buffer = length <= StackLimit
    ? stackalloc char[StackLimit]
    : (rented = ArrayPool<char>.Shared.Rent(length));
try
{
    var written = Format(value, buffer);
    Write(buffer[..written]);
}
finally
{
    if (rented is not null)
        ArrayPool<char>.Shared.Return(rented);
}
```

**Don't**
- Use `stackalloc` with a size that comes from input without a limit. A large value crashes the process with a stack overflow, which can't be caught.
- Use a rented array after returning it, or return it twice. Another caller may already be using it.
- Assume a rented array has the exact size you asked for. It can be larger, so always slice to the length you need.

**Why:** Parsing and formatting code is usually dominated by copying and allocating. Spans let the code read the original data in place.

---

## Value types (structs)

A `struct` lives inline: on the stack, or inside the object or array that holds it. It needs no separate heap allocation. But it is **copied** every time it is passed by value.

**Do**
- Use a `struct` for small, short-lived values that are logically a single value (a coordinate, a timestamp pair, a parse result). A good rule of thumb is 16 bytes or less.
- Mark structs `readonly struct` (or `readonly record struct`). The compiler can then skip defensive copies.
- Implement `IEquatable<T>` on structs used as dictionary keys or compared often. Without it, the default equality boxes the struct and can be slow.
- Pass large `readonly` structs with `in` or `ref readonly` to avoid copying them. On a struct that isn't `readonly`, the compiler makes hidden copies anyway, which defeats the purpose.

```csharp
public readonly record struct LineRange(int Start, int Length);
```

**Don't**
- Make large, mutable structs. Copies are expensive, and changes to a copy are silently lost.
- Store structs as `object` or as an interface type. That **boxes** them (copies them to the heap), which removes the benefit.

---

## Collections

**Do**
- Pass a **capacity** when you know (or can estimate) how many items a `List<T>`, `Dictionary<TKey,TValue>`, `HashSet<T>`, or `StringBuilder` will hold. Each time a collection outgrows its storage, it allocates a bigger one and copies everything.
- Use `TryGetValue` instead of `ContainsKey` followed by the indexer. That is one lookup instead of two.
- Use `FrozenDictionary` / `FrozenSet` for lookup tables built once at startup and read many times. They are slower to create and faster to read.
- Use `SearchValues<T>` when you search for any of a fixed set of characters or strings. It picks the fastest search method once and reuses it.
- Use a dictionary **alternate lookup** to search a `string`-keyed dictionary with a `ReadOnlySpan<char>`, so you don't create a string just to look it up.
- Use `CollectionsMarshal.GetValueRefOrAddDefault` to update a dictionary value in a single lookup in hot paths. Don't add or remove entries while you still use the reference it returns.

```csharp
private static readonly FrozenDictionary<string, LogLevel> Levels =
    new Dictionary<string, LogLevel>(StringComparer.OrdinalIgnoreCase)
    {
        ["INFO"] = LogLevel.Information,
        ["WARN"] = LogLevel.Warning,
        ["ERROR"] = LogLevel.Error,
    }.ToFrozenDictionary(StringComparer.OrdinalIgnoreCase);

private static readonly FrozenDictionary<string, LogLevel>.AlternateLookup<ReadOnlySpan<char>> LevelsBySpan =
    Levels.GetAlternateLookup<ReadOnlySpan<char>>();

public static bool TryParseLevel(ReadOnlySpan<char> text, out LogLevel level) =>
    LevelsBySpan.TryGetValue(text, out level);
```

**Don't**
- Call `ToList()` or `ToArray()` "just in case". Each call copies the whole sequence.
- Use `List<T>.Contains` or `Remove` inside a loop over a large list. Each call scans the list. Use a `HashSet<T>` or a dictionary.
- Use `ConcurrentDictionary` when only one thread touches the data. It is slower than a plain `Dictionary`.

---

## LINQ in hot paths

LINQ is clear and fine for most code. In a loop that runs millions of times, though, LINQ adds a delegate call per item, and chained operators (`Where(...).Select(...)`) create extra objects.

**Do**
- Keep LINQ for readability outside hot paths.
- Replace LINQ with a plain `for` or `foreach` in measured hot paths.
- Use the span helpers (`IndexOf`, `IndexOfAny`, `Count`, `SequenceEqual`, `ContainsAny`) on spans and arrays. They are written to use the CPU's vector instructions and are very fast.

```csharp
// ❌ Calls a delegate for every entry, and goes through the slower IEnumerable<T> interface.
var errors = entries.Count(e => e.Level == LogLevel.Error);

// ✅ A direct loop the JIT can fully optimise.
var errors = 0;
foreach (var entry in entries)
{
    if (entry.Level == LogLevel.Error)
        errors++;
}
```

**Don't**
- Enumerate the same `IEnumerable<T>` several times. It may redo all the work each time.

---

## Async code

Every `async` method that actually waits creates a small state object to remember where it was. That is cheap, but it adds up in tight loops.

**Do**
- Return `ValueTask` / `ValueTask<T>` from methods that **usually complete without waiting** (for example, a read that is usually served from a buffer). It avoids allocating a `Task` in the common case.
- Use `ConfigureAwait(false)` in library code. It avoids jumping back to the original context when there is one.
- Use `IAsyncEnumerable<T>` to stream results instead of collecting everything into a list first.
- Process items in batches when each item is small. Awaiting once per thousand lines is far cheaper than awaiting once per line.

**Don't**
- Await a `ValueTask` twice, or store it to await later. Await it once, or convert it with `AsTask()`.
- Mark a method `async` only to return another task. Return that task directly, unless you need `using` or `try`/`catch` around it.
- Wrap synchronous CPU work in `Task.Run` inside a library. Let the caller decide where it runs.

---

## Lambdas and delegates

**Do**
- Mark lambdas `static` when they don't need outside variables. A lambda that captures nothing is already cheap; `static` makes the compiler **guarantee** it stays that way, by refusing accidental captures.
- Use overloads that take a **state argument** so a lambda doesn't need to capture.

```csharp
// ❌ Captures 'prefix', so a closure is created on every call.
var value = cache.GetOrAdd(key, k => prefix + k);

// ✅ Passes 'prefix' as state; the lambda captures nothing.
var value = cache.GetOrAdd(key, static (k, p) => p + k, prefix);
```

---

## Classes, generics, and method calls

**Do**
- Make classes `sealed` ([C# › Files, namespaces, and type design](./csharp-best-practices.md#files-namespaces-and-type-design)). The JIT can then call methods directly instead of looking them up at runtime, and can inline them.
- Use generic methods with constraints (`where T : IParser`) instead of interface parameters when the arguments are structs. The JIT creates a specialised copy for each struct type, so there is no boxing and calls can be inlined.
- Keep hot methods small. Small methods are more likely to be inlined by the JIT.

**Don't**
- Scatter `[MethodImpl(MethodImplOptions.AggressiveInlining)]` on methods. The JIT usually decides better. Use it only where a benchmark proves it helps.

---

## Exceptions

Throwing an exception is slow: it captures a stack trace and unwinds the call stack.

**Do**
- Use the **Try pattern** (`bool TryParse(..., out T result)`) for failures that are expected, such as a malformed log line.
- Move `throw` statements into small helper methods (or use the built-in `ArgumentNullException.ThrowIfNull` and friends). This keeps the hot method small enough to inline.

**Don't**
- Use exceptions for normal control flow ([C# › Exceptions and error handling](./csharp-best-practices.md#exceptions-and-error-handling)).
- Throw and catch inside a loop that processes every record.

---

## Regular expressions

**Do**
- Use `[GeneratedRegex]` so the pattern is turned into C# code at compile time. It starts fast and runs fast.
- Prefer plain span code (`IndexOf`, `StartsWith`, slicing) for simple, fixed formats. It is usually several times faster than any regex.
- Use `RegexOptions.NonBacktracking` for patterns that run on untrusted input. It guarantees run time grows linearly with input size. It is not source-generated (the generator falls back to a normal cached `Regex`), and it doesn't support backreferences or lookarounds.
- Keep patterns tight. Each extra alternative (`a|b|c`) adds work for **every** input, even when it rarely matches.

```csharp
public static partial class Patterns
{
    [GeneratedRegex(@"^\d{2}:\d{2}:\d{2}", RegexOptions.CultureInvariant)]
    public static partial Regex TimePrefix();
}
```

**Don't**
- Create `new Regex(...)` inside a method that runs often. Each construction parses and compiles the pattern again.
- Use regex `Split` or `Replace` in a hot loop when a span-based loop can do the same job.

---

## Untrusted input

A parser that reads files from other systems must assume that some files are corrupt or hostile. One bad file must cost at most one record. It must never crash the process, use all memory, or run for hours. Fast code that falls over on a bad file is not fast.

**Do**
- **Limit recursion depth.** A recursive reader over nested input (tags, JSON, binary trees) needs a depth limit. A deep enough input overflows the stack, and .NET cannot catch a stack overflow: the whole process dies. Above the limit, mark the record as malformed and keep the rest as raw text.
- **Check sizes read from the file with 64-bit maths.** `count * 4` on an `int` can wrap around to a small number and pass a bounds check. Compare against the space that is left instead: `if (count < 0 || count > remaining / 4)`, or compute with `long` or `checked`.
- **Check a size before you allocate with it.** Never write `new T[sizeFromFile]` before checking that the size fits the data that is actually there.
- **Give every collection that input can grow a limit.** This includes dictionaries of names seen, sample lists, and caches. Above the limit, count the rest in one "other" bucket.
- **Keep per-record work linear.** "For each item, scan all earlier items" and "try `key[1]`, `key[2]`, … until one is free" are quadratic. A record with 100,000 items then takes minutes. Keep a running counter in a dictionary instead.
- **Catch the exceptions a record can cause, per record.** Validate values before you pass them to constructors that throw (`DateTime`, `Guid`, `DateTimeOffset`). Where that is not practical, catch the expected exception types around one record, quarantine that record, and continue.
- **Add a test for each limit.** Build the hostile input in the test (100,000 nested tags, a year of `0000`, a huge count) and check that the record is flagged, the next record still parses, and the time and memory used stay small.

**Don't**
- Catch only the one exception type you have seen so far. A different corrupt value throws a different type, and that type then stops the whole file.
- Use long regex timeouts as a safety net. Each timeout costs its full duration on every line that hits it.

---

## Advanced topics

Use these only after measurement shows the simpler techniques are not enough. They make code harder to read and easier to get wrong.

### `ref` locals, `ref` returns, and `ref struct`

- A `ref` return gives the caller direct access to a value inside an array or struct, without copying it. This is useful for large structs stored in arrays.
- A `ref struct` (like `Span<T>`) can only live on the stack. Use it for short-lived parsers or readers that hold spans. Since C# 13, a `ref struct` can implement interfaces and be used as a generic argument with `allows ref struct`.

```csharp
public ref struct LineReader(ReadOnlySpan<char> text)
{
    private ReadOnlySpan<char> _remaining = text;

    public bool TryReadLine(out ReadOnlySpan<char> line)
    {
        if (_remaining.IsEmpty) { line = default; return false; }
        var end = _remaining.IndexOf('\n');
        line = end < 0 ? _remaining : _remaining[..end];
        _remaining = end < 0 ? default : _remaining[(end + 1)..];
        return true;
    }
}
```

### Inline arrays

- `[InlineArray(N)]` creates a fixed-size buffer **inside** a struct, with no separate array allocation. It is useful for small, fixed-size groups of values, such as the fields of a record with a known column count.

### Vectorisation (SIMD)

- Modern CPUs can process 16, 32, or 64 bytes in one instruction. `Vector128<T>`, `Vector256<T>`, `Vector512<T>`, and `Vector<T>` expose this.
- **Prefer the built-in methods first.** `IndexOf`, `IndexOfAny`, `SequenceEqual`, `SearchValues`, `Ascii`, and `Utf8` are already vectorised and tested on every CPU type. For maths over number arrays, `TensorPrimitives` (from the `System.Numerics.Tensors` NuGet package) does the same.
- Write your own vector code only for a proven hot loop that no built-in method covers. Always keep a plain fallback path, and check `Vector128.IsHardwareAccelerated`.

### Skipping zero-initialisation

- `[SkipLocalsInit]` stops the runtime from zero-filling `stackalloc` buffers. It saves time for large stack buffers that you fully overwrite. You must never read memory you haven't written, and it requires `<AllowUnsafeBlocks>true</AllowUnsafeBlocks>`.

### `Unsafe` and `MemoryMarshal`

- `MemoryMarshal.GetReference` and `Unsafe.As` remove safety checks such as type and bounds checks. `MemoryMarshal.Cast` reinterprets memory as another type. They are a last resort.
- Every use must have a comment that explains why it is safe, and a test that covers the edge cases.

**Don't**
- Use `unsafe` code or pointers when a span works. The JIT already removes many bounds checks in simple `for` loops over spans and arrays.

---

## Review checklist

- [ ] Every performance change has before/after numbers, and the benchmark uses realistic input.
- [ ] Hot paths avoid hidden allocations: no `Substring`/`Split`, no LINQ, no capturing lambdas, no boxing.
- [ ] Text is compared with `StringComparison.Ordinal*`, never with `ToLower()`/`ToUpper()`.
- [ ] `stackalloc` sizes have a hard limit; rented arrays are returned in `finally` and never used afterwards.
- [ ] Structs are small and `readonly`, and implement `IEquatable<T>` when used as keys.
- [ ] Collections are pre-sized when the count is known; read-only lookup tables are frozen.
- [ ] `ValueTask` is awaited exactly once; library code uses `ConfigureAwait(false)`.
- [ ] Regexes are source-generated, and simple formats use span code instead.
- [ ] Code that reads untrusted input limits recursion depth, checks sizes from the input with 64-bit maths before allocating, limits every collection the input can grow, stays linear per record, and has a test for each limit.
- [ ] Advanced techniques (`Unsafe`, SIMD, `SkipLocalsInit`) are justified by a benchmark and explained in a comment.
