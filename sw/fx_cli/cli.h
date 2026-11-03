/* cli.h - the fx> command line. Effect-agnostic: everything comes from fx_table. */

#ifndef FX_CLI_H
#define FX_CLI_H

#include <stddef.h>

#define CLI_LINE_MAX 160

void cli_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* The line editor, one received character at a time: echo, backspace, and on
 * Enter run the line and print the next prompt. */
void cli_feed(int c);

/* "fx> " and whatever has been typed of the next line. */
void cli_prompt(void);

/* A line from elsewhere (the front panel), printed over the prompt line, which
 * is then put back with what had been typed. */
void cli_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Run one command line. */
void cli_exec(char *line);

void cli_banner(void);

#endif /* FX_CLI_H */
