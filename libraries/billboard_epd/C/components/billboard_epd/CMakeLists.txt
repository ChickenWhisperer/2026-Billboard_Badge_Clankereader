idf_component_register(
    SRCS "billboard_epd.c" "billboard_decay.c" "billboard_espidf.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES driver esp_timer
)
