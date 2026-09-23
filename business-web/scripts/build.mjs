import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "src");
const output = join(root, "dist");
const files = ["index.html", "styles.css", "app.js", "api.js", "domain.js"];

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
const manifest = {};
for (const file of files) {
  const input = join(source, file);
  const contents = await readFile(input);
  if (contents.includes(Buffer.from("https://")) || contents.includes(Buffer.from("http://"))) {
    throw new Error(`${file} contains a remote URL; the business Web must be fully offline`);
  }
  await copyFile(input, join(output, file));
  manifest[file] = createHash("sha256").update(contents).digest("hex");
}
await writeFile(join(output, "asset-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
process.stdout.write(`Built ${files.length} offline Web assets in ${output}\n`);
