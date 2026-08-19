import { readFileSync, writeFileSync } from "node:fs";

const path = new URL("../web/previs_editor.js", import.meta.url);
const source = readFileSync(path, "utf8");
writeFileSync(path, source.replace(/[ \t]+$/gm, ""), "utf8");
