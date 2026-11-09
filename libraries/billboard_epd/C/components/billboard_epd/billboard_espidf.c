#include "billboard_espidf.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdlib.h>
#include <string.h>

typedef struct {
    billboard_panel_t panel;
    billboard_espidf_config_t config;
    spi_device_handle_t spi;
    uint8_t *storage, *dma;
    bool owns_bus;
} instance_t;

static int transfer(instance_t *s, const uint8_t *data, size_t size)
{
    while (size) {
        size_t chunk = size > 4000 ? 4000 : size;
        memcpy(s->dma, data, chunk);
        spi_transaction_t t = {.length = chunk * 8, .tx_buffer = s->dma};
        if (spi_device_polling_transmit(s->spi, &t) != ESP_OK) return -1;
        data += chunk; size -= chunk;
    }
    return 0;
}
static int write_data(void *context, unsigned half, uint8_t reg, const uint8_t *data, size_t size)
{
    instance_t *s = context;
    const billboard_espidf_config_t *c = &s->config;
    if (spi_device_acquire_bus(s->spi, portMAX_DELAY) != ESP_OK) return -1;
    int result = 0;
    if (gpio_set_level(c->dc, 0) || gpio_set_level(c->cs1, half == 2) ||
        gpio_set_level(c->cs2, half == 1) || transfer(s, &reg, 1)) result = -1;
    gpio_set_level(c->cs1, 1); gpio_set_level(c->cs2, 1);
    if (!result && (gpio_set_level(c->dc, 1) || gpio_set_level(c->cs1, half == 2) ||
                   gpio_set_level(c->cs2, half == 1) || transfer(s, data, size))) result = -1;
    gpio_set_level(c->cs1, 1); gpio_set_level(c->cs2, 1);
    spi_device_release_bus(s->spi);
    return result;
}
static int reset_pin(void *context, bool high)
{
    instance_t *s = context;
    gpio_set_level(s->config.cs1, 1); gpio_set_level(s->config.cs2, 1);
    return gpio_set_level(s->config.reset, high);
}
static int busy(void *context, unsigned half)
{
    instance_t *s = context;
    return gpio_get_level(half == 1 ? s->config.busy1 : s->config.busy2);
}
static void delay_ms(void *context, unsigned ms)
{
    (void)context;
    TickType_t ticks = pdMS_TO_TICKS(ms);
    vTaskDelay(ticks ? ticks : 1); /* Works with the default 100 Hz RTOS tick. */
}
static uint32_t now_ms(void *context)
{
    (void)context;
    return (uint32_t)(esp_timer_get_time() / 1000);
}
billboard_espidf_config_t billboard_espidf_default_config(void)
{
    return (billboard_espidf_config_t) {
        .host = SPI2_HOST, .cs1 = 13, .cs2 = 3, .busy1 = 12, .busy2 = 8,
        .dc = 10, .reset = 11, .sck = 21, .mosi = 47,
        .clock_hz = 20000000, .initialize_bus = true,
    };
}
esp_err_t billboard_espidf_create(const billboard_espidf_config_t *c, billboard_panel_t **out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    *out = NULL;
    if (!c || c->clock_hz < 1 || c->clock_hz > 20000000) return ESP_ERR_INVALID_ARG;
    int pins[] = {c->cs1,c->cs2,c->busy1,c->busy2,c->dc,c->reset,c->sck,c->mosi};
    for (unsigned i = 0; i < sizeof(pins)/sizeof(pins[0]); i++) {
        if (!GPIO_IS_VALID_GPIO(pins[i])) return ESP_ERR_INVALID_ARG;
        if (i != 2 && i != 3 && !GPIO_IS_VALID_OUTPUT_GPIO(pins[i])) return ESP_ERR_INVALID_ARG;
        for (unsigned j = 0; j < i; j++) if (pins[i] == pins[j]) return ESP_ERR_INVALID_ARG;
    }
    instance_t *s = calloc(1, sizeof(*s));
    if (!s) return ESP_ERR_NO_MEM;
    s->config = *c;
    s->storage = heap_caps_malloc(BILLBOARD_STORAGE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s->dma = heap_caps_malloc(4000, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    esp_err_t result = ESP_ERR_NO_MEM;
    if (!s->storage || !s->dma) goto fail;
    gpio_config_t outputs = {.mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << c->cs1) | (1ULL << c->cs2) | (1ULL << c->dc) | (1ULL << c->reset)};
    gpio_config_t inputs = {.mode = GPIO_MODE_INPUT,
        .pin_bit_mask = (1ULL << c->busy1) | (1ULL << c->busy2)};
    if ((result = gpio_config(&outputs)) != ESP_OK || (result = gpio_config(&inputs)) != ESP_OK) goto fail;
    gpio_set_level(c->cs1, 1); gpio_set_level(c->cs2, 1);
    gpio_set_level(c->dc, 1); gpio_set_level(c->reset, 1);
    if (c->initialize_bus) {
        spi_bus_config_t bus = {.sclk_io_num = c->sck, .mosi_io_num = c->mosi,
            .miso_io_num = -1, .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = 4000};
        result = spi_bus_initialize(c->host, &bus, SPI_DMA_CH_AUTO);
        if (result != ESP_OK) goto fail;
        s->owns_bus = true;
    }
    spi_device_interface_config_t device = {.clock_speed_hz = c->clock_hz,
        .mode = 0, .spics_io_num = -1, .queue_size = 1};
    result = spi_bus_add_device(c->host, &device, &s->spi);
    if (result != ESP_OK) goto fail;
    billboard_io_t io = {.context = s, .write = write_data, .reset = reset_pin,
        .busy = busy, .delay_ms = delay_ms, .now_ms = now_ms};
    billboard_panel_init(&s->panel, &io, s->storage, BILLBOARD_STORAGE_BYTES);
    *out = &s->panel;
    return ESP_OK;
fail:
    if (s->spi) spi_bus_remove_device(s->spi);
    if (s->owns_bus) spi_bus_free(c->host);
    free(s->storage); free(s->dma); free(s);
    return result;
}
int billboard_espidf_destroy(billboard_panel_t *panel)
{
    if (!panel) return BILLBOARD_ERR_ARGUMENT;
    instance_t *s = panel->io.context;
    int result = panel->fault ? panel->fault : billboard_panel_end_burst(panel);
    reset_pin(s, false);
    spi_bus_remove_device(s->spi);
    if (s->owns_bus) spi_bus_free(s->config.host);
    free(s->storage); free(s->dma); free(s);
    return result;
}
