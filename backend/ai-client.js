// Generation-only Chat Completions subset shared by Settings and AI features.
class AiConnectionError extends Error {}

function redactStructured(value, token) {
  if (!token) return value;
  if (typeof value === "string") return value.split(token).join("[redacted]");
  if (Array.isArray(value)) return value.map((item) => redactStructured(item, token));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactStructured(item, token)]));
  return value;
}

async function generateText(connection, messages) {
  let response;
  try {
    response = await fetch(`${connection.base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST",
      redirect: "error",
      headers: {
        "content-type": "application/json",
        ...(connection.token ? { authorization: `Bearer ${connection.token}` } : {}),
      },
      body: JSON.stringify({ model: connection.model, messages, stream: false }),
      signal: AbortSignal.timeout(30000),
    });
  } catch {
    throw new AiConnectionError("Cannot reach AI endpoint. Check the base URL, server, TLS certificate, and network; requests time out after 30 seconds.");
  }
  if (!response.ok) {
    // Provider bodies and transport exceptions can echo headers and credentials.
    await response.body?.cancel();
    const help = response.status === 401 || response.status === 403
      ? "Check the saved token and endpoint permissions."
      : response.status === 404 ? "Check the base URL and model name."
        : response.status === 429 ? "Check provider quota or retry later."
          : "Check the model, compatible Chat Completions support, and provider availability.";
    throw new AiConnectionError(`AI generation failed (HTTP ${response.status}). ${help}`);
  }
  let payload;
  try { payload = await response.json(); } catch {
    throw new AiConnectionError("AI endpoint returned invalid JSON. Use a compatible Chat Completions endpoint.");
  }
  const choice = payload?.choices?.[0];
  const message = choice?.message;
  if (typeof message?.content !== "string" || !message.content.trim() || message.tool_calls?.length || message.function_call || message.refusal || choice.finish_reason !== "stop") {
    throw new AiConnectionError("AI endpoint did not return a complete text generation. Check the model supports non-streaming Chat Completions text replies.");
  }
  return connection.token ? message.content.split(connection.token).join("[redacted]") : message.content;
}

module.exports = { generateText, AiConnectionError, redactStructured };
