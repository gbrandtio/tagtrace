# .NET Performance

Platform-level performance guidance for .NET 10: how to measure, and how the garbage collector, the JIT compiler, threading, I/O, serialization, caching, and deployment choices affect speed and memory. Language-level techniques are in [C# Performance](./csharp-performance.md). Database access is in [EF Core Performance](./efcore-performance.md).

---

## Measuring

**Do**
- Pick the tool that answers your question:

  | Question | Tool |
  |----------|------|
  | Is method A faster than method B? How much does it allocate? | **BenchmarkDotNet** with `[MemoryDiagnoser]` |
  | What is the app doing right now (CPU, GC, thread pool, exceptions)? | **`dotnet-counters`** |
  | Where does the time go? | **`dotnet-trace`**, then open the trace in Visual Studio, PerfView, or speedscope |
  | What is filling up memory? | **`dotnet-gcdump`** |
  | Why did it hang or crash? | **`dotnet-dump`** |

- Install the diagnostic tools as .NET tools (`dotnet tool install -g dotnet-counters`) and use them against a Release build.
- Start with `dotnet-counters`. It shows at a glance whether the problem is CPU, GC, lock contention, or a starved thread pool.

```bash
dotnet-counters monitor --process-id <pid> --counters System.Runtime
dotnet-trace collect --process-id <pid> --profile cpu-sampling
```

- Add **performance regression tests** for critical paths: run benchmarks in CI and compare against a stored baseline.
- Publish your own metrics with `System.Diagnostics.Metrics` (a `Meter` with counters and histograms) for things like *lines parsed per second*. See [.NET › Observability](./dotnet-best-practices.md#observability).

**Don't**
- Measure Debug builds, builds with a debugger attached, or the first few seconds after startup. The JIT is still optimising code during that time (see below).
- Compare numbers taken on different machines, or on a laptop running on battery.

---

## The garbage collector

The garbage collector (GC) frees memory that is no longer used. Knowing roughly how it works explains most memory advice.

- **Generations.** New objects start in *generation 0*. Most objects die young, and gen 0 collections are cheap. Objects that survive move to gen 1 and then gen 2. Gen 2 collections are expensive.
- **Large Object Heap (LOH).** Objects of 85,000 bytes or more (for example, a large array or string) go straight to the LOH. It is collected only with gen 2, so frequent large allocations are costly.
- **Workstation vs Server GC.** Workstation GC uses one heap and suits desktop and small console apps. Server GC uses one heap per CPU core and suits busy services. ASP.NET Core apps use Server GC by default.
- **DATAS** (Dynamic Adaptation To Application Sizes) is on by default with Server GC. It grows and shrinks the number of heaps to match the load, so a quiet service uses less memory.

**Do**
- Keep objects short-lived, or keep them alive for the whole process. The costly case is "medium-lived" objects that survive into gen 2 and then die.
- Pool or reuse large buffers instead of allocating them repeatedly ([C# › Span, Memory, and pooled buffers](./csharp-performance.md#spant-memoryt-and-pooled-buffers)).
- Choose the GC mode in the project file, and record why:

```xml
<PropertyGroup>
  <!-- A batch tool that processes large files: use Server GC on all cores. -->
  <ServerGarbageCollection>true</ServerGarbageCollection>
  <!-- Turn DATAS off, so the GC starts with one heap per core instead of growing slowly. -->
  <GarbageCollectionAdaptationMode>0</GarbageCollectionAdaptationMode>
</PropertyGroup>
```

- Use `GCSettings.LatencyMode = GCLatencyMode.SustainedLowLatency` only around short sections where pauses must be avoided, and restore the previous mode afterwards. It only has an effect when concurrent GC is on (the default). For a short section that must have no GC at all, use `GC.TryStartNoGCRegion`.

**Don't**
- Call `GC.Collect()` in production code.
- Add finalizers. Objects with a finalizer survive at least one extra collection. Use `SafeHandle` instead ([.NET › Native interop](./dotnet-best-practices.md#native-interop)).
- Tune GC settings without a before/after measurement.

---

## The JIT compiler, tiering, and PGO

.NET code is compiled to machine code **at run time** by the JIT (just-in-time) compiler.

- **Tiered compilation.** A method is first compiled quickly with few optimisations, so the app starts fast. If the method is called often, it is compiled again with full optimisations.
- **Dynamic PGO** (profile-guided optimisation). While running the first version, the runtime records how the method is actually used, for example which concrete type sits behind an interface. The optimised version is then built for that real usage.
- **Escape analysis.** The .NET 10 JIT can place some small objects that never leave a method on the stack instead of the heap. This is a bonus, not something to design around.

**Do**
- Leave tiered compilation and dynamic PGO **on** (the defaults). They are a large part of why modern .NET is fast.
- Expect the first calls to be slower. Warm up before measuring.
- Use **ReadyToRun** (`<PublishReadyToRun>true</PublishReadyToRun>`) when startup time matters. The code is precompiled at publish time, and the JIT still re-optimises hot methods later.

**Don't**
- Turn off tiered compilation to "make it faster". It usually slows startup and removes PGO.
- Draw conclusions from a benchmark that runs a method only a few times.

---

## Startup and deployment

| Option | What it does | Good for | Trade-off |
|--------|--------------|----------|-----------|
| **Framework-dependent** (default) | Uses the .NET runtime installed on the machine | Most apps | Requires the runtime on the target |
| **Self-contained** | Ships the runtime with the app | Machines you don't control | Larger download |
| **ReadyToRun** | Precompiles code at publish time | Faster startup, still fully dynamic | Larger files |
| **Native AOT** | Compiles the whole app to a native executable; no JIT at run time | CLI tools, small services, fastest startup and lowest memory | No runtime code generation; reflection-heavy libraries may not work; every dependency must be trim- and AOT-safe |
| **Trimming** | Removes unused code from the published output | Smaller self-contained and AOT apps | Code reached only by reflection can be removed by mistake |

**Do**
- Treat trim and AOT warnings as errors in projects that publish with trimming or AOT. A warning means something may break at run time.
- Use source generators (JSON, logging, regex, configuration binding) instead of reflection. They work with trimming and AOT, and they are faster anyway.
- Set `<InvariantGlobalization>true</InvariantGlobalization>` for tools that don't need culture-specific behaviour. It removes the ICU dependency and speeds up startup. Be aware that it also changes culture-aware comparison, upper/lower-casing, and sorting of non-ASCII text, so test with real input first.

**Don't**
- Enable Native AOT in a library project. AOT is a property of the final app. Libraries should only declare `<IsAotCompatible>true</IsAotCompatible>` and fix the warnings it reports.

---

## Threading and the thread pool

.NET runs most background work and `async` continuations on a shared **thread pool**. If its threads are blocked, all work in the process slows down.

**Do**
- Keep I/O asynchronous all the way down in services, where blocked threads starve the pool ([C# › Async/await](./csharp-best-practices.md#asyncawait)). A CPU-bound batch tool that reads one file at a time can use sync reads, which are as fast or faster ([File I/O › Sync or async](./file-io-performance.md#sync-or-async)).
- Use `Parallel.ForEachAsync` with a `MaxDegreeOfParallelism` to process many independent items, such as many files.
- Use `Parallel.For` / `Parallel.ForEach` for CPU-heavy work on in-memory data.
- Use `Channel<T>` to connect a producer (for example, a file reader) with one or more consumers (for example, parsers). Use a **bounded** channel so a fast producer can't fill all memory.
- Use the `System.Threading.Lock` type for `lock` statements. It is faster than locking on a plain `object`.
- Keep locked sections tiny. Never `await`, do I/O, or call unknown code while holding a lock.
- Use `Interlocked` for simple shared counters.

```csharp
var channel = Channel.CreateBounded<string>(new BoundedChannelOptions(10_000)
{
    SingleWriter = true,
    FullMode = BoundedChannelFullMode.Wait,
});
```

**Don't**
- Block on async code with `.Result` or `.Wait()`. It ties up a pool thread and can starve the pool.
- Start more parallel work than there are cores for CPU-bound work. Extra threads only add switching overhead.
- Share mutable state between parallel workers without synchronisation. Give each worker its own state and merge the results at the end.

**Why:** Thread pool starvation looks like "everything is slow but the CPU is idle". `dotnet-counters` shows it as a growing *ThreadPool Queue Length*.

---

## File and stream I/O

These are the basics. Measured comparisons, parallel reads, memory-mapped files, and durable writes are in [File I/O Performance](./file-io-performance.md).

**Do**
- Read large files **as a stream**, line by line or block by block. Don't load the whole file into memory with `File.ReadAllText` or `File.ReadAllLines`.
- Pass `FileOptions.SequentialScan` when you read a file from start to end. It tells the operating system to read ahead.
- Read in large pieces (64 KB or more). The default 4 KB buffer causes many small system calls. When you read into your own large buffer, or through a `StreamReader` with its own large buffer, set the `FileStream`'s `BufferSize = 0` so the data is not copied twice.
- Use `System.IO.Pipelines` for byte streams that arrive in pieces you don't control, such as network or decompression streams. For a synchronous file parser, a plain read loop is as fast and simpler ([File I/O › Pipelines](./file-io-performance.md#systemiopipelines)).
- Use `RandomAccess` for reads at known positions without a shared file position.
- Choose `CompressionLevel.Fastest` when writing compressed output that is read soon after. Choose `Optimal` or `SmallestSize` only when file size matters more than time.

```csharp
await using var stream = new FileStream(path, new FileStreamOptions
{
    Mode = FileMode.Open,
    Access = FileAccess.Read,
    Share = FileShare.Read,
    Options = FileOptions.Asynchronous | FileOptions.SequentialScan,
    BufferSize = 0, // the StreamReader below has its own 64 KB buffer
});
using var reader = new StreamReader(stream, Encoding.UTF8, detectEncodingFromByteOrderMarks: false, bufferSize: 64 * 1024);
while (await reader.ReadLineAsync(cancellationToken) is { } line)
{
    // ...
}
```

This is the simple option: one `string` per line. It is about 3× slower than working on bytes. For hot paths, see [File I/O › Work on bytes, not strings](./file-io-performance.md#work-on-bytes-not-strings).

**Don't**
- Open and close the same file repeatedly in a loop.
- Use `MemoryStream` as a growing buffer for large data. It reallocates and copies as it grows. Use pooled buffers or `RecyclableMemoryStream` (from the `Microsoft.IO.RecyclableMemoryStream` NuGet package) instead.

---

## Serialization

**Do**
- Use `System.Text.Json` with **source generation** (`JsonSerializerContext`). It avoids reflection at run time and works with trimming and AOT.
- Create `JsonSerializerOptions` **once** and reuse it. Building a new instance for each call throws away its internal caches.
- Serialize straight to the output stream (`JsonSerializer.SerializeAsync(stream, ...)`) instead of building a string first.
- Use `JsonSerializer.DeserializeAsyncEnumerable` to stream large JSON arrays one item at a time.
- Use `Utf8JsonWriter` / `Utf8JsonReader` directly for the hottest paths, such as writing millions of records.

```csharp
[JsonSerializable(typeof(LogEntry))]
[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase)]
internal sealed partial class LogJsonContext : JsonSerializerContext;

await JsonSerializer.SerializeAsync(output, entry, LogJsonContext.Default.LogEntry, cancellationToken);
```

**Don't**
- Use `Newtonsoft.Json` in new code. `System.Text.Json` is faster and allocates less.
- Serialize to a `string` and then encode it to bytes. Write UTF-8 directly.

---

## HTTP

**Do**
- Use `IHttpClientFactory` or one long-lived `HttpClient` ([.NET › Hosted services, time, and HTTP](./dotnet-best-practices.md#hosted-services-time-and-http)). Connections are then reused.
- Use `HttpCompletionOption.ResponseHeadersRead` for large responses, and read the body as a stream.
- Set `PooledConnectionLifetime` on long-lived handlers so DNS changes are picked up.
- Enable response compression for large text payloads.

**Don't**
- Create a new `HttpClient` per request. It wastes connections and can exhaust sockets.
- Read a large response with `ReadAsStringAsync` when you only need to parse it once.

---

## Caching and pooling

**Do**
- Cache results that are expensive to compute and read often. Give every cache a **size limit** and an **expiry**, otherwise it is a memory leak.
- Use `HybridCache` when you need both an in-memory and a distributed cache. It also prevents many callers from computing the same missing value at the same time.
- Use `ObjectPool<T>` (from `Microsoft.Extensions.ObjectPool`) for objects that are expensive to create and safe to reset, such as large `StringBuilder` instances.

**Don't**
- Pool cheap objects. Pooling has its own cost, and the GC handles small short-lived objects very well.
- Cache data without deciding how it becomes stale.

---

## Logging cost

**Do**
- Use source-generated logging (`[LoggerMessage]`) ([.NET › Logging](./dotnet-best-practices.md#logging)). It does no work when the log level is disabled.
- Guard expensive log arguments with `logger.IsEnabled(level)`.
- Log per batch or per file in hot loops, not per item.

**Don't**
- Log at `Information` or above inside a loop that runs per record.

---

## Runtime settings reference

Set these in the app's project file. Change them only with a measurement that shows the benefit.

| Setting | Default | When to change |
|---------|---------|----------------|
| `ServerGarbageCollection` | `false` (`true` for ASP.NET Core) | Turn on for throughput-heavy batch tools and services on multi-core machines. |
| `GarbageCollectionAdaptationMode` | not set (DATAS is on when Server GC is on) | Set to `0` for batch jobs that should use all cores from the start, rather than keep memory low. |
| `ConcurrentGarbageCollection` | `true` | Turn off only for pure batch jobs where pause times don't matter. |
| `TieredCompilation` | `true` | Leave on. |
| `TieredPGO` | `true` | Leave on. |
| `PublishReadyToRun` | `false` | Turn on when startup time matters. |
| `PublishAot` | `false` | Turn on for CLI tools and small services where startup and memory matter, and all dependencies support AOT. |
| `InvariantGlobalization` | `false` | Turn on when the app never needs culture-specific formatting. |

---

## Review checklist

- [ ] The claim "this is faster" is backed by BenchmarkDotNet results or a trace from a Release build.
- [ ] Large files are streamed, not loaded whole, and large buffers are pooled.
- [ ] No `GC.Collect()`, no finalizers, and GC settings changes are measured and explained.
- [ ] Tiered compilation and PGO are left on; trim/AOT warnings are treated as errors where trimming or AOT is used.
- [ ] No blocking on async code; parallel work has a bounded degree of parallelism; channels are bounded.
- [ ] JSON uses source-generated `System.Text.Json` with reused options.
- [ ] `HttpClient` is reused through the factory.
- [ ] Every cache has a size limit and an expiry.
- [ ] Hot loops don't log per item.
