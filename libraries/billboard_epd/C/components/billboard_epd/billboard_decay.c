#include "billboard_decay.h"
#include <limits.h>
#include <string.h>

int billboard_decay_init(billboard_decay_t *c, const uint8_t *frame, size_t size,
                        uint8_t *storage, size_t storage_size)
{
    if (!c || !frame || !storage || !size || size > UINT_MAX / 8u ||
        size > SIZE_MAX / 5u || storage_size < size * 5u) return -1;
    c->size = size;
    c->target = storage;
    c->counts = storage + size;
    return billboard_decay_reset(c, frame, size);
}

int billboard_decay_reset(billboard_decay_t *c, const uint8_t *frame, size_t size)
{
    if (!c || !frame || size != c->size || !c->target || !c->counts) return -1;
    memmove(c->target, frame, size);
    memset(c->counts, 0, size * 4);
    c->pending = 0;
    c->inflight = false;
    return 0;
}

int billboard_decay_prepare(billboard_decay_t *c, const uint8_t *frame, size_t size,
                           unsigned bp, unsigned wp, uint8_t *black, uint8_t *white)
{
    if (!c || !frame || !black || !white || !c->target || size != c->size ||
        c->inflight || bp < 1 || bp > 15 || wp < 1 || wp > 15) return -1;
    c->pending = 0;
    for (size_t byte = 0; byte < size; byte++) {
        unsigned requested = frame[byte];
        unsigned difference = requested ^ c->target[byte];
        black[byte] = white[byte] = 0;
        for (unsigned bit = 0; bit < 8; bit++) {
            unsigned mask = 1u << bit;
            size_t location = byte * 4 + bit / 2;
            unsigned shift = (bit % 2) * 4;
            unsigned packed = c->counts[location];
            unsigned count = (packed >> shift) & 15u;
            if (difference & mask) {
                count = (requested & mask) ? wp : bp;
                c->counts[location] = (uint8_t)((packed & ~(15u << shift)) | (count << shift));
            }
            if (count) {
                c->pending++;
                if (requested & mask) white[byte] |= (uint8_t)mask;
                else black[byte] |= (uint8_t)mask;
            }
        }
        c->target[byte] = (uint8_t)requested;
    }
    c->inflight = c->pending != 0;
    return 0;
}

int billboard_decay_complete(billboard_decay_t *c, const uint8_t *black,
                            const uint8_t *white, size_t size)
{
    if (!c || !black || !white || !c->target || size != c->size || !c->inflight) return -1;
    c->pending = 0;
    for (size_t byte = 0; byte < size; byte++) {
        unsigned requested = c->target[byte];
        unsigned delivered = (black[byte] & ~requested) | (white[byte] & requested);
        for (unsigned bit = 0; bit < 8; bit++) {
            size_t location = byte * 4 + bit / 2;
            unsigned shift = (bit % 2) * 4;
            unsigned packed = c->counts[location];
            unsigned count = (packed >> shift) & 15u;
            if (count && (delivered & (1u << bit))) {
                count--;
                c->counts[location] = (uint8_t)((packed & ~(15u << shift)) | (count << shift));
            }
            c->pending += count != 0;
        }
    }
    c->inflight = false;
    return 0;
}
