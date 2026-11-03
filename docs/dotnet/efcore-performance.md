# EF Core Performance

Performance guidance for Entity Framework Core 10: how to keep database access fast and predictable. EF Core is an adapter and belongs in the Infrastructure layer ([Clean Architecture](./clean-architecture.md)). General runtime topics are in [.NET Performance](./dotnet-performance.md).

The single most important idea: **EF Core turns your LINQ into SQL, and the database does the work.** Most performance problems come from sending the wrong SQL, too much SQL, or SQL that fetches far more data than needed.

---

## See the SQL

You can't fix a query you haven't seen.

**Do**
- Log the generated SQL and its duration during development:

```csharp
services.AddDbContext<AppDbContext>(options => options
    .UseSqlServer(connectionString)
    .LogTo(Console.WriteLine, [DbLoggerCategory.Database.Command.Name], LogLevel.Information));
```

- Use `query.ToQueryString()` to print the SQL of a single query without running it.
- Look at the database's **execution plan** for slow queries (SQL Server: *Include Actual Execution Plan*; PostgreSQL: `EXPLAIN ANALYZE`; SQLite: `EXPLAIN QUERY PLAN`).
- Watch for EF's warnings in the log. For example, it warns about queries that may load too much data through several `Include` calls.
- Collect EF Core's built-in metrics (the `Microsoft.EntityFrameworkCore` meter) in production to track query counts and how often EF's own query cache is hit.

**Don't**
- Enable `EnableSensitiveDataLogging()` outside development. It writes parameter values, which may be personal data, to the logs.

---

## DbContext lifetime and pooling

**Do**
- Keep a `DbContext` **short-lived**: one per web request, per message, or per unit of work. It is cheap to create.
- Use **context pooling** (`AddDbContextPool`) in busy services. Contexts are reset and reused instead of being created each time.
- Use `IDbContextFactory<T>` (`AddPooledDbContextFactory`) when you need a context outside a DI scope, such as in a background service or for parallel work.

```csharp
services.AddPooledDbContextFactory<AppDbContext>(options => options.UseNpgsql(connectionString));

public sealed class ImportJob(IDbContextFactory<AppDbContext> contextFactory)
{
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        await using var db = await contextFactory.CreateDbContextAsync(cancellationToken);
        // ...
    }
}
```

**Don't**
- Keep one `DbContext` alive for the whole application. It slowly fills with tracked entities and gets slower with every operation.
- Use the same `DbContext` from several threads at once. It is not thread-safe.
- Store per-request state in a pooled context's fields. It would leak into the next request.

---

## Fetch only what you need

**Do**
- **Project** with `Select` into a DTO or record when you only need some columns. EF then selects only those columns.
- Use `AsNoTracking()` for read-only queries. EF then skips change tracking, which saves time and memory.
- Set `QueryTrackingBehavior.NoTracking` as the default for contexts that mostly read, and opt in to tracking with `AsTracking()` where you update.
- Filter, sort, and page **in the query**, before `ToListAsync()`. Everything after `ToListAsync()` runs in memory, on all rows.

```csharp
// ❌ Loads every column of every order into memory, tracks them, then filters.
var recent = (await db.Orders.ToListAsync())
    .Where(o => o.CreatedAt > since)
    .Select(o => new OrderSummary(o.Id, o.Total));

// ✅ The database filters and returns two columns. Nothing is tracked.
var recent = await db.Orders
    .Where(o => o.CreatedAt > since)
    .Select(o => new OrderSummary(o.Id, o.Total))
    .ToListAsync(cancellationToken);
```

- Use `AnyAsync()` to check for existence, not `CountAsync() > 0`.
- Use `FirstOrDefaultAsync()` when you only need one row. `SingleOrDefaultAsync()` has to read two rows to prove there is only one, which is fine when that check is what you want.
- Use `FindAsync(id)` for lookups by primary key when you are going to **update** the entity. It returns the entity from the context without a database call if it is already tracked. Note that `Find` always tracks the result and can't use `Include` or `Select`, so for read-only lookups use `AsNoTracking().FirstOrDefaultAsync(x => x.Id == id)`.

**Don't**
- Load whole entities to read one or two values.
- Call `ToList()` in the middle of a query to "make it work". If EF can't translate part of a query, rewrite that part; don't pull the whole table into memory.

---

## Avoid the N+1 problem

The *N+1 problem* is one query for a list, plus one extra query **for each item** in that list. With 1,000 items, that is 1,001 database round trips.

**Do**
- Load related data in the same query, with `Include` or, better, with a projection.
- Keep **lazy loading off** (it is off by default). It hides N+1 queries behind normal property access.

```csharp
// ❌ One query for the customers, then one query per customer for their orders.
foreach (var customer in await db.Customers.ToListAsync())
{
    var count = await db.Orders.CountAsync(o => o.CustomerId == customer.Id);
}

// ✅ One query.
var counts = await db.Customers
    .Select(c => new { c.Id, c.Name, OrderCount = c.Orders.Count() })
    .ToListAsync(cancellationToken);
```

**Don't**
- Run queries inside a loop over the results of another query.

---

## Single vs split queries

When you `Include` **several collections**, a single SQL query joins them all. The result repeats each parent row for every combination of children. This is called *cartesian explosion*, and it can turn a few hundred rows into millions.

**Do**
- Use `AsSplitQuery()` when a query includes two or more collection navigations. EF then sends one query per collection and combines the results.
- Keep single queries (the default) when you include at most one collection, or only reference navigations. They are one round trip and are consistent with each other.
- Set the default for a context if most of its queries load several collections. The setting lives on the provider options: `UseSqlServer(connectionString, o => o.UseQuerySplittingBehavior(QuerySplittingBehavior.SplitQuery))`.

```csharp
var orders = await db.Orders
    .Include(o => o.Lines)
    .Include(o => o.Payments)
    .AsSplitQuery()
    .ToListAsync(cancellationToken);
```

**Don't**
- Use split queries with `Skip`/`Take` without a unique `OrderBy`. Each split query could otherwise see the rows in a different order.

---

## Paging and large results

**Do**
- Page every query that can return an unbounded number of rows.
- Prefer **keyset paging** ("give me the next 50 rows after id X") over offset paging (`Skip(10000).Take(50)`). With offset paging the database still reads and throws away all the skipped rows, so later pages get slower.
- Stream very large results with `AsAsyncEnumerable()` and `await foreach`. Rows are processed one at a time instead of all being held in a list. Combine it with `AsNoTracking()`. The connection stays busy until the loop ends, so don't run other queries on the same context inside the loop. Use a second context instead.

```csharp
// Keyset paging: uses the index on Id and costs the same for every page.
var page = await db.Events
    .Where(e => e.Id > lastSeenId)
    .OrderBy(e => e.Id)
    .Take(50)
    .ToListAsync(cancellationToken);
```

**Don't**
- Return `IQueryable<T>` from a repository or API without a limit. The caller can then accidentally load the whole table.

---

## Query caching and parameters

Turning LINQ into SQL takes time. EF caches the result **by the shape of the query**, and the database caches its execution plan **by the SQL text**. Both caches only help if the same query shape produces the same SQL each time.

**Do**
- Use normal C# variables in queries. EF sends them as **parameters**, so the SQL text stays the same and both caches are reused.
- Check the SQL for `Contains` over a list (`ids.Contains(x.Id)`). In EF Core 10 it becomes `IN (@ids1, @ids2, ...)`, one parameter per item, padded so that lists of similar size share the same SQL. Very large lists are the case to measure. You can change the translation for one query with `EF.Constant(...)` or `EF.Parameter(...)`, or for the whole context with `UseParameterizedCollectionMode(...)`.
- Use **compiled queries** (`EF.CompileAsyncQuery`) for small queries that run extremely often. EF does less work per call because the LINQ-to-SQL step is done once, up front.
- Use a **compiled model** (`dotnet ef dbcontext optimize`) for contexts with a large model. It reduces startup time, which helps CLI tools and serverless functions. It must be regenerated whenever the model changes, and has documented limitations, so check them for your provider first.

```csharp
// The CancellationToken parameter is recognised by EF and cancels the query.
private static readonly Func<AppDbContext, Guid, CancellationToken, Task<Device?>> DeviceById =
    EF.CompileAsyncQuery((AppDbContext db, Guid id, CancellationToken ct) =>
        db.Devices.AsNoTracking().FirstOrDefault(d => d.Id == id));
```

**Don't**
- Build queries with values baked into expression trees by hand (`Expression.Constant(value)`). Each distinct value produces new SQL, which fills both caches with one-off entries.
- Build SQL strings by concatenation. It defeats caching and opens the door to SQL injection.

---

## Writing data

**Do**
- Call `SaveChangesAsync()` **once** for a batch of changes, not once per entity. EF groups the statements into as few round trips as it can.
- Use `ExecuteUpdateAsync` and `ExecuteDeleteAsync` to change or delete many rows with one SQL statement. No entities are loaded or tracked.

```csharp
// ❌ Loads every expired session, tracks it, then deletes them one statement at a time.
var expired = await db.Sessions.Where(s => s.ExpiresAt < now).ToListAsync(cancellationToken);
db.Sessions.RemoveRange(expired);
await db.SaveChangesAsync(cancellationToken);

// ✅ One DELETE statement.
await db.Sessions.Where(s => s.ExpiresAt < now).ExecuteDeleteAsync(cancellationToken);

// ✅ One UPDATE statement.
await db.Devices
    .Where(d => d.LastSeen < cutoff)
    .ExecuteUpdateAsync(s => s.SetProperty(d => d.Status, DeviceStatus.Offline), cancellationToken);
```

- For **large imports** (tens of thousands of rows or more), insert in batches of a few thousand rows and call `ChangeTracker.Clear()` after each batch. The tracker otherwise keeps growing and every save gets slower.
- For very large imports, use the provider's bulk-copy API (`SqlBulkCopy` for SQL Server, binary `COPY` for PostgreSQL) instead of EF's change tracker.
- Keep transactions short. Prepare the data first, then open the transaction, write, and commit.

**Don't**
- Read an entity just to update one field when `ExecuteUpdateAsync` can do it directly.
- Forget how `ExecuteUpdate`/`ExecuteDelete` differ from `SaveChanges`. They run **immediately**, on their own, and are not part of the next `SaveChanges`. They also bypass the change tracker, so tracked copies in the context are now out of date. If they must succeed or fail together with a `SaveChanges`, wrap both in an explicit transaction (`db.Database.BeginTransactionAsync()`).
- Keep a transaction open while waiting for user input, network calls, or device responses. It holds database locks the whole time.

---

## Schema and indexes

The fastest query is useless without the right index.

**Do**
- Add an **index** for every column you regularly filter, join, or sort by (`HasIndex`). Foreign keys get an index by convention.
- Use **composite indexes** whose column order matches your most common filters, and **filtered indexes** (`HasFilter`) for queries that always target a subset of rows.
- Give string columns a **maximum length** (`HasMaxLength`) where the data has one. Unlimited text columns can't be indexed efficiently on some databases.
- Choose the inheritance mapping deliberately. **TPH** (one table for all types, the default) is usually the fastest. **TPT** (one table per type) needs joins for every query and is often slow. **TPC** (one table per concrete type) avoids joins but makes queries over the base type use `UNION`.
- Store small value groups that are always read together as **complex types** or **JSON columns**, instead of separate tables with joins.

**Don't**
- Add indexes "just in case". Each index slows down every insert, update, and delete on that table.
- Use value converters on columns you filter by unless you've checked the SQL. Some conversions stop the database from using an index.

---

## Raw SQL

**Do**
- Use `FromSql` (or `SqlQuery<T>` for unmapped result types) when a query is much better written by hand, for example with database-specific features. Interpolated values are sent as parameters, so it is safe.
- Compose LINQ on top of `FromSql` where possible (`Where`, `Select`, `AsNoTracking`).

```csharp
var top = await db.Database
    .SqlQuery<DeviceErrorCount>($"SELECT DeviceId, COUNT(*) AS Errors FROM Events WHERE Level = {level} GROUP BY DeviceId")
    .ToListAsync(cancellationToken);
```

**Don't**
- Use `FromSqlRaw` or `ExecuteSqlRaw` with values concatenated into the string. That is a SQL injection risk ([.NET › Security basics](./dotnet-best-practices.md#security-basics)).

---

## Connections and resilience

**Do**
- Let the database driver pool connections. It does this by default; keep the pool enabled.
- Enable retries for transient failures on cloud databases (`EnableRetryOnFailure()`). With retries on, wrap manual transactions in `db.Database.CreateExecutionStrategy().ExecuteAsync(...)` so the whole transaction is retried together.
- Set a **command timeout** that matches the work. Long reports may need more; interactive queries should fail fast.
- For SQLite, use write-ahead logging (`PRAGMA journal_mode=WAL`) when reads and writes happen at the same time.

**Don't**
- Open and hold connections manually unless you need to. EF opens and closes them per operation.
- Turn on MARS (*Multiple Active Result Sets*) on SQL Server without a reason. It adds overhead to every command.

---

## Advanced topics

- **Interceptors** (`DbCommandInterceptor`, `SaveChangesInterceptor`) let you inspect or change commands centrally, for example to add query hints or to log slow commands. Keep them light: they run on every command.
- **Global query filters** (such as soft-delete or tenant filters) are added to every query of that entity. Make sure the filtered columns are indexed. EF Core 10 supports **named filters**, so you can turn off one filter without turning off the others.
- **`AsNoTrackingWithIdentityResolution()`** skips change tracking but still makes sure the same row appears as the same object. Use it for read-only queries that include the same related entity many times.
- **`LeftJoin`** (a new LINQ operator in .NET 10, translated by EF Core 10) expresses a left outer join directly, instead of the older `GroupJoin` + `SelectMany` + `DefaultIfEmpty` pattern. The SQL is the same, and the query is easier to read and review.
- **Change-tracking strategy.** The default *snapshot* tracking compares every tracked entity on `SaveChanges`. For contexts that track very many entities, turning off `AutoDetectChangesEnabled` during a bulk operation and calling `DetectChanges()` once can help.

---

## Review checklist

- [ ] The generated SQL has been looked at (log or `ToQueryString()`) for new or changed queries.
- [ ] Read-only queries use `AsNoTracking()` and project only the needed columns.
- [ ] Filtering, sorting, and paging happen in the database, before `ToListAsync()`.
- [ ] No queries run inside a loop over other query results (no N+1), and lazy loading is off.
- [ ] Queries that include several collections use `AsSplitQuery()`.
- [ ] Unbounded results are paged (keyset paging for deep pages) or streamed.
- [ ] Queries use parameters, never values concatenated into SQL or baked into expression trees.
- [ ] Bulk updates and deletes use `ExecuteUpdateAsync`/`ExecuteDeleteAsync`; large imports are batched.
- [ ] New filter, join, and sort columns have indexes, and no unused indexes were added.
- [ ] `DbContext` instances are short-lived and never shared between threads.
