// trace.h
// Lightweight process-flow and data-flow tracing for C/C++ code bases.
//
// Two output streams written to a single log file:
//   PROC -> "[PROC] >> ClassName::method 12345"
//           "[PROC] << ClassName::method 12347"
//   DATA -> "[DATA] signal_name=3.1416 @12345"
//
// Edit TRACE_LOG_PATH below to change where the log file is written.

#pragma once

#include <unistd.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <time.h>

// Output log file path. Change this to redirect trace output.
#ifndef TRACE_LOG_PATH
#  define TRACE_LOG_PATH  "/tmp/trace.log"
#endif

// Master toggles. Set to 0 to compile out either stream entirely.
#ifndef TRACE_PROC_ENABLED
#  define TRACE_PROC_ENABLED  1
#endif

#ifndef TRACE_DATA_ENABLED
#  define TRACE_DATA_ENABLED  0
#endif

// Per-call-site rate limits in milliseconds.
// A 400Hz function with TRACE_PROC_RATE_MS=50 emits at most 20 lines/sec.
#ifndef TRACE_PROC_RATE_MS
#  define TRACE_PROC_RATE_MS   50
#endif

#ifndef TRACE_DATA_RATE_MS
#  define TRACE_DATA_RATE_MS  200
#endif

// Monotonic millisecond clock. No external dependencies.
static inline uint32_t _trace_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000UL + ts.tv_nsec / 1000000UL);
}

// Lazy file open. One fd per process, never closed. Returns -1 on failure
// so the trace silently drops rather than crashing the host program.
static inline int _trace_fd(void)
{
    static int fd = -1;
    if (fd == -1) {
        fd = open(TRACE_LOG_PATH,
                  O_WRONLY | O_CREAT | O_TRUNC, 0644);
    }
    return fd;
}

// Emit a PROC line. enter=true prints ">>", enter=false prints "<<".
static inline void _trace_proc_emit(const char *func, bool enter)
{
    int fd = _trace_fd();
    if (fd == -1) return;

    char buf[128];
    int len = snprintf(buf, sizeof(buf),
                       "[PROC] %s %.60s %lu\n",
                       enter ? ">>" : "<<",
                       func,
                       (unsigned long)_trace_now_ms());
    if (len > 0 && len < (int)sizeof(buf)) {
        { ssize_t _wr = write(fd, buf, (size_t)len); (void)_wr; }
    }
}

// Emit a DATA line in the form "[DATA] name=value @timestamp".
static inline void _trace_data_emit(const char *name, float val)
{
    int fd = _trace_fd();
    if (fd == -1) return;

    char buf[128];
    int len = snprintf(buf, sizeof(buf),
                       "[DATA] %.30s=%.4f @%lu\n",
                       name,
                       (double)val,
                       (unsigned long)_trace_now_ms());
    if (len > 0 && len < (int)sizeof(buf)) {
        { ssize_t _wr = write(fd, buf, (size_t)len); (void)_wr; }
    }
}

// TRACE_ENTER: place at the top of any function you want to trace.
// Uses a per-call-site static timer so high-frequency callers do not flood
// the log. __PRETTY_FUNCTION__ captures the full signature including class.
#if TRACE_PROC_ENABLED
#  define TRACE_ENTER()                                           \
     do {                                                         \
         static uint32_t _te = 0;                                 \
         uint32_t _tn = _trace_now_ms();                          \
         if (_tn - _te >= (uint32_t)TRACE_PROC_RATE_MS) {         \
             _te = _tn;                                           \
             _trace_proc_emit(__PRETTY_FUNCTION__, true);         \
         }                                                        \
     } while (0)
#else
#  define TRACE_ENTER()  do {} while (0)
#endif

// TRACE_EXIT: place before every return point of a traced function.
#if TRACE_PROC_ENABLED
#  define TRACE_EXIT()                                            \
     do {                                                         \
         static uint32_t _tx = 0;                                 \
         uint32_t _tn = _trace_now_ms();                          \
         if (_tn - _tx >= (uint32_t)TRACE_PROC_RATE_MS) {         \
             _tx = _tn;                                           \
             _trace_proc_emit(__PRETTY_FUNCTION__, false);        \
         }                                                        \
     } while (0)
#else
#  define TRACE_EXIT()  do {} while (0)
#endif

// TRACE_DATA: snapshot any numeric value at any point in a function.
// Enable per-file with: #define TRACE_DATA_ENABLED 1 before the include.
#if TRACE_DATA_ENABLED
#  define TRACE_DATA(name, val)                                   \
     do {                                                         \
         static uint32_t _td = 0;                                 \
         uint32_t _tn = _trace_now_ms();                          \
         if (_tn - _td >= (uint32_t)TRACE_DATA_RATE_MS) {         \
             _td = _tn;                                           \
             _trace_data_emit((name), (float)(val));              \
         }                                                        \
     } while (0)
#else
#  define TRACE_DATA(name, val)  do {} while (0)
#endif
