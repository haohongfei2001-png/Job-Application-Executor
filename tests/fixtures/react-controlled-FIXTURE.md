# Isolated React controlled-form fixture

`react-controlled.bundle.js` is a test-only browser bundle built from
`react-controlled-source.jsx`. It contains React `18.3.1`, React DOM `18.3.1`
and Scheduler `0.23.2`; these packages are MIT licensed. Their bundled notices
and common MIT license are in the adjacent `LEGAL.txt` and `MIT-LICENSE.txt`.
Esbuild `0.25.9` generated the bundle and is not included in it.

The bundle is checked in so the Python CI browser test is self-contained and
never fetches a framework script at runtime. For a rebuild, install the exact
versions above plus esbuild in a temporary directory, copy the JSX source into
that directory so Node resolves those packages, then run:

```sh
esbuild react-controlled-source.jsx --bundle --minify --platform=browser \
  --format=iife --jsx=automatic --legal-comments=external \
  --outfile=react-controlled.bundle.js
```

The committed bundle SHA-256 is
`738dda3d63e62020e0e6a47e882056520b45ff4cb14b7794937152c571fc0a11`.
The fixture uses only `127.0.0.1`, has an inert final button and reports
framework state separately from the server-side draft oracle.
