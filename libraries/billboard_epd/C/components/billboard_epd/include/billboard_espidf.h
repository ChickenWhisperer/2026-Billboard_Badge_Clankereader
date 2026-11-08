#pragma once
#include "billboard_epd.h"
#include "driver/spi_master.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    spi_host_device_t host;
    int cs1, cs2, busy1, busy2, dc, reset, sck, mosi;
    int clock_hz;
    bool initialize_bus; /* false to use a bus already initialized by the app */
} billboard_espidf_config_t;

billboard_espidf_config_t billboard_espidf_default_config(void);
/* Allocates buffers, configures pins/SPI. No display refresh until requested.
 * PSRAM required for panel storage. Caller owns the handle and serializes use. */
esp_err_t billboard_espidf_create(const billboard_espidf_config_t *, billboard_panel_t **out);
/* Shuts down and frees only the bus owned by this instance. Returns a shutdown
 * failure if one occurred; resources are still released and handle is invalid. */
int billboard_espidf_destroy(billboard_panel_t *);
#ifdef __cplusplus
}
#endif
