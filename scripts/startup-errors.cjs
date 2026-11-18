// Keep expected startup failures actionable without dumping private runtime data.
process.once("uncaughtException", (error) => {
  if (error.code === "EADDRINUSE") {
    console.error(`Error: Port ${process.env.PORT} is occupied. Stop the other portal or set PORT=3001 bash scripts/start.sh.`);
  } else if (error.code === "EACCES" || error.code === "EPERM" || String(error.code).startsWith("SQLITE_")) {
    console.error("Error: Cannot access private storage or database. Check JOB_SEARCH_DATA_DIR, DB_PATH, and SESSIONS_PATH permissions and available disk space.");
  } else {
    console.error("Error: Portal startup failed. Select Node.js 22.23.3, rerun bash scripts/bootstrap.sh, and check private storage permissions. See docs/native-setup.md.");
  }
  process.exit(1);
});
