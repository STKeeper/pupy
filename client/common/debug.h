#ifndef __DEBUG_H
#define __DEBUG_H

#include <stdio.h>
#include <stdarg.h>

#ifdef DEBUG

static int dprint(const char *fmt, ...) {
    va_list args;
    int n;

    va_start (args, fmt);
    n = vfprintf(stdout, fmt, args);
    va_end (args);
    fflush(stdout);
    return n;
}

#define DOC(x) x

#else
#define DOC(x) ""

#define dprint(...)	do {} while (0)

#endif

#endif /* __DEBUG_H */
