const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const dest = path.join(root, "www");
fs.mkdirSync(dest, { recursive: true });
fs.copyFileSync(path.join(root, "index.html"), path.join(dest, "index.html"));
