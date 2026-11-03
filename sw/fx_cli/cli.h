/* cli.h - the fx> command line. Effect-agnostic: everything comes from fx_table. */

#ifndef FX_CLI_H
#define FX_CLI_H

#include <stddef.h>

#define CLI_LINE_MAX 160

void cli_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Read one line with echo and backspace. Returns its length, or -1 at end of input. */
int cli_readline(char *buf, size_t len);

/* Run one command line. */
void cli_exec(char *line);

void cli_banner(void);

#endif /* FX_CLI_H */
