# Architecture Principles

How we structure systems so they stay **changeable, testable, and understandable** as they grow. Class-level design is covered in [SOLID](./solid-principles.md). The concrete layering we use is described in [Clean Architecture](./clean-architecture.md).

---

## Goals

Good architecture keeps the cost of change low. In practice, that means:

| Goal | What it looks like |
|------|--------------------|
| **Changeability** | A change in one concern (a vendor, a protocol, a storage engine, a UI) is contained in one place. |
| **Testability** | Business behaviour can be tested quickly without hardware, network, database, or UI. |
| **Explicit boundaries** | It is obvious which code may depend on which, and the build enforces it. |
| **Understandability** | A new developer can find where a feature lives from its name. |
| **Deferrable decisions** | Frameworks, databases, and transports are details that can be chosen late and swapped later. |

---

## Separation of concerns

**Do**
- Separate **what** the system does (business rules, use cases) from **how** it does it (I/O, frameworks, devices, protocols).
- Keep each of these in its own place: domain logic, orchestration (use cases), integration (adapters), and delivery (API, UI, CLI, service host).
- Keep code that changes together close together (**high cohesion**), and code that changes for different reasons apart (**low coupling**).

**Don't**
- Mix transport details (serial frames, HTTP status codes, SQL) with business decisions in the same method.
- Let framework types (`HttpContext`, `DbContext`, vendor SDK classes) flow into business logic.

---

## Coupling, cohesion, and dependency direction

- **Depend in the direction of stability.** Volatile code (UI, adapters, vendor integrations) depends on stable code (domain, use-case contracts), never the reverse.
- **Avoid cycles.** Project and namespace dependencies must form a directed acyclic graph.
- **Prefer explicit contracts.** Modules talk through interfaces, DTOs, or messages, not through each other's internals.
- **Measure coupling by the blast radius of change.** If changing one thing forces a release of five components, they are coupled, whatever the diagrams say.

```mermaid
flowchart LR
    UI[Delivery / Host] --> App[Application]
    Infra[Infrastructure] --> App
    App --> Domain
    UI -.->|composition only| Infra
```

---

## Architectural styles

Pick the **simplest style that fits**. They can be combined, for example a modular monolith whose modules each use Clean Architecture internally.

| Style | Idea | Good fit | Watch out for |
|-------|------|----------|---------------|
| **Layered (n-tier)** | Presentation → Business → Data | Simple CRUD apps | Business logic tends to depend on the data layer; leaks easily. |
| **Clean / Hexagonal / Onion** | Domain and use cases at the centre; I/O as adapters around ports | Systems with real business rules and several integrations (devices, external APIs, storage) | Ceremony if the domain is trivial. See [Clean Architecture](./clean-architecture.md). |
| **Vertical slice** | Organise by feature/use case; each slice owns its request, handler, and data access | Feature-rich applications with many independent use cases | Duplication across slices; needs discipline for shared domain rules. |
| **Modular monolith** | One deployable, with strongly separated modules that communicate via contracts | Most new products; a path to services later if needed | Module boundaries must be enforced, or it decays into a big ball of mud. |
| **Microservices** | Independently deployable services per bounded context | Large organisations, independent scaling or release cadence | Operational cost, distributed failures, data consistency. Don't start here. |

**Default recommendation:** start with a **modular monolith** (or a single library/service) structured with **Clean Architecture**, and organise the application layer by feature (vertical slices inside the use-case layer).

---

## Boundaries and contracts

**Do**
- Define **DTOs at every boundary** (API, messages, persistence, device protocols) and map them to and from domain types explicitly.
- Keep domain models **independent of serialisation**: no `[JsonProperty]` or ORM attributes on domain types.
- Put an **anti-corruption layer (ACL)** in front of every third-party or vendor API. The adapter translates vendor types, error codes, and quirks into your own model.

```csharp
// The vendor's world stays inside the adapter
internal sealed class VendorScannerAdapter(VendorScannerApi api) : IScanner
{
    public async Task<Result<ScannedPage>> ScanAsync(CancellationToken ct)
    {
        var (code, raw) = await Task.Run(() => (api.DoScan(out var bytes), bytes), ct);
        return code switch
        {
            0    => Result<ScannedPage>.Ok(ScannedPage.FromBytes(raw)),
            0x21 => Result<ScannedPage>.Fail("Paper jam"),
            _    => Result<ScannedPage>.Fail($"Scanner error 0x{code:X}")
        };
    }
}
```

- Version external contracts (APIs, messages, SDK surface) explicitly, and never break them silently.
- Write mapping code by hand, or with a source generator (for example Mapperly). Mapping stays visible and compile-time checked.

**Don't**
- Share a single model across API, domain, and persistence "to save time".
- Use reflection-based auto-mappers that hide breaking changes until runtime.

---

## Cross-cutting concerns

Handle these **consistently and centrally**, not ad hoc in every class:

| Concern | Approach |
|---------|----------|
| Logging and tracing | `ILogger<T>`, `ActivitySource`, OpenTelemetry ([.NET › Logging](./dotnet-best-practices.md#logging)) |
| Validation | At boundaries (DTO validation) and in the domain (invariants in constructors and factories) |
| Error handling | Result types for expected failures; exceptions for exceptional ones; one top-level handler per host |
| Resilience | Retries, timeouts, and circuit breakers via Polly pipelines in adapters, never in domain code |
| Configuration | Validated options, bound in the composition root |
| Authorization / security | At the delivery boundary, plus use-case checks where the rules are business rules |
| Transactions / units of work | Owned by the application layer (the use case defines the boundary) |

Implement them with **decorators** or **pipeline behaviours** around use-case handlers or ports, so business code stays clean.

---

## Patterns catalogue

Use patterns to name solutions, not as goals in themselves.

| Pattern | Use it to | .NET notes |
|---------|-----------|------------|
| **Strategy** | Swap algorithms or policies at runtime | Register several implementations; resolve via `IEnumerable<T>` or keyed services |
| **Factory** | Encapsulate complex or conditional creation | Prefer DI + keyed services; use a factory for runtime-parameterised objects |
| **Adapter** | Make an external API fit your port | The standard shape for every infrastructure integration |
| **Decorator** | Add behaviour (retry, caching, logging, metrics) without changing the inner class | Register manually or with Scrutor's `Decorate` |
| **Mediator / CQRS-lite** | Decouple delivery from use-case handlers; separate reads from writes | A simple handler interface is often enough; a library is optional |
| **Repository** | Abstract aggregate persistence behind a domain-oriented interface | One per aggregate root. Not a generic `IRepository<T>` over EF Core, which already is a repository/unit of work |
| **Outbox** | Publish events reliably with a state change | Needed when you write to a DB and a broker in one operation |
| **Options** | Typed, validated configuration | [.NET › Configuration](./dotnet-best-practices.md#configuration-and-options) |
| **State machine** | Model devices and workflows with explicit states and transitions | Make states and valid transitions explicit types; reject invalid transitions |

---

## Anti-patterns

| Anti-pattern | Why it hurts | Instead |
|--------------|--------------|---------|
| **God class** | Every change touches it; untestable; merge conflicts | Split by responsibility ([SRP](./solid-principles.md#single-responsibility-principle-srp)) |
| **Static helpers / singletons as hidden dependencies** | Invisible coupling, global state, impossible to fake | Inject dependencies explicitly |
| **Service locator** | Dependencies are hidden and fail only at runtime | Constructor injection |
| **Anaemic domain (where rules exist)** | Business rules scatter into services and get duplicated | Put invariants and behaviour on domain types |
| **Leaky abstraction** | Interfaces expose vendor types, SQL, or HTTP details | Shape ports in domain terms; translate in adapters |
| **Shared database between modules** | Hidden coupling through the schema | Each module owns its data; integrate via contracts |
| **Distributed monolith / premature microservices** | Network calls and deployment pain without independence | Modular monolith first |
| **Magic strings and implicit conventions** | Typos and silent breakage | Constants, enums, strongly typed IDs and options |
| **Persisting state in ad-hoc files** | Races, corruption, no migration path | A deliberate store behind a port |

---

## Architecture Decision Records (ADRs)

Record significant decisions where the code lives, so the *why* survives people moving on.

**When to write one:** choosing a framework, storage, or transport; defining a module boundary; deviating from these guidelines; or any decision that is expensive to reverse.

**Where:** `docs/adr/NNNN-short-title.md`, numbered sequentially. Never delete an ADR. Supersede it with a new one.

**Template**

```markdown
# NNNN. <Decision title>

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD
- Deciders: <names or roles>

## Context
What problem or force prompts this decision? What constraints apply?

## Decision
What we will do, stated in full sentences.

## Consequences
What becomes easier or harder. Risks and follow-up work.

## Alternatives considered
Each option, with why it was not chosen.
```

---

## Enforcing architecture with tests

Rules that aren't enforced decay. Encode dependency rules as **architecture tests** that run with the unit tests, using [NetArchTest](https://github.com/BenMorris/NetArchTest) or [ArchUnitNET](https://github.com/TNG/ArchUnitNET):

```csharp
[Fact]
public void Domain_does_not_depend_on_infrastructure_or_frameworks()
{
    var result = Types.InAssembly(typeof(DomainMarker).Assembly)
        .ShouldNot()
        .HaveDependencyOnAny("MyProduct.Infrastructure", "Microsoft.EntityFrameworkCore", "System.IO.Ports")
        .GetResult();

    Assert.True(result.IsSuccessful, string.Join(", ", result.FailingTypeNames ?? []));
}
```

Project references enforce the coarse rules: Domain must not reference Infrastructure. Architecture tests enforce the finer ones: naming, sealed types, no framework types in particular namespaces.

---

## Review checklist

- [ ] Business decisions are separated from I/O, frameworks, and vendor details.
- [ ] Dependencies point toward stable code, and there are no cycles between projects or namespaces.
- [ ] Each boundary has its own DTOs with explicit mapping, and vendor APIs sit behind an anti-corruption layer.
- [ ] Cross-cutting concerns are handled centrally (decorators, pipeline behaviours, host-level handlers).
- [ ] No new god classes, static hidden dependencies, service locators, or leaky abstractions.
- [ ] Significant or expensive-to-reverse decisions have an ADR.
- [ ] Architecture tests cover new dependency rules.
