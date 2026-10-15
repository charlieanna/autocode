# Full Figma receipt transport

The native connector limits returned text to about 20 KB. Repeated 14 KB LZW
parts made a real whole-file collection require 1,672 calls; one page's complete
ASCII source is 27,383,426 bytes. Removing facts or accepting headers alone would
weaken source qualification.

The standalone page collector embeds the reviewed gzip-only subset of pinned
MIT-licensed fflate 0.8.2. It synchronously compresses the unchanged ASCII JSON
at level 9 with timestamp zero. No stream, worker, text-codec, runtime import,
network or global mutation is required. Package/source pins, license, exact
regeneration command and native invocation are beside the vendored helper.

The host uses Python's standard-library zlib decoder. It bounds output by both
the declared exact length and 32 MiB, validates gzip checksum/trailer, accepts
one complete member, rejects extra/truncated data and non-ASCII output, and then
checks the original SHA256 and receipt identity. Historical LZW parts remain
supported with their existing canonical-codec checks.

An authenticated Foundations source of 1,732,397 bytes retained its exact hash
and byte sequence through public reassembly: gzip required 9 parts instead of
37 LZW parts. This is an offline transport proof using a retained native source,
not a substitute for fresh native whole-file or visual acceptance.
