# trace.h

A tiny header-only tracer for C and C++ projects. Drop it into any code base,
sprinkle a few macros at the top of the functions you care about, run the
program, and you get a log file you can either visualise on a flame-graph
timeline or fold into an aggregated call tree.

The whole thing is two files:

```
trace.h         the tracer itself (single header, no dependencies)
trace_tree.py   post-processor: ASCII tree, Chrome Trace JSON, or proto3
```

That is the entire project.

---

## Why this exists

If you have ever tried to figure out the runtime call graph of a moderately
sized C++ project, you know the options are usually bad:

- gprof and perf need symbols, a profiling build, and a clean exit. They give
  you a flat hot list, not an ordered call tree.
- A real debugger (gdb, lldb) is great for one stop, terrible for watching
  what hundreds of calls do across seconds of runtime.
- DTrace, eBPF, LTTng, etc. all work, but they need root, kernel headers, or
  a Linux distribution that cooperates. Most embedded SDKs and SITL setups do
  not.
- printf debugging works, but writing free-form printfs and grepping them
  back into a tree is a manual job every time.

`trace.h` is the printf-debugging version, but structured. Two macros wrap
the functions you care about, every call writes one line to a log file, and
`trace_tree.py` reconstructs the call tree (or a Chrome timeline) from the
log afterwards. No build system changes, no profiler, no root. It works in
SITL, in unit tests, on embedded targets with a writable filesystem, anywhere
POSIX `write()` and `clock_gettime()` exist.

It is good for real-time code bases because:

- **Zero allocations on the hot path.** `snprintf` into a stack buffer,
  one `write()` syscall, done. No `malloc`, no `iostream`, no locks.
- **Per-call-site rate limiting.** A 400Hz loop will not flood the log.
  Each macro has its own static timer.
- **Safe to fail.** If the log file cannot be opened the tracer silently
  becomes a no-op. It cannot crash the host process.
- **Compiles to nothing when disabled.** `TRACE_PROC_ENABLED=0` collapses
  both macros to `do {} while (0)`. Use it on production builds.
- **Survives partial logs.** The parser tolerates dropped events from the
  rate limiter, so you do not have to capture a perfectly clean session to
  get a useful tree.
- **No new dependencies on the C side.** Only `unistd.h`, `fcntl.h`,
  `time.h`, `stdio.h`. Works in any reasonable POSIX environment.

---

## How it works

`trace.h` writes two kinds of lines to a single log file:

```
[PROC] >> ClassName::method 12345
[PROC] << ClassName::method 12347
[DATA] signal_name=3.1416 @12345
```

The number at the end is a monotonic millisecond timestamp.

`trace_tree.py` reads that log and produces one of three outputs:

1. **`regular`** — an ASCII call tree plus a per-signal data summary, written
   to `<logfile>_trace.txt`.
2. **`json`** — Chrome Trace Format, written to `<logfile>_trace.json`. Drop
   it into any flame-graph timeline viewer.
3. **`protobuf`** — a proto3 binary (`<logfile>_trace.pb`) plus the schema
   file (`<logfile>_trace.proto`) so the binary is self-documenting.

---

## Integrating it into another code base

### 1. Copy the header

Put `trace.h` somewhere on the include path. A common spot is a generic
utilities directory like `include/` or `libraries/common/`.

### 2. Include it where you want to trace

```cpp
#include "trace.h"
```

If you want data tracing too, set the flag before the include:

```cpp
#define TRACE_DATA_ENABLED 1
#include "trace.h"
```

### 3. Wrap functions

```cpp
void Controller::update()
{
    TRACE_ENTER();

    if (early_exit_condition) {
        TRACE_EXIT();
        return;
    }

    // ... normal work ...

    TRACE_DATA("roll_demand_deg", roll_demand);
    TRACE_DATA("airspeed_m_s",    airspeed);

    TRACE_EXIT();
}
```

That is the entire integration. Run the program, the log file will appear
at the configured path.

### 4. Build-time control

If you want to compile the tracer out completely without editing the header,
pass build flags:

```
-DTRACE_PROC_ENABLED=0 -DTRACE_DATA_ENABLED=0
```

Both macros collapse to nothing under the preprocessor.

---

## Where to edit the output file path

The log path is defined at the top of `trace.h`:

```c
#ifndef TRACE_LOG_PATH
#  define TRACE_LOG_PATH  "/tmp/trace.log"
```

Two ways to change it:

- **Edit `trace.h` directly** — change the string literal above.
- **Override from the build system** — pass `-DTRACE_LOG_PATH='"/your/path/here.log"'`
  on the compiler command line. The `#ifndef` guard makes sure the build
  flag wins over the default.

The file is opened once per process with `O_WRONLY | O_CREAT | O_TRUNC`,
so each new run starts a fresh log.

---

## Running the analyser

```
python3 trace_tree.py <logfile> [options]
```

Options:

| Flag                  | What it does                                              |
|-----------------------|-----------------------------------------------------------|
| `--no-proc`           | Skip the call-tree section (regular format only).         |
| `--no-data`           | Skip the data-summary section (regular format only).      |
| `--format regular`    | ASCII tree + data summary written to `<log>_trace.txt`. Default. |
| `--format json`       | Chrome Trace JSON written to `<log>_trace.json`.          |
| `--format protobuf`   | Proto3 binary + schema (`<log>_trace.pb` and `<log>_trace.proto`). |

Examples:

```bash
# Default: write ASCII tree to skywalker_x8_trace.txt
python3 trace_tree.py skywalker_x8.log

# Skip the data section
python3 trace_tree.py skywalker_x8.log --no-data

# Produce a Chrome Trace JSON for Perfetto / Speedscope / chrome://tracing
python3 trace_tree.py skywalker_x8.log --format json

# Produce a proto3 binary plus its schema
python3 trace_tree.py skywalker_x8.log --format protobuf
```

---

## Visualising the output

The JSON and protobuf outputs were used with the following tools.

### Perfetto UI — `https://ui.perfetto.dev`

Drag the `<log>_trace.json` file onto the page. You get a normal flame-graph
timeline with begin/end slices for every traced function, and a slice
flamegraph view for aggregated time per function.

![Perfetto timeline view](https://github.com/ehsanasgharzde/traceRT/images/1.png)

![Perfetto slice flamegraph](https://github.com/ehsanasgharzde/traceRT/images/2.png)

### omute.net JSON editor — `https://omute.net/editor`

Useful when you want to walk the raw JSON structure. The editor expands the
`traceEvents` array as a node graph, which is handy for spot-checking that
the begin/end pairs make sense.

![omute.net JSON node graph](https://github.com/ehsanasgharzde/traceRT/images/4.png)

### protoc

If you took the protobuf route:

```bash
protoc --decode=TraceReport <log>_trace.proto < <log>_trace.pb
```

You get a human-readable text dump of the aggregated tree plus the data
series. Any proto3 library in any language can read the same `.pb` using
the bundled `.proto`.

---

## What gets dropped, and why

The parser prints two counters on every run:

```
N unmatched exit(s) skipped
M open enter(s) at EOF
```

`unmatched exit(s)` are `<<` lines that did not match any open `>>`. This
usually means the program was attached partway through a function call.

`open enter(s) at EOF` are `>>` lines whose matching `<<` was suppressed
by the rate limiter, or whose function was still executing when the log
was closed. The tree still includes the node, the timing just is not
counted.

If `M` is very high your rate limit is too aggressive. Lower
`TRACE_PROC_RATE_MS` in `trace.h` or override it with a build flag.

---

## Limits and gotchas

- `__PRETTY_FUNCTION__` produces long strings on C++; the emit buffer is
  128 bytes. Function signatures longer than ~60 characters get truncated
  but the normaliser in `trace_tree.py` only looks at the part before `(`,
  so this is fine in practice.
- Multi-threaded programs: each thread writes to the same fd. POSIX
  guarantees the small `write()` is atomic for writes under `PIPE_BUF`,
  which is plenty for a single trace line. Threads still all show up on
  the same track in the JSON output because the encoder hard-codes `tid`
  to 1; patch `_parse_raw_events` if you need per-thread tracks.
- Timestamps are milliseconds. If you need finer resolution change
  `_trace_now_ms` to return microseconds and update the Chrome export
  scaling (`* 1000` becomes `* 1`).

---

## License

The traceRT licensed under MIT, use freely!

- [Overview of license](https://github.com/ehsanasgharzde/traceRT/LICENSE)
