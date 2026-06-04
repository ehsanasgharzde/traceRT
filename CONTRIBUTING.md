# Contributing

The project is small on purpose. Two files, one header, one Python script.
Patches that keep it that way are very welcome.

## What I would like help with

- Bug reports with a minimal reproducer (a small `.log` snippet plus the
  command you ran is ideal).
- Cleanups to the parser, especially around multi-threaded traces.
- Extra exporters. The current ones are ASCII, Chrome Trace JSON, and proto3.
  Other formats (FlameGraph folded stacks, OTLP, etc.) would slot in next to
  `export_json` and `export_protobuf` without much surgery.
- Documentation fixes.

## What I would rather not merge

- New runtime dependencies on the C side. `trace.h` is meant to compile
  anywhere with a POSIX `write()` and `clock_gettime()`.
- New required Python dependencies. The analyser uses only the standard
  library on purpose so it runs anywhere with Python 3.
- Large refactors that split the header into multiple files, or split the
  analyser into a package. Both are deliberately single-file.

## Style

- C side: follow the style already in `trace.h`. Plain C with `static inline`
  helpers, no classes, no templates, no allocations on the hot path.
- Python side: standard library only, no third-party formatters in the
  toolchain. Keep functions short and avoid recursion on user data so deep
  traces do not blow the stack.

## Submitting a change

1. Open an issue first if the change is non-trivial. Saves both of us time.
2. One topic per pull request.
3. Run `trace_tree.py` against the sample log in `examples/` (if present)
   before pushing, so we know all three exporters still work.
4. Update the README if you change behaviour or add a flag.

That is it. Thanks for taking the time.
