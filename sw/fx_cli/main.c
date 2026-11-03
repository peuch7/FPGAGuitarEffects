/* main.c - fx_cli: configure the codec, then serve the fx> command line on the
 * UART and the front panel (TFT + encoders), both editing the same chain.
 *
 * The audio never passes through the ARM: it flows codec -> I2S -> router ->
 * I2S -> codec in the PL. The ARM only edits the router's patch memory.
 */

#include "chain.h"
#include "cli.h"
#include "panel.h"
#include "patch.h"
#include "ui.h"
#include "fx_platform.h"

int main(void)
{
    int panel;

    cli_printf("\n\nfx_cli: configuring the SSM2603 codec... ");
    cli_printf(codec_init() ? "NO ANSWER on I2C (check the IIC_0 EMIO pins in the XDC)\n" : "ok\n");
    /* The first access to the PL. If the bitstream is not loaded, nothing answers
     * on AXI and the CPU stalls here: say so first, so the hang is explained. */
    cli_printf("fx_cli: reading the router's patch memory (stuck here = no bitstream)... ");
    patch_init(fx_bram());
    chain_init();
    cli_printf("ok\n");
    panel = panel_init() == 0;
    if (panel)
        ui_init();
    else
        cli_printf("fx_cli: front panel NOT started (SPI1, GPIO or timer missing?); serial only\n");
    cli_banner();
    cli_prompt();

    /* One character, then one panel pass: neither waits for the other. */
    for (;;) {
        int c = fx_trygetchar();
        if (c == FX_EOF)
            break;
        if (c != FX_NO_CHAR)
            cli_feed(c);
        if (panel)
            ui_poll();
    }
    return 0;
}
