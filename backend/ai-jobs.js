const prompts = require("../job_search_cli/ai-prompts.json");
const { generateText, AiConnectionError, redactStructured } = require("./ai-client");
const { readConnection, getAiReadiness } = require("./ai-readiness");

const text = (value) => typeof value === "string" && Boolean(value.trim());
const keys = (value, expected) => value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join() === expected.sort().join();
function validateResult(kind, value) {
  const valid = kind === "analysis"
    ? keys(value, ["analyzed", "desicion", "reasoning"]) && value.analyzed === 1 && [0, 1].includes(value.desicion) && text(value.reasoning)
    : keys(value, ["must_haves", "nice_to_haves", "company", "application_type"]) &&
      [value.must_haves, value.nice_to_haves].every((items) => Array.isArray(items) && items.every(text)) &&
      keys(value.company, ["name", "industry"]) && text(value.company.name) && text(value.company.industry) && text(value.application_type);
  if (!valid) throw new AiConnectionError(`AI returned an invalid ${kind} result. Retry or check the model's JSON support.`);
  return value;
}
async function generateJobResult(db, kind, job, notWant = "") {
  const ai = getAiReadiness(db);
  if (!ai.ready) throw new AiConnectionError(ai.guidance);
  const context = { company_name: job.company_name, position_name: job.position_name, about_text: job.about_text };
  if (kind === "analysis") {
    context.job_location = job.job_location;
    context.not_want = notWant;
  }
  const connection = readConnection(db);
  const reply = await generateText(connection, [
    { role: "system", content: prompts[kind] },
    { role: "user", content: JSON.stringify(context) },
  ]);
  let value;
  try { value = JSON.parse(reply); } catch {
    throw new AiConnectionError(`AI returned non-JSON ${kind} output. Retry or check the model's JSON support.`);
  }
  return validateResult(kind, redactStructured(value, connection.token));
}
module.exports = { generateJobResult, validateResult };
