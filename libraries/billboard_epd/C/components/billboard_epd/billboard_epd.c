#include "billboard_epd.h"
#include "billboard_waveforms.h"
#include <string.h>

#define TRY(expr) do { int result_ = (expr); if (result_ != 0) return result_; } while (0)
#define SEND(half_, index_, ...) do { const uint8_t data_[] = { __VA_ARGS__ }; \
    TRY(write_data(p, half_, index_, data_, sizeof(data_))); } while (0)

static int write_data(billboard_panel_t *p, unsigned half, uint8_t reg,
                      const uint8_t *data, size_t size)
{
    return p->io.write(p->io.context, half, reg, data, size) == 0 ? 0 : BILLBOARD_ERR_IO;
}
static void delay(billboard_panel_t *p, unsigned ms) { p->io.delay_ms(p->io.context, ms); }
static uint32_t now(billboard_panel_t *p) { return p->io.now_ms(p->io.context); }
static int reset_pin(billboard_panel_t *p, bool high)
{
    return p->io.reset(p->io.context, high) == 0 ? 0 : BILLBOARD_ERR_IO;
}
static int check(billboard_panel_t *p)
{
    if (!p || !p->black || !p->io.write) return BILLBOARD_ERR_ARGUMENT;
    return p->fault ? BILLBOARD_ERR_STATE : 0;
}
static int latch(billboard_panel_t *p, int result)
{
    if (result) {
        p->fault = result;
        p->ready = false;
        p->lut = NULL;
        p->phase = "fault";
        (void)reset_pin(p, false);
    }
    return result;
}
static int wait_ready(billboard_panel_t *p, unsigned timeout)
{
    uint32_t start = now(p);
    for (;;) {
        int left = p->io.busy(p->io.context, 1), right = p->io.busy(p->io.context, 2);
        if (left < 0 || right < 0) return BILLBOARD_ERR_IO;
        if (left && right) return 0;
        if ((uint32_t)(now(p) - start) >= timeout) return BILLBOARD_ERR_TIMEOUT;
        delay(p, 5);
    }
}
static int reset(billboard_panel_t *p)
{
    p->phase = "reset";
    delay(p, 50);
    TRY(reset_pin(p, true)); delay(p, 20);
    TRY(reset_pin(p, false)); delay(p, 100);
    TRY(reset_pin(p, true)); delay(p, 100);
    delay(p, 5);
    return 0;
}
static int strobe(billboard_panel_t *p)
{
    SEND(0, 0xA7, 0x10); delay(p, 20);
    SEND(0, 0xA7, 0x00); delay(p, 20);
    return 0;
}
static int cog_init(billboard_panel_t *p, const uint8_t *lut)
{
    p->phase = "init";
    SEND(0, 0x05, 0x7D); delay(p, 200);
    SEND(0, 0x05, 0x00); delay(p, 10);
    SEND(0, 0xD8, 0x80); SEND(0, 0xD6, 0x00);
    TRY(strobe(p));
    SEND(0, 0x03, 0x00, 0x11);
    SEND(0, 0x44, 0x00); SEND(0, 0x45, 0x80); TRY(strobe(p));
    SEND(0, 0x44, 0x06); SEND(0, 0x45, 0x82); TRY(strobe(p));
    SEND(0, 0x60, 0x25); SEND(1, 0x61, 0x01); SEND(0, 0x02, 0x00);
    if (lut) TRY(write_data(p, 0, 0xB0, lut, 400));
    return 0;
}
static int power_on(billboard_panel_t *p)
{
    p->phase = "power on";
    SEND(0, 0x51, 0x50, 0x01);
    for (unsigned v = 1; v < 5; v++) {
        SEND(0, 0x09, 0x1F); SEND(0, 0x51, 0x50, v); SEND(0, 0x09, 0x9F); delay(p, 2);
    }
    for (unsigned v = 1; v < 11; v++) {
        SEND(0, 0x09, 0x1F); SEND(0, 0x51, 0x0A, v); SEND(0, 0x09, 0x9F); delay(p, 2);
    }
    for (unsigned v = 3; v < 11; v++) {
        SEND(0, 0x09, 0x7F); SEND(0, 0x51, 0x0A, v); SEND(0, 0x09, 0xFF); delay(p, 2);
    }
    for (unsigned v = 9; v > 1; v--) {
        SEND(0, 0x09, 0x7F); SEND(0, 0x51, v, 0x0A); SEND(0, 0x09, 0xFF); delay(p, 2);
    }
    SEND(0, 0x09, 0xFF); delay(p, 10);
    return 0;
}
static int pull(billboard_panel_t *p, unsigned timeout)
{
    p->phase = "refresh";
    TRY(wait_ready(p, timeout));
    SEND(0, 0x15, 0x3C);
    uint32_t start = now(p);
    bool saw_left = false, saw_right = false;
    while (!saw_left || !saw_right) {
        int left = p->io.busy(p->io.context, 1), right = p->io.busy(p->io.context, 2);
        if (left < 0 || right < 0) return BILLBOARD_ERR_IO;
        saw_left |= !left; saw_right |= !right;
        if ((uint32_t)(now(p) - start) >= 100) return BILLBOARD_ERR_TIMEOUT;
        delay(p, 1);
    }
    TRY(wait_ready(p, timeout));
    p->phase = "ready";
    return 0;
}
static uint8_t reverse(uint8_t value)
{
    value = (uint8_t)((value >> 4) | (value << 4));
    value = (uint8_t)(((value & 0xCC) >> 2) | ((value & 0x33) << 2));
    return (uint8_t)(((value & 0xAA) >> 1) | ((value & 0x55) << 1));
}
static int images(billboard_panel_t *p, const uint8_t *black, const uint8_t *red)
{
    p->phase = "send images";
    SEND(0, 0x13, 0, 0x3B, 0, 0, 0x9F, 2);
    SEND(0, 0x90, 0, 0x3B, 0, 0xA9);
    for (unsigned half = 1; half <= 2; half++) {
        for (unsigned color = 0; color < 2; color++) {
            const uint8_t *plane = color ? red : black;
            for (unsigned row = 0; row < BILLBOARD_HEIGHT; row++)
                for (unsigned x = 0; x < 60; x++)
                    p->half[row * 60 + x] = reverse(plane[row * 120 + (half - 1) * 60 + x]);
            SEND(half, 0x12, 0x3B, 0, 0x14);
            TRY(write_data(p, half, color ? 0x11 : 0x10, p->half, BILLBOARD_HALF_BYTES));
        }
    }
    return 0;
}
static int begin(billboard_panel_t *p, const uint8_t *lut)
{
    if (!p->ready) {
        TRY(reset(p)); TRY(cog_init(p, lut)); TRY(power_on(p));
        p->ready = true;
    } else {
        TRY(wait_ready(p, 15000));
        if (p->lut != lut) TRY(write_data(p, 0, 0xB0, lut, 400));
    }
    p->lut = lut;
    return 0;
}
static int end(billboard_panel_t *p)
{
    if (!p->ready) return 0;
    p->phase = "shutdown";
    TRY(wait_ready(p, 15000));
    SEND(0, 0x09, 0x7F); SEND(0, 0x05, 0x7D); SEND(0, 0x09, 0x00);
    delay(p, 100);
    TRY(wait_ready(p, 15000));
    TRY(reset_pin(p, false));
    p->ready = false; p->lut = NULL; p->phase = "idle";
    return 0;
}
int billboard_panel_init(billboard_panel_t *p, const billboard_io_t *io, uint8_t *storage, size_t size)
{
    if (!p || !io || !storage || size < BILLBOARD_STORAGE_BYTES || !io->write ||
        !io->reset || !io->busy || !io->delay_ms || !io->now_ms) return BILLBOARD_ERR_ARGUMENT;
    memset(p, 0, sizeof(*p));
    p->io = *io; p->black = storage; p->red = storage + BILLBOARD_FRAME_BYTES;
    p->half = p->red + BILLBOARD_FRAME_BYTES; p->phase = "idle";
    return 0;
}
static int drive(billboard_panel_t *p, const uint8_t *black, const uint8_t *white, const uint8_t *red)
{
    bool active = false, has_red = false;
    for (unsigned i = 0; i < BILLBOARD_FRAME_BYTES; i++) {
        uint8_t b = black[i], r = red ? (red[i] & (uint8_t)~b) : 0;
        uint8_t hold = (uint8_t)~(b | white[i] | r);
        p->black[i] = b | hold; p->red[i] = r | hold;
        active |= (b | white[i] | r) != 0; has_red |= r != 0;
    }
    if (!active) return 0;
    TRY(begin(p, has_red ? BILLBOARD_LUT_RED : BILLBOARD_LUT_BOLD));
    TRY(images(p, p->black, p->red));
    return pull(p, 15000);
}
int billboard_panel_drive(billboard_panel_t *p, const uint8_t *black,
                         const uint8_t *white, const uint8_t *red, size_t size)
{
    TRY(check(p));
    if (!black || !white || size != BILLBOARD_FRAME_BYTES) return BILLBOARD_ERR_ARGUMENT;
    return latch(p, drive(p, black, white, red));
}
int billboard_panel_end_burst(billboard_panel_t *p)
{
    TRY(check(p)); return latch(p, end(p));
}
static int full(billboard_panel_t *p, const uint8_t *black, const uint8_t *red)
{
    TRY(end(p));
    for (unsigned i = 0; i < BILLBOARD_FRAME_BYTES; i++) {
        uint8_t b = black ? black[i] : 0, r = red ? red[i] : 0;
        p->black[i] = b & (uint8_t)~r; p->red[i] = r;
    }
    TRY(reset(p)); TRY(images(p, p->black, p->red));
    TRY(cog_init(p, NULL)); TRY(power_on(p)); p->ready = true;
    TRY(pull(p, 90000)); return end(p);
}
int billboard_panel_full_refresh(billboard_panel_t *p, const uint8_t *black,
                                const uint8_t *red, size_t size)
{
    TRY(check(p));
    if (size != BILLBOARD_FRAME_BYTES) return BILLBOARD_ERR_ARGUMENT;
    return latch(p, full(p, black, red));
}
static int grayscale(billboard_panel_t *p, const uint8_t *black, const uint8_t *gray)
{
    TRY(full(p, NULL, NULL));
    TRY(begin(p, BILLBOARD_LUT_GRAY));
    TRY(images(p, p->black, p->red));
    for (unsigned pass = 0; pass < 6; pass++) TRY(pull(p, 15000));
    TRY(images(p, black, gray));
    for (unsigned pass = 0; pass < 3; pass++) TRY(pull(p, 15000));
    return end(p);
}
int billboard_panel_grayscale(billboard_panel_t *p, const uint8_t *black,
                             const uint8_t *gray, size_t size)
{
    TRY(check(p));
    if (!black || !gray || size != BILLBOARD_FRAME_BYTES) return BILLBOARD_ERR_ARGUMENT;
    for (unsigned i = 0; i < BILLBOARD_FRAME_BYTES; i++)
        if (black[i] & gray[i]) return BILLBOARD_ERR_ARGUMENT;
    return latch(p, grayscale(p, black, gray));
}
int billboard_panel_recover(billboard_panel_t *p)
{
    if (!p || !p->black || !p->io.write) return BILLBOARD_ERR_ARGUMENT;
    p->fault = 0; p->ready = false; p->lut = NULL;
    return latch(p, full(p, NULL, NULL));
}
