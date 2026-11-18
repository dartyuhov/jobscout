const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function privateDirectory(directory) {
  if (!fs.existsSync(directory)) {
    privateDirectory(path.dirname(directory));
    fs.mkdirSync(directory, { mode: 0o700 });
  }
  return directory;
}

function resolveRuntimePaths() {
  const home = os.homedir();
  const xdg = process.env.XDG_DATA_HOME;
  const platformRoot = process.platform === "darwin"
    ? path.join(home, "Library", "Application Support")
    : xdg && path.isAbsolute(xdg) ? xdg : path.join(home, ".local", "share");
  const data = path.resolve(process.env.JOB_SEARCH_DATA_DIR || path.join(platformRoot, "job-search"));
  const database = path.resolve(process.env.DB_PATH || path.join(data, "linkedin_jobs.sqlite3"));
  const sessions = path.resolve(process.env.SESSIONS_PATH || path.join(data, "sessions.json"));
  const canonical = (location) => fs.existsSync(location) ? fs.realpathSync(location) : path.join(canonical(path.dirname(location)), path.basename(location));
  const roots = [path.resolve(__dirname, ".."), process.env.JOB_SEARCH_SOURCE_ROOT].filter(Boolean).map(canonical);
  for (const location of [data, database, sessions]) {
    if (roots.some((root) => { const relative = path.relative(root, canonical(location)); return !relative || (!relative.startsWith(".." + path.sep) && relative !== ".." && !path.isAbsolute(relative)); })) {
      throw new Error(`Runtime storage '${location}' must be outside the checkout. Select a private external location.`);
    }
  }
  const runtime = privateDirectory(path.join(data, "runtime"));
  const logs = privateDirectory(path.join(data, "logs"));
  const chrome = privateDirectory(path.join(data, "chrome"));
  privateDirectory(path.join(chrome, "Default"));
  privateDirectory(path.dirname(database));
  privateDirectory(path.dirname(sessions));
  fs.closeSync(fs.openSync(database, "a", 0o600));
  fs.chmodSync(database, 0o600);
  return {
    data, database, sessions, runtime, logs, chrome,
    env: { ...process.env, JOB_SEARCH_DATA_DIR: data, DB_PATH: database, SESSIONS_PATH: sessions },
  };
}

module.exports = { resolveRuntimePaths };
