#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    size_t size;
    uint8_t *target, *counts;
    unsigned pending;
    bool inflight;
} billboard_decay_t;

/* Caller supplies 5*size bytes, retained until the planner is discarded.
 * Frames: 1 white, 0 black. Masks: 1 drives. MSB-first horizontal bits. */
int billboard_decay_init(billboard_decay_t *, const uint8_t *clean_frame,
                        size_t size, uint8_t *storage, size_t storage_size);
int billboard_decay_reset(billboard_decay_t *, const uint8_t *clean_frame, size_t);
/* Pulse budgets 1..15. Returns 0 on success; read state.pending for pixel count.
 * Do not prepare again or alter masks before complete (after hardware BUSY). */
int billboard_decay_prepare(billboard_decay_t *, const uint8_t *frame, size_t,
                           unsigned black_pulses, unsigned white_pulses,
                           uint8_t *black_mask, uint8_t *white_mask);
int billboard_decay_complete(billboard_decay_t *, const uint8_t *black_mask,
                            const uint8_t *white_mask, size_t);
#ifdef __cplusplus
}
#endif
