/* main.c - fx_cli: configure the codec, then run the fx> command line on the UART.
 *
 * The audio never passes through the ARM: it flows codec -> I2S -> router ->
 * I2S -> codec in the PL. The ARM only edits the router's patch memory.
 */

#include "cli.h"
#include "patch.h"
#include "fx_platform.h"

int main(void)
{
    char line[CLI_LINE_MAX];

    cli_printf("\n\nfx_cli: configuring the SSM2603 codec... ");
    cli_printf(codec_init() ? "NO ANSWER on I2C (check the IIC_0 EMIO pins in the XDC)\n" : "ok\n");
    /* The first access to the PL. If the bitstream is not loaded, nothing answers
     * on AXI and the CPU stalls here: say so first, so the hang is explained. */
    cli_printf("fx_cli: reading the router's patch memory (stuck here = no bitstream)... ");
    patch_init(fx_bram());
    cli_printf("ok\n");
    cli_banner();

    for (;;) {
        cli_printf("fx> ");
        if (cli_readline(line, sizeof line) < 0)
            break;
        cli_exec(line);
    }
    return 0;
}
