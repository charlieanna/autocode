# Synchronous Figma source compression

This is the gzip-only tree-shaken subset of MIT-licensed fflate 0.8.2. It is
embedded verbatim in `tools/figma_inventory_page.js` between vendor markers so
the collector remains a standalone native Plugin API script. It imports no
module and requires no browser compression, text-encoding, worker or network API.
Build defines remove the unrelated optional text-codec feature probe dependency;
they do not change runtime globals. The local `asciiGzip` wrapper fixes
compression level 9 and timestamp 0. The
retained upstream timestamp branch is unreachable through this wrapper.

Upstream: https://github.com/101arrowz/fflate/tree/v0.8.2

Pinned npm package integrity:
`sha512-cPJU47OaAoCbg0pBvzsgpTPhmhqI5eJjh/JIu8tPj5q+T7iLvW/JAYUqmE7KOB4R1ZyEhzBaIQpQpardBF5z8A==`

Upstream `esm/browser.js` SHA256:
`8cc1f687e0159e977addb6b85e274dbd11e622cf151f4fcb7b85d49622ea43e7`

## Regenerate

Use an isolated directory, install exact `fflate@0.8.2` and `esbuild@0.25.10`
with lifecycle scripts disabled, and retain the package lock. The entry is:

```js
import { gzipSync } from "./node_modules/fflate/esm/browser.js";
export function asciiGzip(bytes) { return gzipSync(bytes, {level:9,mtime:0}); }
```

Bundle with:

```sh
esbuild entry.js --bundle --format=iife --global-name=autocodeFigmaGzip --platform=neutral --target=es2020 --tree-shaking=true --legal-comments=inline --define:TextDecoder=undefined --define:TextEncoder=undefined --outfile=bundle.js
```

Retain this header and the unmodified MIT license, then update the embedded copy
and its protocol test. Review the output: no `eval`, dynamic function creation,
worker, module import, network, compression-stream or text-encoder dependency
may remain. The collector test compares the embedded and vendored bytes and
round-trips through independent standard-library gzip decoding.

## Native invocation

Read the complete checked-in `tools/figma_inventory_page.js`. Replace only its
`FILE_KEY`, `PAGE_ID` and integer `outputPart` literals, and execute that entire
standalone source with the Figma inspection skill. No separate helper load,
build step, npm package, Node API, network request or runtime import is needed.
Retain every returned part and reassemble with `autocode_design_sources.reassemble`;
part headers and an offline collection audit do not establish source acceptance.
The transport changes no receipt field or original source JSON byte.
