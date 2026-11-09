# Billboard display libraries

Reusable C/ESP-IDF and MicroPython drivers for the 960 × 672 B1 display on the
Hackaday 2026 Billboard Badge. Version **0.1.0-rc.1** includes fast drive masks,
per-pixel pulse budgets, factory color refresh and slow black/gray/white refresh.

* [Usage and API guide](display-driver/README.md)
* [C component](C/components/billboard_epd/README.md)
* [MicroPython package](Python/badge/lib/billboard_epd/README.md)
* [Validation and remaining hardware checks](display-driver/VALIDATION.md)

The source package includes examples, host tests and reproducible packaging tools.
Run `python display-driver/tools/make_release.py` to build separate library ZIPs.
This release candidate has software validation; physical display validation is
required before a stable release. Licensing is in [LICENSE](LICENSE).
