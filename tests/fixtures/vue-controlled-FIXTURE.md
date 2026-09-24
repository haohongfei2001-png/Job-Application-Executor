# Isolated Vue controlled-form fixture

`vue-controlled.bundle.js` is a test-only browser bundle built from
`vue-controlled-source.js`. It contains Vue `3.5.18` and its bundled
`@vue/*` runtime packages. Their MIT notices and license are adjacent.
Esbuild `0.25.9` generated the bundle and is not included in it.

The bundle is checked in so Python CI never fetches framework code in a
browser. The exact npm dependency graph is recorded in the adjacent
`vue-controlled.package.json` and `vue-controlled.package-lock.json`.
To rebuild in a temporary directory, copy those files there as
`package.json` and `package-lock.json`, run `npm ci`, copy the source there,
and run:

```sh
./node_modules/.bin/esbuild vue-controlled-source.js --bundle --minify \
  --platform=browser --format=iife --legal-comments=external \
  --outfile=vue-controlled.bundle.js
```

The committed bundle SHA-256 is
`8f2246555a5a9e86c46c10f37e0d921805d6bf99bca40b429a0156ad7e60e51e`.
The fixture uses only `127.0.0.1`, has an inert final button, and reports
Vue state separately from the server draft oracle.
