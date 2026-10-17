/** Bundle the extension. The `vscode` module is provided by the host, never bundled. */
import { build, context } from "esbuild";
import { copyFile } from "node:fs/promises";

// Include the repository's license in the distributable without a second copy
// to maintain. The bundled runtime needs no node_modules in its VSIX.
await copyFile(new URL("../LICENSE", import.meta.url), new URL("LICENSE", import.meta.url));

const watch = process.argv.includes("--watch");

const options = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  platform: "node",
  format: "cjs",
  target: "node18",
  external: ["vscode"],
  sourcemap: true,
  minify: false,
  logLevel: "warning",
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
} else {
  await build(options);
}
