#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BILLBOARD_EPD_VERSION "0.1.0-rc.1"
#define BILLBOARD_WIDTH 960
#define BILLBOARD_HEIGHT 672
#define BILLBOARD_FRAME_BYTES 80640u
#define BILLBOARD_HALF_BYTES 40320u
#define BILLBOARD_STORAGE_BYTES (2u * BILLBOARD_FRAME_BYTES + BILLBOARD_HALF_BYTES)

typedef enum {
    BILLBOARD_OK = 0,
    BILLBOARD_ERR_ARGUMENT = -1,
    BILLBOARD_ERR_IO = -2,
    BILLBOARD_ERR_TIMEOUT = -3,
    BILLBOARD_ERR_STATE = -4,
} billboard_result_t;

/* Single-owner synchronous transport. No callback may report success before
 * its I/O finishes. half = 0 both controllers, 1 left, 2 right. now_ms must
 * be monotonic modulo uint32_t. BUSY reads return 0 busy, 1 ready, <0 error. */
typedef struct {
    void *context;
    int (*write)(void *, unsigned half, uint8_t index, const uint8_t *, size_t);
    int (*reset)(void *, bool high);
    int (*busy)(void *, unsigned half);
    void (*delay_ms)(void *, unsigned);
    uint32_t (*now_ms)(void *);
} billboard_io_t;

typedef struct {
    billboard_io_t io;
    uint8_t *black, *red, *half;
    const uint8_t *lut;
    bool ready;
    int fault;
    const char *phase;
} billboard_panel_t;

/* No hardware side effects. storage must outlive panel and have at least
 * BILLBOARD_STORAGE_BYTES bytes; no alignment restriction. */
int billboard_panel_init(billboard_panel_t *, const billboard_io_t *, uint8_t *storage, size_t);
/* MSB-first horizontal masks, 1 = drive. Priority black > red > white;
 * everything else selects HOLD. red may be NULL. size must be FRAME_BYTES.
 * Returns only after both controllers complete; stays powered between calls. */
int billboard_panel_drive(billboard_panel_t *, const uint8_t *black,
                         const uint8_t *white, const uint8_t *red, size_t size);
int billboard_panel_end_burst(billboard_panel_t *);
/* Factory waveform; pigment bits 1 = ink, 0 = white. NULL planes mean white.
 * Red wins overlapping bits. Does not use application artwork. */
int billboard_panel_full_refresh(billboard_panel_t *, const uint8_t *black,
                                const uint8_t *red, size_t size);
/* Slow B1 three-tone mode. Black and gray masks must not overlap (11 invalid).
 * Factory white baseline, six white pulls, three grayscale pulls. No red. */
int billboard_panel_grayscale(billboard_panel_t *, const uint8_t *black,
                             const uint8_t *gray, size_t size);
/* A latched fault blocks further drawing until recovery completes. */
int billboard_panel_recover(billboard_panel_t *);

#ifdef __cplusplus
}
#endif
