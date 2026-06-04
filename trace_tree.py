#!/usr/bin/env python3
"""
trace_tree.py
Parses [PROC] and [DATA] lines produced by trace.h and produces one of:
  regular   ASCII call tree + data summary written to <logfile>_trace.txt
  json      Chrome Trace Format file (<logfile>_trace.json)
  protobuf  proto3 binary (<logfile>_trace.pb) plus schema (<logfile>_trace.proto)
"""

import sys
import re
import argparse
from collections import deque


# Strip return type and parameter list from a __PRETTY_FUNCTION__ string.
# "void ModeAuto::update()"  -> "ModeAuto::update"
def normalise(pretty: str) -> str:
    name  = pretty.split('(')[0].strip()
    parts = name.split()
    return parts[-1] if parts else pretty


# Aggregated node in the call tree. One per unique function name per parent.
class CallNode:
    __slots__ = ('name', 'calls', 'total_ms', 'children')

    def __init__(self, name: str):
        self.name     = name
        self.calls    = 0
        self.total_ms = 0
        self.children = {}

    def get_or_create_child(self, name: str) -> 'CallNode':
        if name not in self.children:
            self.children[name] = CallNode(name)
        return self.children[name]

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls > 0 else 0.0


# Rolling statistics for one [DATA] signal.
class DataSeries:
    __slots__ = ('name', 'count', 'total', 'minimum', 'maximum', 'last')

    def __init__(self, name: str):
        self.name    = name
        self.count   = 0
        self.total   = 0.0
        self.minimum =  float('inf')
        self.maximum = -float('inf')
        self.last    = 0.0

    def add(self, val: float):
        self.count += 1
        self.total += val
        self.last   = val
        if val < self.minimum: self.minimum = val
        if val > self.maximum: self.maximum = val

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count > 0 else 0.0


# Line patterns emitted by trace.h.
_PROC_RE = re.compile(
    r'\[PROC\]\s+'
    r'(>>|<<)\s+'
    r'(.+?)'
    r'\s+(\d+)'
    r'\s*$'
)

_DATA_RE = re.compile(
    r'\[DATA\]\s+'
    r'([\w.]+)'
    r'=([-+]?\d*\.?\d+)'
    r'\s+@(\d+)'
)


# Single pass over the log. Builds the aggregated call tree and the data map.
# Returns: root, data_map, proc_lines, data_lines, skipped, dropped
#   skipped : << exits that did not match any open enter
#   dropped : >> enters still open at EOF (rate-limit artefacts)
def parse(filename: str):
    root         = CallNode("ROOT")
    call_stack   = []
    parent_stack = [root]
    data_map     = {}
    proc_lines   = 0
    data_lines   = 0
    skipped      = 0

    try:
        with open(filename, 'r', errors='replace') as fh:
            for line in fh:

                mp = _PROC_RE.search(line)
                if mp:
                    proc_lines += 1
                    direction  = mp.group(1)
                    fname      = normalise(mp.group(2).strip())
                    ts         = int(mp.group(3))

                    if direction == '>>':
                        parent = parent_stack[-1]
                        node   = parent.get_or_create_child(fname)
                        node.calls += 1
                        call_stack.append((fname, ts, node))
                        parent_stack.append(node)

                    elif direction == '<<':
                        matched = False
                        # Pop back to the most recent matching enter to
                        # tolerate dropped events from rate limiting.
                        for i in range(len(call_stack) - 1, -1, -1):
                            if call_stack[i][0] == fname:
                                _, entry_ts, node = call_stack.pop(i)
                                elapsed = ts - entry_ts
                                if elapsed >= 0:
                                    node.total_ms += elapsed
                                while len(parent_stack) > len(call_stack) + 1:
                                    parent_stack.pop()
                                matched = True
                                break
                        if not matched:
                            skipped += 1
                    continue

                md = _DATA_RE.search(line)
                if md:
                    data_lines += 1
                    vname = md.group(1)
                    val   = float(md.group(2))
                    if vname not in data_map:
                        data_map[vname] = DataSeries(vname)
                    data_map[vname].add(val)

    except FileNotFoundError:
        print(f"[ERROR] File not found: {filename}")
        sys.exit(1)

    dropped = len(call_stack)
    return root, data_map, proc_lines, data_lines, skipped, dropped


# Iterative BFS over the tree. Counts unique function names.
# Recursion is avoided so very deep traces do not blow the Python stack.
def _count_unique(root: CallNode) -> int:
    seen_names  = set()
    seen_ids    = set()
    queue       = deque(root.children.values())

    while queue:
        node = queue.popleft()
        nid  = id(node)
        if nid in seen_ids:
            continue
        seen_ids.add(nid)
        seen_names.add(node.name)
        queue.extend(node.children.values())

    return len(seen_names)


# Iterative pre-order walk. Yields (node, prefix, is_last) for ASCII drawing.
def _iter_tree(root: CallNode):
    seen_ids = set()

    stack = []
    children = sorted(root.children.values(), key=lambda n: n.calls, reverse=True)
    for i, child in enumerate(children):
        stack.append((child, "", i == len(children) - 1))
    stack.reverse()

    while stack:
        node, prefix, is_last = stack.pop()
        nid = id(node)
        if nid in seen_ids:
            continue
        seen_ids.add(nid)

        yield node, prefix, is_last

        child_prefix = prefix + ("   " if is_last else "|  ")
        children     = sorted(node.children.values(),
                               key=lambda n: n.calls, reverse=True)
        for i, child in enumerate(reversed(children)):
            original_index = len(children) - 1 - i
            child_is_last  = (original_index == len(children) - 1)
            stack.append((child, child_prefix, child_is_last))


# Build the ASCII call-tree section as a list of lines.
def build_proc_tree_lines(root: CallNode):
    lines = []
    top_level = sorted(root.children.values(),
                       key=lambda n: n.calls, reverse=True)

    if not top_level:
        lines.append("[No [PROC] trace lines found in file]")
        lines.append("")
        lines.append("Checklist:")
        lines.append("  1.  TRACE_PROC_ENABLED 1 in trace.h (or build flags)")
        lines.append("  2.  TRACE_ENTER() / TRACE_EXIT() placed in target functions")
        lines.append("  3.  Program output captured to a log file")
        return lines

    total_calls  = sum(n.calls for n in top_level)
    total_unique = _count_unique(root)

    lines.append("=" * 62)
    lines.append("  Procedure Call Tree")
    lines.append(f"  {total_calls} top-level call(s)  |  {total_unique} unique function(s)")
    lines.append("=" * 62)
    lines.append("")

    for node, prefix, is_last in _iter_tree(root):
        connector = "+- " if is_last else "+- "
        timing    = f"  [{node.avg_ms:.1f}ms avg x{node.calls}]"
        lines.append(f"{prefix}{connector}{node.name}{timing}")

    lines.append("")
    lines.append("-" * 62)
    lines.append("Legend:  +- child call    [Xms avg xN]  avg duration + count")
    return lines


# Build the per-signal data summary section as a list of lines.
def build_data_summary_lines(data_map: dict):
    lines = []
    if not data_map:
        lines.append("[No [DATA] trace lines found in file]")
        lines.append("")
        lines.append("Checklist:")
        lines.append("  1.  TRACE_DATA_ENABLED 1 in trace.h (or build flags)")
        lines.append("  2.  TRACE_DATA(\"name\", value) added to target functions")
        return lines

    lines.append("=" * 62)
    lines.append("  Data Flow Summary")
    lines.append(f"  {len(data_map)} variable(s) observed")
    lines.append("=" * 62)
    lines.append("")

    name_w = max(max(len(s.name) for s in data_map.values()), 16)
    header = (f"  {'Variable':<{name_w}}  {'Samples':>8}"
              f"  {'Min':>12}  {'Mean':>12}  {'Max':>12}  {'Last':>12}")
    lines.append(header)
    lines.append("  " + "-" * (name_w + 56))

    for series in sorted(data_map.values(), key=lambda s: s.name):
        lines.append(f"  {series.name:<{name_w}}  {series.count:>8}"
                     f"  {series.minimum:>12.4f}  {series.mean:>12.4f}"
                     f"  {series.maximum:>12.4f}  {series.last:>12.4f}")

    lines.append("")
    lines.append("-" * 62)
    return lines


# Companion schema file written next to every protobuf binary so the result
# is always self-documenting.
_PROTO_SCHEMA = '''\
syntax = "proto3";

// Companion schema for trace_tree.py --format protobuf output.
// Decode: protoc --decode=TraceReport <basename>_trace.proto < <basename>_trace.pb

message CallNode {
  string            name     = 1;
  uint32            calls    = 2;
  double            avg_ms   = 3;
  repeated CallNode children = 4;
}

message DataSeries {
  string name    = 1;
  uint32 count   = 2;
  double mean    = 3;
  double minimum = 4;
  double maximum = 5;
  double last    = 6;
}

message TraceReport {
  string            source_file         = 1;
  uint32            proc_lines          = 2;
  uint32            data_lines          = 3;
  uint32            skipped_exits       = 4;
  uint32            open_enters_at_eof  = 5;
  repeated CallNode   roots             = 6;
  repeated DataSeries data_series       = 7;
}
'''


# Minimal proto3 wire encoder. No external dependency on the protobuf package.
# Wire types used here:
#   0 - varint   (uint32, bool)
#   1 - 64-bit   (double)
#   2 - length-delimited (string, bytes, embedded message)
def _pb_varint(n: int) -> bytes:
    result = bytearray()
    n = int(n)
    while True:
        bits = n & 0x7F
        n >>= 7
        result.append((0x80 | bits) if n else bits)
        if not n:
            break
    return bytes(result)


def _pb_tag(field: int, wire: int) -> bytes:
    return _pb_varint((field << 3) | wire)


def _pb_uint(field: int, value: int) -> bytes:
    return _pb_tag(field, 0) + _pb_varint(value)


def _pb_double(field: int, value: float) -> bytes:
    import struct
    return _pb_tag(field, 1) + struct.pack('<d', float(value))


def _pb_bytes(field: int, data: bytes) -> bytes:
    return _pb_tag(field, 2) + _pb_varint(len(data)) + data


def _pb_string(field: int, s: str) -> bytes:
    return _pb_bytes(field, s.encode('utf-8'))


# Serialise a CallNode subtree. Depth is bounded by actual tree depth.
def _encode_call_node(node: 'CallNode') -> bytes:
    buf = (
        _pb_string(1, node.name) +
        _pb_uint(2, node.calls) +
        _pb_double(3, node.avg_ms)
    )
    for child in sorted(node.children.values(), key=lambda n: n.calls, reverse=True):
        buf += _pb_bytes(4, _encode_call_node(child))
    return buf


def _encode_data_series(series: 'DataSeries') -> bytes:
    return (
        _pb_string(1, series.name) +
        _pb_uint(2, series.count) +
        _pb_double(3, series.mean) +
        _pb_double(4, series.minimum) +
        _pb_double(5, series.maximum) +
        _pb_double(6, series.last)
    )


# Write <base>_trace.pb plus the companion <base>_trace.proto schema.
def export_protobuf(logfile: str, root: 'CallNode', data_map: dict,
                    proc_lines: int, data_lines: int,
                    skipped: int, dropped: int) -> None:
    buf = (
        _pb_string(1, logfile) +
        _pb_uint(2, proc_lines) +
        _pb_uint(3, data_lines) +
        _pb_uint(4, skipped) +
        _pb_uint(5, dropped)
    )
    for node in sorted(root.children.values(), key=lambda n: n.calls, reverse=True):
        buf += _pb_bytes(6, _encode_call_node(node))
    for series in sorted(data_map.values(), key=lambda s: s.name):
        buf += _pb_bytes(7, _encode_data_series(series))

    base       = logfile.rsplit('.', 1)[0] if '.' in logfile else logfile
    pb_path    = base + '_trace.pb'
    proto_path = base + '_trace.proto'

    with open(pb_path, 'wb') as fh:
        fh.write(buf)
    with open(proto_path, 'w') as fh:
        fh.write(_PROTO_SCHEMA)

    print(f"[trace_tree] Protobuf written  -> {pb_path}  ({len(buf):,} bytes)")
    print(f"[trace_tree] Proto schema      -> {proto_path}")
    print(f"[trace_tree] Decode:")
    print(f"[trace_tree]   protoc --decode=TraceReport {proto_path} < {pb_path}")


# Chrome Trace Format export. PROC >> becomes phase "B", PROC << becomes "E",
# DATA becomes an instant event "i" carrying the sampled value.
# Timestamps are converted from milliseconds (as written by trace.h) to
# microseconds as the format requires.
_DATA_RE_JSON = re.compile(
    r'\[DATA\]\s+([\w.]+)=([-+]?\d*\.?\d+)\s+@(\d+)'
)


def _parse_raw_events(logfile: str) -> list:
    events = []
    try:
        with open(logfile, 'r', errors='replace') as fh:
            for line in fh:
                mp = _PROC_RE.search(line)
                if mp:
                    fname  = normalise(mp.group(2).strip())
                    ts_us  = int(mp.group(3)) * 1000
                    cat    = fname.split('::')[0] if '::' in fname else 'global'
                    events.append({
                        'ph'  : 'B' if mp.group(1) == '>>' else 'E',
                        'ts'  : ts_us,
                        'name': fname,
                        'cat' : cat,
                        'pid' : 1,
                        'tid' : 1,
                    })
                    continue

                md = _DATA_RE_JSON.search(line)
                if md:
                    events.append({
                        'ph'  : 'i',
                        'ts'  : int(md.group(3)) * 1000,
                        'name': md.group(1),
                        'cat' : 'DATA',
                        'pid' : 1,
                        'tid' : 1,
                        's'   : 'p',
                        'args': {'value': float(md.group(2))},
                    })
    except FileNotFoundError:
        print(f"[ERROR] File not found for JSON export: {logfile}")
    return events


def export_json(logfile: str, data_map: dict,
                proc_lines: int, data_lines: int,
                skipped: int, dropped: int) -> None:
    import json
    import os

    trace_events = _parse_raw_events(logfile)

    # Process-name metadata. Shows up as the track label in visualisers.
    trace_events.insert(0, {
        'ph'  : 'M',
        'name': 'process_name',
        'pid' : 1,
        'tid' : 1,
        'args': {'name': 'trace_tree'},
    })

    output: dict = {
        'traceEvents'   : trace_events,
        'displayTimeUnit': 'ms',
        'metadata': {
            'source'            : logfile,
            'proc_lines'        : proc_lines,
            'data_lines'        : data_lines,
            'skipped_exits'     : skipped,
            'open_enters_at_eof': dropped,
            'generator'         : 'trace_tree.py --format json',
        },
    }

    if data_map:
        output['dataSummary'] = {
            name: {
                'count': s.count,
                'mean' : round(s.mean,    6),
                'min'  : round(s.minimum, 6),
                'max'  : round(s.maximum, 6),
                'last' : round(s.last,    6),
            }
            for name, s in sorted(data_map.items())
        }

    base     = logfile.rsplit('.', 1)[0] if '.' in logfile else logfile
    out_path = base + '_trace.json'

    with open(out_path, 'w') as fh:
        json.dump(output, fh, separators=(',', ':'))

    size_kb = os.path.getsize(out_path) // 1024
    n_slice = sum(1 for e in trace_events if e.get('ph') in ('B', 'E'))
    n_inst  = sum(1 for e in trace_events if e.get('ph') == 'i')

    print(f"[trace_tree] JSON written  -> {out_path}  ({size_kb:,} KB)")
    print(f"[trace_tree]   {n_slice:,} slice events (B/E)  |  {n_inst:,} instant events (DATA)")
    print(f"[trace_tree] Load at:")
    print(f"[trace_tree]   Perfetto    https://ui.perfetto.dev")
    print(f"[trace_tree]   Speedscope  https://speedscope.app")
    print(f"[trace_tree]   Chrome      chrome://tracing")


# Regular ASCII export. Writes the call tree and data summary to a text file
# instead of printing to stdout.
def export_text(logfile: str, root: CallNode, data_map: dict,
                no_proc: bool, no_data: bool) -> None:
    base     = logfile.rsplit('.', 1)[0] if '.' in logfile else logfile
    out_path = base + '_trace.txt'

    sections = []
    if not no_proc:
        sections.extend(build_proc_tree_lines(root))
        sections.append("")
    if not no_data:
        sections.extend(build_data_summary_lines(data_map))

    with open(out_path, 'w') as fh:
        fh.write("\n".join(sections))
        fh.write("\n")

    print(f"[trace_tree] ASCII tree written -> {out_path}")


def main():
    ap = argparse.ArgumentParser(
        description="trace.h log analyser: call tree + data summary")
    ap.add_argument("logfile",
                    help="trace.h capture file to analyse")
    ap.add_argument("--no-proc", action="store_true",
                    help="Suppress the procedure call tree (regular format only)")
    ap.add_argument("--no-data", action="store_true",
                    help="Suppress the data-flow summary (regular format only)")
    ap.add_argument(
        "--format",
        choices=["regular", "json", "protobuf"],
        default="regular",
        metavar="FORMAT",
        help=(
            "Output format: regular (default) | json | protobuf. "
            "regular writes <log>_trace.txt. "
            "json writes <log>_trace.json (Chrome Trace Format). "
            "protobuf writes <log>_trace.pb plus <log>_trace.proto schema."
        ),
    )
    args = ap.parse_args()

    print(f"[trace_tree] Parsing: {args.logfile}\n")

    root, data_map, proc_lines, data_lines, skipped, dropped = parse(args.logfile)

    print(f"[trace_tree] {proc_lines} [PROC] line(s) matched, "
          f"{data_lines} [DATA] line(s) matched")
    print(f"[trace_tree] {skipped} unmatched exit(s) skipped, "
          f"{dropped} open enter(s) at EOF "
          f"(rate-limit drops, lower TRACE_PROC_RATE_MS if high)\n")

    if args.format == "json":
        export_json(args.logfile, data_map, proc_lines, data_lines, skipped, dropped)
        return

    if args.format == "protobuf":
        export_protobuf(args.logfile, root, data_map,
                        proc_lines, data_lines, skipped, dropped)
        return

    export_text(args.logfile, root, data_map, args.no_proc, args.no_data)


if __name__ == '__main__':
    main()
