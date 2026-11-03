# File I/O Performance

How to read and write files as fast as possible in .NET 10: which API to pick, how big the buffers should be, when async helps, how to read one file with many threads, and how to write data safely without slowing down. The short [.NET Performance › File and stream I/O](./dotnet-performance.md#file-and-stream-io) section gives the basics. This page is the full guide. Buffer and span techniques are in [C# › Span, Memory, and pooled buffers](./csharp-performance.md#spant-memoryt-and-pooled-buffers).

---

## The short answer

| You want to… | Use | Notes |
|--------------|-----|-------|
| Read a large file once, start to end, as bytes | `FileStream` with `BufferSize = 0`, `FileOptions.SequentialScan`, and your own 64 KB–1 MB pooled buffer | The fastest single-threaded choice. |
| Read a large file as fast as the machine allows | `File.OpenHandle` + `RandomAccess.Read` from several threads, each reading its own part of the file | Split the file at record boundaries (see [Reading one file with many threads](#reading-one-file-with-many-threads)). |
| Jump around inside a large file | `RandomAccess` with explicit offsets, or a memory-mapped file | No shared file position, so it is safe across threads. |
| Parse a byte stream that arrives in pieces (network, a decompressor) | `System.IO.Pipelines` (`PipeReader`) | Handles records that are split across two reads for you. |
| Read text line by line, simply | `File.ReadLines` or `StreamReader.ReadLine` | Fine when one `string` per line is acceptable. About 3× slower than working on bytes. |
| Read a small file (up to a few MB) whole | `File.ReadAllBytes` / `File.ReadAllText` | Simple and fast. Don't use it for large files. |
| Write a large file | `FileStream` with `BufferSize = 0`, your own 64 KB–1 MB buffer, and `PreallocationSize` when the size is known | Batch small writes into large ones. |
| Replace a file without risk of leaving it half-written | Write to a temporary file, flush it to disk, then `File.Move(temp, path, overwrite: true)` | See [Durable and atomic writes](#durable-and-atomic-writes). |

---

## What actually costs time

Reading a file goes through several layers. Each one can be the bottleneck.

1. **The disk.** A modern NVMe SSD reads several GB per second. A network share or a cloud disk can be 10–100× slower.
2. **The operating system's page cache.** The OS keeps recently used file data in memory. A file that was read recently is served from memory ("warm"). A file that was not is read from disk ("cold"). Warm reads are often 5–20× faster than cold reads.
3. **System calls.** Each `Read` that reaches the OS has a fixed cost. Reading 1 GB in 4 KB pieces means about 260,000 system calls. Reading it in 1 MB pieces means about 1,000.
4. **Copies.** `FileStream` has its own internal buffer. If you also have a buffer, the data is copied twice. `StreamReader` adds a third buffer and converts bytes to `char`.
5. **Decoding.** Turning UTF-8 bytes into .NET `string` (UTF-16) touches every byte and doubles the memory. It is often the most expensive step after the disk.
6. **Allocations.** One `string` per line on a 10-million-line file creates 10 million objects for the garbage collector.

The rest of this page is about removing steps 3–6, and spreading step 1 over several threads.

---

## Measured results

These numbers come from a small test program that counts the lines in a 1 GB log file (11.4 million lines of about 94 bytes). They show the *relative* cost of each approach. Your absolute numbers will differ.

**Machine:** Apple M4 (10 cores), macOS 15.7, internal SSD, .NET SDK 10.0.401, Release build, Workstation GC. Median of 5 runs. The file was in the page cache (warm), so these numbers show CPU and copying cost, not disk speed.

### Reading

| Approach | Time | Throughput | Allocated |
|----------|-----:|-----------:|----------:|
| `RandomAccess.Read`, 10 threads, 1 MB buffers | **39 ms** | **25.8 GiB/s** | 4 KB |
| Memory-mapped file, one span over the whole view | 108 ms | 9.3 GiB/s | 352 B |
| `FileStream` (4 KB internal buffer), 64 KB reads | 116 ms | 8.6 GiB/s | 328 B |
| `RandomAccess.Read`, 1 MB buffer | 117 ms | 8.5 GiB/s | 104 B |
| `FileStream` async (`BufferSize = 0`), 1 MB reads | 118 ms | 8.4 GiB/s | 632 B |
| `FileStream` (`BufferSize = 0`), 64 KB reads | 119 ms | 8.4 GiB/s | 256 B |
| `PipeReader` over `FileStream`, 1 MB buffer | 120 ms | 8.4 GiB/s | 1.2 KB |
| `FileStream` (4 KB internal buffer), 4 KB reads | 184 ms | 5.4 GiB/s | 328 B |
| `File.ReadAllBytes`, then count | 196 ms | 5.1 GiB/s | **1 GB** |
| `StreamReader.ReadLine` | 405 ms | 2.5 GiB/s | **2.2 GB** |
| `File.ReadLines` | 420 ms | 2.4 GiB/s | **2.2 GB** |
| `StreamReader.Read(Span<char>)`, 64 KB | 468 ms | 2.1 GiB/s | 3.5 KB |

### Writing

1 GB written as 11.4 million 94-byte lines. The OS caches writes, so these numbers measure the cost of getting data *to the OS*, not to the disk.

| Approach | Time | Throughput |
|----------|-----:|-----------:|
| Own 1 MB buffer, `PreallocationSize` set | **362 ms** | 2.8 GiB/s |
| Own 1 MB buffer, `FileStream` with `BufferSize = 0` | 367 ms | 2.7 GiB/s |
| `PipeWriter` over `FileStream`, flush every 1 MB | 373 ms | 2.7 GiB/s |
| `StreamWriter` (64 KB) over `FileStream` with `BufferSize = 0` | 387 ms | 2.6 GiB/s |
| `FileStream` with 64 KB internal buffer, one `Write` per line | 394 ms | 2.5 GiB/s |
| `FileStream` with 4 KB internal buffer, one `Write` per line | 472 ms | 2.1 GiB/s |
| `StreamWriter` with defaults | 496 ms | 2.0 GiB/s |

### What the numbers tell us

- **Buffer size matters more than the API.** Going from 4 KB reads to 64 KB reads made `FileStream` about 60% faster. Going further, to 1 MB, changed nothing on this machine.
- **`FileStream`, `RandomAccess`, async `FileStream`, and `PipeReader` are all about equally fast** once the buffer is large. Pick the one whose programming model fits your code.
- **Memory-mapped files were only about 10% faster** for a simple start-to-end scan, and they bring extra risks (see [Memory-mapped files](#memory-mapped-files)).
- **Several threads reading different parts of the file were 3× faster** than the best single thread. This is the biggest win available for a large file.
- **Working with `char` and `string` is 3–4× slower than working with bytes.** The cost is decoding UTF-8 to UTF-16, not the file access itself. `ReadLine` also allocates the whole file twice over (2.2 GB for a 1 GB file).
- **For writes, the main cost is the number of `Write` calls**, not the API. Collecting lines into one large buffer and writing it in one call is the fastest approach.

To repeat a measurement on your own machine and data, use BenchmarkDotNet as described in [.NET Performance › Measuring](./dotnet-performance.md#measuring). Measure with a **cold cache** as well when your real workload reads files that were not read recently: on Linux, run `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches` before each run. On macOS, run `sudo purge`.

---

## Opening files

`FileStreamOptions` collects every option in one place. Prefer it over the long `FileStream` constructors.

```csharp
var options = new FileStreamOptions
{
    Mode = FileMode.Open,
    Access = FileAccess.Read,
    Share = FileShare.Read,
    Options = FileOptions.SequentialScan,
    BufferSize = 0, // no internal buffer: we read into our own large buffer
};
using var file = new FileStream(path, options);
```

### Buffer size

**Do**
- Set `BufferSize = 0` when every `Read` or `Write` you make is already large (64 KB or more). The internal buffer would only add a copy.
- Keep the internal buffer when you make many small reads or writes, and raise it to 64 KB. Small calls are then served from memory.
- Use 64 KB as a starting point for your own buffer. Try 256 KB and 1 MB on slow or network storage, where fewer, larger requests help more.
- Rent large buffers from `ArrayPool<byte>.Shared` and return them in a `finally` block. A new 1 MB array goes to the Large Object Heap every time (see [.NET › The garbage collector](./dotnet-performance.md#the-garbage-collector)).

**Don't**
- Leave the default 4 KB buffer and read in small pieces. On the test machine that made reads about 60% slower (37% less throughput).
- Set the internal buffer *and* your own buffer to large sizes. The data is copied twice for no gain.

### FileOptions flags

| Flag | Effect | When to use |
|------|--------|-------------|
| `SequentialScan` | Tells the OS you will read from start to end, so it reads ahead more. On Windows it is a `CreateFile` flag. On Linux it calls `posix_fadvise(POSIX_FADV_SEQUENTIAL)`. On macOS it has no effect. | Every start-to-end read. It costs nothing. |
| `RandomAccess` | Tells the OS not to read ahead. | Reads that jump around a large file. |
| `Asynchronous` | Opens the file for real asynchronous I/O on Windows. On Linux and macOS it has no effect on performance (see [Sync or async](#sync-or-async)). | Async code on Windows. |
| `WriteThrough` | Each write goes to the disk before it returns (`O_SYNC` on Unix). Very slow. | Almost never. Flush once at the end instead (see [Durable and atomic writes](#durable-and-atomic-writes)). |
| `DeleteOnClose` | Deletes the file when the last handle is closed. | Temporary files. |

### Sharing and locking

**Do**
- Open read-only files with `FileShare.Read`. On Windows, opening a file for reading *without* `FileShare.Write` also lets .NET cache the file's length, which makes repeated `Length` calls much cheaper.
- Open log files that another process is still writing with `FileShare.ReadWrite`. Otherwise the open fails on Windows.

**Don't**
- Rely on `FileShare` to protect files on Linux and macOS. File locks there are only advisory: other programs can ignore them.

---

## Reading a file start to end

### Work on bytes, not strings

Most log formats mark the end of a record with a byte (`\n`) and use ASCII or UTF-8 for the parts you search for. You can find record boundaries, timestamps, and keywords on the raw bytes, and decode only the parts you keep.

**Do**
- Search bytes with span methods: `IndexOf`, `IndexOfAny`, `LastIndexOf`, `Count`, and `SearchValues<byte>` for larger sets. They use SIMD instructions and process 16–64 bytes per step.
- Decode to `string` only the fields you store, with `Encoding.UTF8.GetString(span)` or `Utf8.ToUtf16` into a pooled `char` buffer.
- Parse numbers and dates straight from UTF-8 with `Utf8Parser`, or with `int.TryParse(ReadOnlySpan<byte>, ...)` and the other `IUtf8SpanParsable` methods.

**Don't**
- Call `ReadLine` on a hot path. It allocates a `string` for every line.
- Use `File.ReadAllLines`. It holds every line in memory at once.

### Handling records that cross a buffer boundary

A fixed-size buffer will often end in the middle of a record. Move the unfinished part to the start of the buffer and read more data after it.

```csharp
public delegate void LineHandler(ReadOnlySpan<byte> line);

public static void ForEachLine(string path, LineHandler onLine)
{
    using var file = new FileStream(path, new FileStreamOptions
    {
        Options = FileOptions.SequentialScan,
        BufferSize = 0,
    });

    byte[] buffer = ArrayPool<byte>.Shared.Rent(256 * 1024);
    try
    {
        int carried = 0;
        while (true)
        {
            int read = file.Read(buffer.AsSpan(carried));
            int filled = carried + read;
            Span<byte> data = buffer.AsSpan(0, filled);

            if (read == 0)
            {
                if (filled > 0)
                {
                    onLine(data); // the last line has no '\n'
                }

                return;
            }

            int start = 0;
            int newLine;
            while ((newLine = data[start..].IndexOf((byte)'\n')) >= 0)
            {
                onLine(data.Slice(start, newLine));
                start += newLine + 1;
            }

            carried = filled - start;
            if (carried == buffer.Length)
            {
                // One line is longer than the whole buffer: grow it.
                byte[] larger = ArrayPool<byte>.Shared.Rent(buffer.Length * 2);
                buffer.CopyTo(larger, 0);
                ArrayPool<byte>.Shared.Return(buffer);
                buffer = larger;
            }
            else
            {
                data[start..].CopyTo(buffer); // overlapping copy is safe with spans
            }
        }
    }
    finally
    {
        ArrayPool<byte>.Shared.Return(buffer);
    }
}
```

Lines from Windows files end with `\r\n`. Trim a trailing `\r` inside `onLine` if your format allows it.

`System.IO.Pipelines` does this bookkeeping for you (see [Pipelines](#systemiopipelines)). The hand-written loop is shorter to debug and has no async overhead, so prefer it for synchronous file parsing.

### Reading a fixed number of bytes

**Do**
- Use `stream.ReadExactly(buffer)` when you need exactly N bytes, for example a file header or a fixed-size record. It throws if the file ends early.
- Use `stream.ReadAtLeast(buffer, minimum, throwOnEndOfStream: false)` when you need *at least* N bytes but are happy to take more.

**Don't**
- Assume one `Read` call fills the buffer. It may return fewer bytes, even in the middle of a file, especially on pipes and network shares.

```csharp
// ❌ May read only part of the header.
stream.Read(header);

// ✅ Loops until the header is full, or throws at end of file.
stream.ReadExactly(header);
```

### Text with `StreamReader`

When you really need text (for example, a format in UTF-16, or code that needs `string`), make `StreamReader` as cheap as possible.

**Do**
- Pass the encoding when you know it, and turn off detection: `new StreamReader(stream, Encoding.UTF8, detectEncodingFromByteOrderMarks: false)`.
- Give the reader a large buffer (`bufferSize: 64 * 1024`), and give the underlying `FileStream` `BufferSize = 0`.
- Read into a pooled `char` buffer with `Read(Span<char>)` when you don't need a `string` per line.

---

## Sync or async

Async file I/O does **not** make a single read faster. It frees the thread while the read is waiting, which matters when many operations run at once (a web server handling many requests). For a batch job that reads one file at a time, sync is as fast or faster.

How it works differs by platform:

| Platform | What async file I/O does |
|----------|--------------------------|
| Windows, file opened with `FileOptions.Asynchronous` | Real asynchronous I/O (overlapped I/O). The thread is free while the disk works. |
| Windows, file opened without it | Each `ReadAsync` runs a blocking read on a thread-pool thread. |
| Linux and macOS | Always a blocking read on a thread-pool thread. The OS has no general async file API that .NET uses. |

**Do**
- Use sync reads in CPU-bound batch tools that process files one after another.
- Use async reads in services, where blocking threads would starve the thread pool. Open the file with `FileOptions.Asynchronous` so Windows gets real async I/O.
- Read in large pieces when you use async. Each `await` has a fixed cost, so 1 MB reads are much cheaper per byte than 4 KB reads.

**Don't**
- Mix sync and async calls on the same `FileStream`. On Windows, a sync call on a stream opened for async I/O waits for an async operation underneath.
- Expect async to help on Linux or macOS for a single file. It adds a thread-pool hop to each read.

---

## Reading one file with many threads

One thread can only scan so fast. On the test machine, splitting a 1 GB file into 10 parts and reading them in parallel was 3× faster than the best single thread. On cold data, parallel reads also keep an NVMe SSD's internal queues busy, which single-threaded reads usually don't.

`RandomAccess` is designed for this. Every call takes an explicit file offset, so there is no shared position and no locking between threads.

```csharp
public static long CountLinesInParallel(string path)
{
    using SafeFileHandle handle = File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.Read);
    long length = RandomAccess.GetLength(handle);
    int parts = Environment.ProcessorCount;
    long partSize = (length + parts - 1) / parts;
    long total = 0;

    Parallel.For(0, parts, part =>
    {
        long offset = part * partSize;
        long end = Math.Min(length, offset + partSize);
        byte[] buffer = ArrayPool<byte>.Shared.Rent(1024 * 1024);
        try
        {
            long count = 0;
            while (offset < end)
            {
                int toRead = (int)Math.Min(buffer.Length, end - offset);
                int read = RandomAccess.Read(handle, buffer.AsSpan(0, toRead), offset);
                if (read == 0)
                {
                    break;
                }

                count += buffer.AsSpan(0, read).Count((byte)'\n');
                offset += read;
            }

            Interlocked.Add(ref total, count);
        }
        finally
        {
            ArrayPool<byte>.Shared.Return(buffer);
        }
    });

    return total;
}
```

Counting newlines works with arbitrary split points. Parsing records does not: a split point will usually fall inside a record. Move each split point forward to the byte after the next record boundary before you start:

```csharp
private static long NextRecordStart(SafeFileHandle handle, long guess, long length)
{
    Span<byte> probe = stackalloc byte[4096];
    while (guess < length)
    {
        int read = RandomAccess.Read(handle, probe, guess);
        if (read == 0)
        {
            break; // the file became shorter than expected
        }

        int newLine = probe[..read].IndexOf((byte)'\n');
        if (newLine >= 0)
        {
            return guess + newLine + 1;
        }

        guess += read;
    }

    return length;
}
```

**Do**
- Use this only for formats where you can find a record boundary from any position, such as one record per line. Formats with multi-line records, or records that depend on earlier ones, need a sequential pass (or a first pass that records boundaries).
- Keep results in file order when the order matters: give each part its own output list and join them at the end.
- Limit the number of parts to the number of cores. More threads than cores only add switching cost.

**Don't**
- Share one `FileStream` between threads. Its position is shared, and it is not thread-safe.
- Expect a gain on a spinning hard disk. Parallel reads make the head jump back and forth, which is slower than one sequential read.

### Scatter/gather

`RandomAccess.Read(handle, IReadOnlyList<Memory<byte>>, offset)` and the matching `Write` overload fill or write several buffers in one system call. Use them when your data is naturally split into separate buffers, for example a fixed-size header and a body. Microsoft measured about 8% faster writes on Linux, and no gain on Windows.

---

## Memory-mapped files

A memory-mapped file makes the file look like an array in memory. The OS loads pages from disk when you touch them.

**Do**
- Consider memory-mapped files for **random access to large files that are read many times**, for example an index you search repeatedly. The page cache then acts as your cache, with no copying.
- Get a span over the view through `SafeMemoryMappedViewHandle.AcquirePointer` (in an `unsafe` block), and always call `ReleasePointer` in a `finally` block.
- Map large files in windows of a few hundred MB if the process might run as 32-bit, or if you only need part of the file.

**Don't**
- Use `MemoryMappedViewAccessor.Read<T>` or `ReadArray` per value in a hot loop. Each call does bounds checks and is much slower than a span.
- Pick memory-mapped files just for a start-to-end scan. In our test it was only about 10% faster than `FileStream` with a large buffer, and it is harder to use correctly.
- Map files that another process might shorten while you read them. On Linux and macOS, touching a page that no longer exists kills the process with `SIGBUS`. On Windows, a mapped file cannot be shortened, which blocks the other program instead.
- Map files on network shares. A network error becomes a crash instead of an `IOException`.

---

## System.IO.Pipelines

`PipeReader` manages a chain of buffers for you. You ask for data, look at as much as you need, and tell it how far you got. Unfinished records stay in the pipe until more data arrives.

```csharp
await using var file = new FileStream(path, new FileStreamOptions
{
    Options = FileOptions.Asynchronous | FileOptions.SequentialScan,
    BufferSize = 0,
});
var reader = PipeReader.Create(file, new StreamPipeReaderOptions(bufferSize: 1024 * 1024, minimumReadSize: 64 * 1024));

while (true)
{
    ReadResult result = await reader.ReadAsync(cancellationToken);
    ReadOnlySequence<byte> buffer = result.Buffer;

    while (TryReadLine(ref buffer, out ReadOnlySequence<byte> line))
    {
        Process(line);
    }

    // Everything before buffer.Start is consumed; everything up to buffer.End has been looked at.
    reader.AdvanceTo(buffer.Start, buffer.End);
    if (result.IsCompleted)
    {
        break;
    }
}

await reader.CompleteAsync();
```

**Do**
- Use Pipelines when data arrives in pieces you don't control: network streams, decompression streams, or a producer and consumer running at different speeds.
- Set `bufferSize` and `minimumReadSize` on `StreamPipeReaderOptions`. The default 4 KB is too small for files.
- Use `SequenceReader<byte>` to search a `ReadOnlySequence<byte>` that spans several segments.

**Don't**
- Add Pipelines to a synchronous file parser just for speed. In our test it was as fast as a plain `FileStream`, with more code.

---

## Whole-file helpers

`File.ReadAllBytes`, `File.ReadAllText`, `File.ReadAllLines`, and their `Write` counterparts are the simplest APIs and are fast for small files.

**Do**
- Use them for configuration files, small fixtures, and other files up to a few MB.
- Use `File.ReadLines` (not `ReadAllLines`) for simple line-by-line work on medium files. It reads lazily.

**Don't**
- Use them on log files, exports, or anything that can grow. The whole file must fit in memory, and every byte array of 85 KB or more goes to the Large Object Heap.

---

## Writing files

### Batch small writes

Every `Write` call has a fixed cost, even when it only copies into a buffer. Collect output in a large buffer and write it in one call.

**Do**
- Format straight into a byte buffer. `Utf8.TryWrite` writes interpolated strings as UTF-8, and `IUtf8SpanFormattable` types (numbers, `DateTime`, `Guid`, and more) format themselves into a `Span<byte>` without creating a `string`.
- Write the buffer when it is nearly full, then reuse it.

```csharp
// ❌ Creates a string per line, then copies it through StreamWriter's char buffer.
writer.WriteLine($"{evt.Timestamp:O} {evt.Level} {evt.Message}");

// ✅ Formats the line straight into the output buffer as UTF-8.
int written;
while (!Utf8.TryWrite(buffer.AsSpan(position), $"{evt.Timestamp:O} {evt.Level} {evt.Message}\n", out written))
{
    // The line didn't fit: write what is buffered, then try again into the empty buffer.
    // (Real code must also handle a single line that is larger than the whole buffer.)
    file.Write(buffer, 0, position);
    position = 0;
}

position += written;
```

- Use `StreamWriter` when your code produces `string` values anyway. Give it a 64 KB buffer, pass `new UTF8Encoding(encoderShouldEmitUTF8Identifier: false)` to avoid writing a byte-order mark, and give the underlying `FileStream` `BufferSize = 0`.
- Use `PipeWriter` (`GetSpan`, `Advance`, `FlushAsync`) in async code that writes many small pieces.

**Don't**
- Call `Flush()` after every line. It forces a system call each time and removes the benefit of buffering.
- Use `StreamWriter.AutoFlush = true` for bulk output.

### Preallocate when you know the size

`PreallocationSize` reserves disk space when the file is created. The file system then doesn't have to grow the file in many small steps, and you get an `IOException` up front if the disk is too full, instead of halfway through.

```csharp
using var output = new FileStream(path, new FileStreamOptions
{
    Mode = FileMode.Create,
    Access = FileAccess.Write,
    BufferSize = 0,
    PreallocationSize = expectedLength,
});
```

Microsoft measured 20% faster writes on Windows and more than 50% faster on Linux for a 100 MB file. On our macOS test the gain was small (about 1%). It only works with `FileMode.Create` or `CreateNew` and `FileAccess.Write`. The final file length is the number of bytes you actually wrote. On Linux and macOS, unused reserved space stays allocated to the file until it is deleted, so don't over-reserve by a large amount.

### Durable and atomic writes

A finished `Write` or `Flush()` only means the OS has the data in memory. A power cut can still lose it. A crash in the middle of writing leaves a half-written file.

**Do**
- Call `Flush(flushToDisk: true)` once, at the end, when the data must survive a power cut. It calls `FlushFileBuffers` on Windows, `fsync` on Linux, and `fcntl(F_FULLFSYNC)` on macOS (which also empties the drive's own cache).
- Replace important files atomically: write to a temporary file in the **same folder**, flush it to disk, then rename it over the target. Readers then see either the old file or the new file, never a mix.

```csharp
string temp = path + ".tmp";
using (var file = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None, bufferSize: 0))
{
    WriteContent(file);
    file.Flush(flushToDisk: true);
}

File.Move(temp, path, overwrite: true);
```

**Don't**
- Use `FileOptions.WriteThrough` for bulk output. Every write then waits for the disk.
- Write the temporary file to a different volume, such as the system temp folder. A rename between volumes becomes a copy, which is not atomic.

---

## Compressed files

Reading compressed data is usually limited by the CPU doing the decompression, not by the disk.

**Do**
- Wrap the decompression stream in a large buffer: read from `GZipStream` or `BrotliStream` in 64 KB or larger pieces, and give the underlying `FileStream` `BufferSize = 0` and `SequentialScan`.
- Decompress several archive entries in parallel when you have many. One stream can only use one core.
- Pick `CompressionLevel.Fastest` for output that is read soon after (see [.NET › File and stream I/O](./dotnet-performance.md#file-and-stream-io)).

**Don't**
- Read a compressed stream byte by byte, or with `StreamReader` defaults, on a hot path.

---

## Many small files

When you process thousands of small files, opening each file costs more than reading it.

**Do**
- Enumerate with `Directory.EnumerateFiles` (lazy) instead of `Directory.GetFiles` (builds the full list first).
- Use `EnumerationOptions` (`RecurseSubdirectories`, `IgnoreInaccessible`, `AttributesToSkip`) instead of filtering afterwards.
- Use `FileSystemEnumerable<T>` when you need size or dates for every file. It reads them from the directory entry, without opening each file or creating a `FileInfo`.
- Process files in parallel with a bounded degree of parallelism (`Parallel.ForEachAsync` with `MaxDegreeOfParallelism`). Small-file work is dominated by waiting on the file system, so parallelism helps even on few cores.

**Don't**
- Call `File.Exists` right before opening a file you expect to exist. Open it and handle `FileNotFoundException`. The check costs an extra system call, and the file can disappear between the check and the open anyway.
- Create a `FileInfo` per file just to read its length.

---

## Review checklist

- [ ] Large files are streamed, not loaded with `ReadAllBytes`, `ReadAllText`, or `ReadAllLines`.
- [ ] Reads and writes are 64 KB or larger, or go through an internal buffer of that size. No double buffering.
- [ ] Start-to-end reads use `FileOptions.SequentialScan`.
- [ ] Large buffers are rented from `ArrayPool<byte>.Shared` and returned in a `finally` block.
- [ ] Hot paths parse bytes and decode only the fields they keep. No `ReadLine` per record.
- [ ] Fixed-size reads use `ReadExactly` or `ReadAtLeast`, not a single `Read`.
- [ ] Async file I/O is used for concurrency, not speed. Files read async on Windows are opened with `FileOptions.Asynchronous`.
- [ ] Parallel reads use `RandomAccess` on one `SafeFileHandle`, split at record boundaries.
- [ ] Memory-mapped files are used only for repeated random access, never on files that can shrink or live on network shares.
- [ ] Writes are batched. No `Flush()` or `AutoFlush` per line.
- [ ] Files that must not end up half-written are written to a temporary file in the same folder, flushed to disk, then moved over the target.
- [ ] Any "this is faster" claim comes with a benchmark, run with both a warm and a cold page cache when cold reads are realistic.
