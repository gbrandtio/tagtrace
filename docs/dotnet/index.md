# Engineering Guidelines

These pages describe how we write, structure, and review code in this repository. They are a **shared baseline**, not a rulebook to be applied blindly: when a rule doesn't fit, deviate deliberately and write down why (in the PR, or in an [ADR](./architecture-principles.md#architecture-decision-records-adrs) for significant decisions).

**Baseline:** .NET 10 (LTS) · C# 14 · SDK-style projects · nullable reference types enabled.

## Contents

| Page | What it covers |
|------|----------------|
| [C# Best Practices](./csharp-best-practices.md) | Language-level guidance: naming, nullability, records, pattern matching, exceptions, async/await, disposal, LINQ, performance, documentation. |
| [.NET Best Practices](./dotnet-best-practices.md) | Platform-level guidance: project setup, build hygiene, dependency injection, configuration, logging, hosted services, observability, SDK/library authoring, native interop, testing, security. |
| [SOLID Principles](./solid-principles.md) | The five SOLID principles with C# examples, plus DRY, KISS, YAGNI, composition over inheritance, Law of Demeter, Tell-Don't-Ask. |
| [Architecture Principles](./architecture-principles.md) | Separation of concerns, coupling and cohesion, architectural styles and when to pick them, boundaries, cross-cutting concerns, patterns, anti-patterns, ADRs, architecture tests. |
| [Clean Architecture](./clean-architecture.md) | The dependency rule, layer responsibilities, reference solution layout, ports and adapters, use cases, testing per layer, and when to keep it lighter. |
| [C# Performance](./csharp-performance.md) | Language-level performance: measuring, allocations, strings, spans and pooled buffers, structs, collections, LINQ, async, exceptions, regex, untrusted input, and advanced topics (`ref struct`, inline arrays, SIMD, `Unsafe`). |
| [.NET Performance](./dotnet-performance.md) | Platform-level performance: diagnostic tools, the garbage collector, JIT tiering and PGO, ReadyToRun and Native AOT, threading, file I/O, serialization, HTTP, caching, logging cost, runtime settings. |
| [File I/O Performance](./file-io-performance.md) | Reading and writing files fast: measured API comparison, buffer sizes, `FileOptions`, bytes vs text, sync vs async, parallel reads with `RandomAccess`, memory-mapped files, Pipelines, batched and durable writes, many small files. |
| [EF Core Performance](./efcore-performance.md) | Database access performance (not the `efcore` log parser): seeing the SQL, context pooling, projections and no-tracking, N+1, split queries, paging, query caching, bulk writes, indexes, raw SQL, connections. |

## Suggested reading order

1. **New to the codebase:** C# → .NET → SOLID → Architecture → Clean Architecture.
2. **Designing a new component:** Architecture → Clean Architecture → SOLID.
3. **Working on a hot path or a slow query:** C# Performance → .NET Performance → File I/O Performance → EF Core Performance.
4. **Reviewing a PR:** jump straight to the *Review checklist* at the end of each page.

## How the pages are organised

Every page follows the same shape:

- **Do / Don't** bullets: the rule in one line.
- **Examples**: `❌` marks code to avoid, `✅` marks the preferred alternative.
- **Why**: the reasoning, so you can judge edge cases yourself.
- **Review checklist**: a short list to use during code review.

The code samples leave out doc comments so they stay short. Real code must document every class and every function, as described in [C# › Comments and documentation](./csharp-best-practices.md#comments-and-documentation).

The pages avoid repeating each other. For example, the *principle* of dependency inversion lives in [SOLID](./solid-principles.md#dependency-inversion-principle-dip), the *mechanics* of the DI container live in [.NET](./dotnet-best-practices.md#dependency-injection), and *how layers are wired* lives in [Clean Architecture](./clean-architecture.md#composition-root-and-registration).

## Citing a rule in a review

Headings are stable, so link to them directly:

> See [C# › Async/await](./csharp-best-practices.md#asyncawait): this blocks on `.Result`.

If a guideline is outdated or wrong, change it in a PR like any other code. Guidelines that nobody follows should be fixed or removed, not ignored.

## Precedence

When sources disagree, follow this order:

1. Analyzer and compiler diagnostics enforced by the build (`.editorconfig`, `Directory.Build.props`).
2. Accepted ADRs.
3. These guidelines.
4. Personal preference.
