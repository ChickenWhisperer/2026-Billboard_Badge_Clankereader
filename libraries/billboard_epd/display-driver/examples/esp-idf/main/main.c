#include "billboard_espidf.h"
#include "billboard_decay.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include <stdlib.h>
#include <string.h>

void app_main(void)
{
    billboard_panel_t *panel = NULL;
    billboard_espidf_config_t config = billboard_espidf_default_config();
    ESP_ERROR_CHECK(billboard_espidf_create(&config, &panel));
    uint8_t *frame = heap_caps_malloc(BILLBOARD_FRAME_BYTES, MALLOC_CAP_SPIRAM);
    uint8_t *black = heap_caps_calloc(1, BILLBOARD_FRAME_BYTES, MALLOC_CAP_SPIRAM);
    uint8_t *white = heap_caps_calloc(1, BILLBOARD_FRAME_BYTES, MALLOC_CAP_SPIRAM);
    uint8_t *storage = heap_caps_malloc(5 * BILLBOARD_FRAME_BYTES, MALLOC_CAP_SPIRAM);
    int result = BILLBOARD_ERR_IO;
    if (!frame || !black || !white || !storage) goto done;
    memset(frame, 0xff, BILLBOARD_FRAME_BYTES);
    result = billboard_panel_full_refresh(panel, NULL, NULL, BILLBOARD_FRAME_BYTES);
    if (result) goto done;
    billboard_decay_t decay;
    result = billboard_decay_init(&decay, frame, BILLBOARD_FRAME_BYTES, storage, 5 * BILLBOARD_FRAME_BYTES);
    if (result) goto done;
    // A 320 x 160 black rectangle, then settle until all pulse counters expire.
    for (unsigned y = 80; y < 240; y++) memset(frame + y * 120 + 20, 0, 40);
    do {
        result = billboard_decay_prepare(&decay, frame, BILLBOARD_FRAME_BYTES, 1, 3, black, white);
        if (result || !decay.pending) break;
        result = billboard_panel_drive(panel, black, white, NULL, BILLBOARD_FRAME_BYTES);
        if (result) goto done;
        result = billboard_decay_complete(&decay, black, white, BILLBOARD_FRAME_BYTES);
        if (result) goto done;
    } while (decay.pending);
    if (result) goto done;
    result = billboard_panel_end_burst(panel);
    if (result) goto done;
    // Slow three-tone example: black, gray, white vertical bars. 1 selects tone.
    memset(black, 0, BILLBOARD_FRAME_BYTES);
    memset(white, 0, BILLBOARD_FRAME_BYTES);
    for (unsigned y = 0; y < BILLBOARD_HEIGHT; y++) {
        memset(black + y * 120, 0xff, 40);
        memset(white + y * 120 + 40, 0xff, 40); // second plane is gray in this call
    }
    result = billboard_panel_grayscale(panel, black, white, BILLBOARD_FRAME_BYTES);
done:
    if (result) ESP_LOGE("example", "display failed: %d (phase=%s)", result, panel->phase);
    else ESP_LOGI("example", "fast monochrome and slow grayscale complete");
    billboard_espidf_destroy(panel);
    free(frame); free(black); free(white); free(storage);
}
