/* codec.c - the SSM2603 on the Zybo Z7, over PS I2C0 (EMIO pins ac_scl/ac_sda).
 *
 * Register values from the original cfg_codec app. The I2S side is the FPGA's
 * (rtl/bclk_div.sv, i2s_rx.sv, i2s_tx.sv): the codec is the slave, 24-bit,
 * left-justified, 48 kHz from a 12.288 MHz MCLK.
 */

#include "fx_platform.h"

#include "sleep.h"
#include "xiicps.h"
#include "xparameters.h"

#define CODEC_ADDR      0x1A        /* CSB tied low on the Zybo */
#define I2C_SCLK_HZ     100000

#define R_LEFT_LINE_IN   0x00
#define R_RIGHT_LINE_IN  0x01
#define R_LEFT_HP        0x02
#define R_RIGHT_HP       0x03
#define R_ANALOG_PATH    0x04
#define R_DIGITAL_PATH   0x05
#define R_POWER          0x06
#define R_DIGITAL_IF     0x07
#define R_SAMPLE_RATE    0x08
#define R_ACTIVE         0x09
#define R_RESET          0x0F

#define LINE_IN_0DB      0x17       /* 1.5 dB steps: 0x00 = -34.5 dB, 0x3F = +33 dB */
#define HP_0DB           0x79       /* 1 dB steps: 0x30 = -73 dB, 0x7F = +6 dB */

static XIicPs g_iic;

#define I2C_IDLE_WAIT_US 10000         /* a 2-byte write takes ~0.3 ms at 100 kHz */

static int codec_write_reg(u8 reg, u16 data)
{
    /* [7-bit register | data bit 8] [data bits 7:0] */
    u8 buf[2] = { (u8)((reg << 1) | ((data >> 8) & 0x01)), (u8)(data & 0xFF) };
    int rc = XIicPs_MasterSendPolled(&g_iic, buf, 2, CODEC_ADDR);
    /* Bounded: a stuck bus (pins not reaching the codec) must not hang the app. */
    for (unsigned t = 0; XIicPs_BusIsBusy(&g_iic); t += 10) {
        if (t >= I2C_IDLE_WAIT_US)
            return -1;
        usleep(10);
    }
    return rc == XST_SUCCESS ? 0 : -1;
}

int codec_init(void)
{
    XIicPs_Config *cfg;
    int rc = 0;

#ifdef SDT
    cfg = XIicPs_LookupConfig(XPAR_XIICPS_0_BASEADDR);
#else
    cfg = XIicPs_LookupConfig(XPAR_XIICPS_0_DEVICE_ID);
#endif
    if (!cfg || XIicPs_CfgInitialize(&g_iic, cfg, cfg->BaseAddress) != XST_SUCCESS)
        return -1;
    XIicPs_SetSClk(&g_iic, I2C_SCLK_HZ);

    /* The datasheet's pop-free power-up: everything but the output stage first,
     * configure, let VMID settle, activate, and only then power the output.
     * The final state is the same as powering everything at once (R6 = 0). */
    rc |= codec_write_reg(R_RESET, 0x000);
    usleep(1000);
    rc |= codec_write_reg(R_POWER, 0x010);          /* all on except OUT (bit 4) */
    rc |= codec_write_reg(R_LEFT_LINE_IN, LINE_IN_0DB);
    rc |= codec_write_reg(R_RIGHT_LINE_IN, LINE_IN_0DB);
    rc |= codec_write_reg(R_LEFT_HP, HP_0DB);
    rc |= codec_write_reg(R_RIGHT_HP, HP_0DB);
    rc |= codec_write_reg(R_ANALOG_PATH, 0x012);    /* DAC to output, line in to ADC, mic muted */
    rc |= codec_write_reg(R_DIGITAL_PATH, 0x000);   /* DAC unmuted, ADC high-pass on */
    /* Bit 6 (MS) = 0: the codec is the I2S SLAVE. The FPGA drives BCLK and both
     * LRCLKs; master mode would make the codec drive them too. */
    rc |= codec_write_reg(R_DIGITAL_IF, 0x009);     /* slave, 24-bit, left-justified */
    rc |= codec_write_reg(R_SAMPLE_RATE, 0x000);    /* 48 kHz, 256 fs */
    usleep(100000);                                 /* VMID settles */
    rc |= codec_write_reg(R_ACTIVE, 0x001);
    rc |= codec_write_reg(R_POWER, 0x000);          /* now the output stage */
    return rc;
}

int codec_set_line_in_db(int tenths_db)
{
    int step = (tenths_db + 345 + 7) / 15;          /* nearest 1.5 dB step from -34.5 dB */
    if (tenths_db < -345 || tenths_db > 330)
        return -1;
    return codec_write_reg(R_LEFT_LINE_IN, (u16)step) | codec_write_reg(R_RIGHT_LINE_IN, (u16)step);
}

int codec_set_headphone_db(int db)
{
    if (db < -73 || db > 6)
        return -1;
    return codec_write_reg(R_LEFT_HP, (u16)(HP_0DB + db)) | codec_write_reg(R_RIGHT_HP, (u16)(HP_0DB + db));
}
