# Komga

- Container mounts config at `/config` and the shared library at `/library`.
- After startup, point Komga to `/library` or create a library that uses it.
- Optionally provide `KOMGA_LIBRARY_ID` and `KOMGA_TOKEN` to let the downloader trigger a scan.
