# .NET Best Practices

Platform-level guidance for .NET 10: how projects are set up, built, composed, configured, observed, packaged, and tested. For language-level guidance see [C# Best Practices](./csharp-best-practices.md).

---

## Project and build setup

**Do**
- Use **SDK-style** projects only, and the **`.slnx`** solution format.
- Pin the exact SDK with `global.json` so every machine and CI agent builds with the same toolchain:

```json
{
  "sdk": { "version": "10.0.401", "rollForward": "disable" }
}
```

  Without `"rollForward": "disable"`, a newer SDK preinstalled on a CI runner (even just a newer patch) can be picked instead and change the build. `dotnet` reads `global.json` from the working directory or a parent, so run it from inside the library folder.

- Centralise shared settings in **`Directory.Build.props`** at the repo root:

```xml
<Project>
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <LangVersion>latest</LangVersion>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <AnalysisLevel>latest-recommended</AnalysisLevel>
    <EnforceCodeStyleInBuild>true</EnforceCodeStyleInBuild>
    <GenerateDocumentationFile>true</GenerateDocumentationFile>
    <Deterministic>true</Deterministic>
    <ContinuousIntegrationBuild Condition="'$(CI)' == 'true'">true</ContinuousIntegrationBuild>
  </PropertyGroup>
</Project>
```

- Use **Central Package Management** (`Directory.Packages.props` with `ManagePackageVersionsCentrally`) so every package version is declared once.
- Commit an **`.editorconfig`** that holds naming, style, and analyzer severities. It is the single source of truth for style.
- Enable `NuGetAudit` for vulnerability-checked restores.

**Don't**
- Commit `bin/`, `obj/`, `.vs/`, user files (`*.user`), or binary dependencies. Restore packages from NuGet feeds.
- Reference DLLs by path (`<Reference HintPath=...>`) when a package exists. If a vendor library has no package, publish it to an internal feed.
- Suppress analyzer warnings globally. Suppress locally, with a justification (`[SuppressMessage(..., Justification = "...")]`).

---

## Dependency injection

The *principle* is covered in [SOLID › DIP](./solid-principles.md#dependency-inversion-principle-dip). This section covers the container mechanics in `Microsoft.Extensions.DependencyInjection`.

**Do**
- Use **constructor injection**. Primary constructors keep it concise.
- Register services in **one composition root** (the host), grouped into `IServiceCollection` extension methods per module:

```csharp
public static class DeviceServiceCollectionExtensions
{
    public static IServiceCollection AddDevices(this IServiceCollection services, IConfiguration config)
    {
        services.AddOptions<DeviceOptions>()
                .Bind(config.GetSection(DeviceOptions.SectionName))
                .ValidateDataAnnotations()
                .ValidateOnStart();

        services.AddSingleton<IDeviceTransport, SerialDeviceTransport>();
        services.AddSingleton(TimeProvider.System);
        return services;
    }
}
```

- Choose lifetimes deliberately:

  | Lifetime | Use for | Watch out for |
  |----------|---------|---------------|
  | Singleton | Stateless services, caches, clients that own a physical resource (port, socket) | Must be thread-safe. Must not depend on scoped services. |
  | Scoped | Per-request/per-operation units of work (`DbContext`) | Outside ASP.NET Core you must create scopes yourself (`IServiceScopeFactory`). |
  | Transient | Lightweight, stateless helpers | Disposable transients are tracked until their scope ends. |

- Enable `ValidateScopes` and `ValidateOnBuild` (on by default in Development) to catch **captive dependencies**, for example a singleton holding a scoped service.
- Use **keyed services** (`AddKeyedSingleton<IPrinter, LaserPrinter>("laser")` with `[FromKeyedServices("laser")]`) when several implementations of one interface are selected by name. Don't hand-roll factories for this.

**Don't**
- Use the **service locator** pattern: injecting `IServiceProvider` and calling `GetService` in business code. The exception is factories and infrastructure plumbing.
- Use `static` mutable state or hand-rolled singletons (`Instance` properties). Let the container own lifetimes.
- Have constructors that do work (I/O, connecting to devices). Constructors assign; do the work in an explicit `StartAsync`/`OpenAsync` or a hosted service.
- Inject more than about 5 dependencies. That signals the class has too many responsibilities ([SRP](./solid-principles.md#single-responsibility-principle-srp)).

---

## Configuration and options

**Do**
- Bind configuration to **strongly typed options classes** with the Options pattern, and validate them at startup:

```csharp
public sealed class DeviceOptions
{
    public const string SectionName = "Device";

    [Required] public required string PortName { get; init; }
    [Range(1_200, 921_600)] public int BaudRate { get; init; } = 115_200;
}
```

- Inject the right interface:
  - `IOptions<T>`: a singleton snapshot that never changes.
  - `IOptionsSnapshot<T>`: recomputed per scope.
  - `IOptionsMonitor<T>`: live reload with change notifications (for long-lived singletons).
- Layer configuration sources: `appsettings.json` → `appsettings.{Environment}.json` → environment variables → command line → secret store.
- Use [source-generated options validation](https://learn.microsoft.com/dotnet/core/extensions/options-validation-generator) (`[OptionsValidator]`) for trimming- and AOT-friendly validation.

**Don't**
- Read `IConfiguration["Some:Key"]` deep inside business code.
- Keep secrets (passwords, keys, connection strings with credentials) in source control. Use User Secrets locally and a vault or environment variables in deployed environments.
- Write configuration files at runtime to persist application state. Use a proper store.

---

## Logging

**Do**
- Inject `ILogger<T>` and use **structured logging** with message templates:

```csharp
logger.LogInformation("Job {JobId} completed in {ElapsedMs} ms", job.Id, elapsed.TotalMilliseconds);
```

- For hot paths and high-volume logs, use the **`LoggerMessage` source generator**:

```csharp
internal static partial class Log
{
    [LoggerMessage(Level = LogLevel.Warning, Message = "Device {Device} did not respond within {Timeout}")]
    public static partial void DeviceTimeout(ILogger logger, string device, TimeSpan timeout);
}
```

- Choose levels consistently:

  | Level | Meaning |
  |-------|---------|
  | `Trace`/`Debug` | Diagnostic detail; off in production by default |
  | `Information` | Significant lifecycle events (started, completed, connected) |
  | `Warning` | Unexpected but recovered; may need attention |
  | `Error` | An operation failed |
  | `Critical` | The application/component cannot continue |

- Pass the exception object itself: `logger.LogError(ex, "...")`.
- Use log scopes (`logger.BeginScope`) to attach correlation data such as an operation ID.

**Don't**
- Use string interpolation or concatenation in log calls (`$"Job {id}"`). It defeats structured logging and allocates even when the level is disabled.
- Log secrets, personal data, or full payloads.
- Write custom file loggers. Plug a provider (console, OpenTelemetry, Serilog sink) into `ILogger`.

---

## Hosted services, time, and HTTP

**Do**
- Run long-running and background work in a **`BackgroundService`** hosted by the Generic Host (`Host.CreateApplicationBuilder`). Use Windows Service / systemd integration (`AddWindowsService()`, `AddSystemd()`) for daemons.

```csharp
public sealed class StatusPoller(IDeviceStatusReader reader, ILogger<StatusPoller> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(5));
        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            try { await reader.RefreshAsync(stoppingToken); }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                logger.LogError(ex, "Status refresh failed");
            }
        }
    }
}
```

- Use **`System.Threading.Channels`** for producer/consumer pipelines between components.
- Inject **`TimeProvider`** instead of calling `DateTime.Now`/`UtcNow`. Tests can then use `FakeTimeProvider`. Store and transmit times in UTC (`DateTimeOffset`).
- Use **`IHttpClientFactory`** (typed or named clients) and add resilience with `Microsoft.Extensions.Http.Resilience` (`AddStandardResilienceHandler()`).
- For non-HTTP resilience (device I/O, queues), use **Polly v8** resilience pipelines rather than hand-written retry loops.

**Don't**
- Use `System.Timers.Timer`, `BackgroundWorker`, or raw `Thread`s for background work in new code.
- `new HttpClient()` per call. It exhausts sockets and ignores DNS changes.
- Let an unhandled exception escape `ExecuteAsync` silently. By default (`BackgroundServiceExceptionBehavior.StopHost`) it stops the host, so decide explicitly.

---

## Observability

**Do**
- Instrument with **OpenTelemetry**: logs via `ILogger`, metrics via `System.Diagnostics.Metrics.Meter`, traces via `ActivitySource`.
- Define meters and activity sources per component, with stable names (`Company.Product.Component`).
- Expose **health checks** (`AddHealthChecks()`) that report the real state of dependencies, including devices, databases, and external services.
- Propagate correlation IDs across process boundaries (W3C trace context).

**Don't**
- Build bespoke status or telemetry plumbing when the standard APIs cover it.

---

## Library and SDK authoring

**Do**
- Treat the **public API as a contract**. Keep it small, make everything else `internal`, and track it with `Microsoft.CodeAnalysis.PublicApiAnalyzers` (`PublicAPI.Shipped.txt` / `PublicAPI.Unshipped.txt`).
- Follow **semantic versioning**: breaking change → major, new feature → minor, fix → patch.
- Accept **abstractions** and return **concrete or read-only** types. Offer async APIs with `CancellationToken`.
- Make the library **DI-friendly without requiring DI**: provide an `AddXyz(this IServiceCollection)` extension in a separate package or namespace, and keep types constructible by hand.
- Depend only on `Microsoft.Extensions.*.Abstractions` packages (logging, options), not on concrete hosting.
- Ship nullable annotations, XML docs, **SourceLink**, and symbol packages (`.snupkg`). Set `PackageReadmeFile`, `PackageLicenseExpression`, and `RepositoryUrl`.
- Multi-target (`<TargetFrameworks>net10.0;netstandard2.0</TargetFrameworks>`) **only** if consumers need it, and isolate the differences with `#if` in as few places as possible.
- Mark obsolete APIs with `[Obsolete("Use X instead.")]` for at least one minor release before removing them.

**Don't**
- Hold static mutable state, read process-global configuration, or write to the working directory from a library.
- Throw from public APIs without documenting the exception types.
- Expose third-party types in your public API unless you are prepared to version with them.

---

## Native interop

**Do**
- Use **`[LibraryImport]`** (source-generated marshalling) instead of `[DllImport]` in new code. It is AOT-friendly and avoids runtime marshalling stubs.

```csharp
internal static partial class NativeMethods
{
    [LibraryImport("vendor_device", StringMarshalling = StringMarshalling.Utf8)]
    internal static partial int Device_Open(string port, out DeviceSafeHandle handle);
}
```

- Wrap native handles in a **`SafeHandle`** subclass. Never pass raw `IntPtr` handles around.
- **Isolate interop** in an infrastructure adapter behind an interface, so the rest of the code (and the tests) never touch native code.
- Translate native error codes into .NET exceptions or result types at the adapter boundary.
- Document and enforce bitness/platform requirements (`RuntimeIdentifier`, `[SupportedOSPlatform("windows")]`).

**Don't**
- Let native types, error codes, or callbacks leak into domain or application code.
- Call native code from multiple threads unless the vendor library is documented as thread-safe. Serialise access instead.

---

## Testing

**Do**
- Use one test framework consistently (**xUnit v3** recommended) with an assertion library and a mocking library or hand-written fakes.
- Name tests by behaviour: `MethodName_State_ExpectedOutcome` or `Should_..._When_...`.
- Structure tests as **Arrange / Act / Assert**, with one logical assertion per test.
- Build a test pyramid:

  | Kind | Scope | Speed | Proportion |
  |------|-------|-------|------------|
  | Unit | One class or use case, dependencies faked | ms | Most |
  | Integration | Real adapters against real or containerised dependencies (Testcontainers), or `WebApplicationFactory` | seconds | Some |
  | End-to-end / hardware-in-the-loop | Whole system on target hardware | minutes | Few, critical paths only |

- Prefer **fakes** (in-memory implementations of ports) over deep mocks for anything with behaviour. Use `FakeTimeProvider` and `FakeLogger` from `Microsoft.Extensions.*.Testing`.
- Make tests deterministic: no `Thread.Sleep`, no real clocks, no shared mutable state, no ordering dependencies.
- Run tests in CI on every PR, and collect coverage as a **signal** (find untested areas), not a target.

**Don't**
- Test private methods directly. Test through the public behaviour.
- Mock types you don't own (for example `SerialPort`). Wrap them in your own interface and mock that.

---

## Security basics

**Do**
- Keep dependencies current (Dependabot/Renovate) and fail the build on known vulnerabilities (`NuGetAudit`).
- Validate all input at system boundaries: API requests, files, device responses, configuration.
- Apply least privilege to service accounts, file system access, and network listeners (bind to `localhost` unless remote access is required).
- Use the built-in cryptography APIs. Never write your own.
- Sign assemblies and packages if your distribution requires it, with keys stored outside the repo.

**Don't**
- Commit secrets, private keys, or credentials. Scan for them in CI.
- Disable TLS validation, even "temporarily".

---

## Review checklist

- [ ] The project inherits `Directory.Build.props`; package versions are central; no binaries or `obj/`/`bin/` are committed.
- [ ] Services use constructor injection, lifetimes are deliberate, there is no service locator or static singleton, and constructors do no I/O.
- [ ] Configuration is bound to validated options (`ValidateOnStart`) and no secrets are in source.
- [ ] Logging is structured (no interpolation), at the right level, and contains no sensitive data.
- [ ] Background work uses `BackgroundService`/channels; time comes from `TimeProvider`; HTTP goes through `IHttpClientFactory` with resilience.
- [ ] Public SDK API is minimal, documented, and tracked by PublicApiAnalyzers, and breaking changes bump the major version.
- [ ] Native interop uses `LibraryImport` and `SafeHandle` and is isolated behind an interface.
- [ ] New behaviour has tests that are deterministic and fast; integration tests cover the adapters.
