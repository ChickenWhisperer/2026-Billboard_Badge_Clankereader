# HTTP Poller (load an image from a URL)

A simple app to periodically load an image from a URL. Intended to be modified to use an image of your choosing.

## Instructions

1. Copy `http_poller.py` to the `apps/` directory
2. Create a file named `wlan_credentials.json` in the `data/` directory
   - Refer to [`wlan_credentials.sample.json`](./wlan_credentials.sample.json) for an example

## Making it yours

There are a few constants in `http_poller.py` to trivially change what URL it loads, how often it refreshes, and how often to wait between failed attempts.

| Constant                  | Description                                               |
| ------------------------- | --------------------------------------------------------- |
| `IMAGE_URL`               | URL that is loaded periodically                           |
| `UPDATE_INTERVAL_SECONDS` | Number of seconds between successful updates              |
| `RETRY_INTERVAL_SECONDS`  | Number of seconds to wait after a failure before retrying |
