// Global settings page (issue #9). Loads current values from GET /app/config,
// posts edits to POST /app/config. The api_key field is intentionally NOT
// pre-populated — the server returns "***" as a sentinel when a key is set,
// and leaving the field blank on submit preserves the existing key server-side.

(() => {
  const form = () => document.getElementById("app-config-form");
  const statusEl = () => document.getElementById("save-status");
  const keyNote = () => document.getElementById("api-key-status");

  const setStatus = (msg, cls) => {
    const el = statusEl();
    if (!el) return;
    el.textContent = msg;
    el.className = "save-status " + (cls || "");
  };

  const populate = (cfg) => {
    const f = form();
    if (!f) return;
    const setVal = (name, val) => {
      const el = f.elements[name];
      if (el && val !== undefined && val !== null) el.value = val;
    };
    setVal("llm_provider", cfg.llm_provider);
    setVal("llm_model", cfg.llm_model);
    setVal("llm_base_url", cfg.llm_base_url);
    setVal("blender_host", cfg.blender_host);
    setVal("blender_port", cfg.blender_port);
    // Leave api_key blank — submitting an empty key preserves the existing one.
    if (cfg.has_api_key) {
      keyNote().textContent = "(a key is configured — leave blank to keep it)";
      keyNote().className = "field-note configured";
    } else {
      keyNote().textContent = "(no key set yet — required for first run)";
      keyNote().className = "field-note missing";
    }
  };

  const readForm = () => {
    const f = form();
    return {
      llm_provider:  f.elements["llm_provider"].value,
      llm_api_key:   f.elements["llm_api_key"].value,  // empty → preserve
      llm_model:     f.elements["llm_model"].value.trim(),
      llm_base_url:  f.elements["llm_base_url"].value.trim(),
      blender_host:  f.elements["blender_host"].value.trim(),
      blender_port:  Number(f.elements["blender_port"].value),
    };
  };

  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const r = await fetch("/app/config");
      if (r.ok) populate(await r.json());
    } catch (err) {
      setStatus("Failed to load current settings.", "error");
    }

    form().addEventListener("submit", async (ev) => {
      ev.preventDefault();
      setStatus("Saving…", "thinking");
      try {
        const r = await fetch("/app/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(readForm()),
        });
        if (!r.ok) {
          const detail = await r.text();
          setStatus(`Save failed (${r.status}): ${detail.slice(0, 200)}`, "error");
          return;
        }
        const body = await r.json();
        const s = body.status || {};
        const parts = [];
        if (s.llm)     parts.push(`llm: ${s.llm}`);
        if (s.blender) parts.push(`blender: ${s.blender}`);
        setStatus("Saved. " + parts.join(" · "), "ok");
        // Re-fetch so the masked key state refreshes.
        const refresh = await fetch("/app/config");
        if (refresh.ok) populate(await refresh.json());
        form().elements["llm_api_key"].value = "";
      } catch (err) {
        setStatus(`Save failed: ${err}`, "error");
      }
    });
  });
})();
