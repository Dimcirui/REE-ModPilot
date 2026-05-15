// ModPilot chat — SSE event dispatcher and DOM updater.
//
// The htmx sse extension fires `htmx:sseMessage` for every server-sent event,
// with the SSE `event:` field on `evt.detail.type`. We dispatch by type and
// mutate the DOM directly — no template fragments come back over the wire.

(() => {
  const log     = () => document.getElementById("log");
  const phases  = () => document.getElementById("phases");
  const form    = () => document.getElementById("chat-form");
  const input   = () => document.getElementById("message-input");
  const status  = () => document.getElementById("status");
  const btn     = () => form().querySelector("button");

  const setStatus = (label, cls) => {
    const el = status();
    el.textContent = label;
    el.className = "status " + (cls || "");
  };

  const appendBubble = (role, text) => {
    const div = document.createElement("div");
    div.className = "bubble " + role;
    div.textContent = text;
    log().appendChild(div);
    log().scrollTop = log().scrollHeight;
  };

  const markPhase = (phaseName, cls) => {
    const node = phases().querySelector(`[data-phase="${phaseName}"]`);
    if (!node) return;
    // Don't downgrade done -> active
    if (cls === "active" && node.classList.contains("done")) return;
    node.classList.remove("active", "error");
    if (cls) node.classList.add(cls);
  };

  const dispatchers = {
    message: (e) => {
      // user messages echo back over SSE — only append assistant ones here;
      // the user bubble is appended optimistically on form submit so the user
      // sees their text immediately even before SSE arrives.
      if (e.role === "assistant") appendBubble("assistant", e.content);
    },
    state: (e) => {
      const map = {
        running_phase:   ["thinking",  "thinking"],
        await_confirm:   ["awaiting confirmation", "thinking"],
        error_handling:  ["error",     "error"],
        ask_mode:        ["ask mode",  "thinking"],
        negotiating:     ["negotiating", "thinking"],
        done:            ["done",      "done"],
        idle:            ["idle",      ""],
      };
      const [label, cls] = map[e.state] || [e.state, ""];
      setStatus(label, cls);
    },
    phase_started: (e) => {
      markPhase(e.phase, "active");
    },
    phase_completed: (e) => {
      markPhase(e.phase, "done");
    },
    tool_call: (e) => {
      const inputPreview = JSON.stringify(e.input || {}).slice(0, 200);
      appendBubble("tool", `> ${e.name}  ${inputPreview}`);
      // Issue #7: a confirmation widget is only meaningful until the LLM picks
      // up the answer and calls the downstream phase tool. Clear the slot so a
      // stale widget can't be re-submitted once its data is consumed.
      const slotClearTools = new Set([
        "physics_chains", "material_setup", "material_generate"
      ]);
      if (slotClearTools.has(e.name)) {
        const slot = document.getElementById("widget-slot");
        if (slot) slot.innerHTML = "";
      }
    },
    tool_result: (e) => {
      const tag = e.success ? "ok" : "FAIL";
      appendBubble("tool", `< [${tag}] ${e.name}: ${(e.summary || "").slice(0, 300)}`);
    },
    error: (e) => {
      appendBubble("error", `Error (${e.where || "?"}): ${e.message}`);
      setStatus("error", "error");
      btn().disabled = false;
    },
    done: (_e) => {
      setStatus("ready", "");
      btn().disabled = false;
    },
    // Issue #4: source-model type auto-inference. Emitted by AgentLoop
    // after setup_infer_model_type runs. We back-fill the form's
    // model_type dropdown, attach a coverage badge, and unlock the field
    // so the user can override before setup_import runs.
    model_type_inferred: (e) => {
      const sel = document.getElementById("model-type-select");
      const note = document.getElementById("model-type-note");
      if (sel && e.preset) {
        // Add the inferred preset as an <option> if the dropdown doesn't
        // already carry it (e.g. a brand-new supplemented preset whose
        // name only appeared after the page loaded).
        if (!Array.from(sel.options).some((o) => o.value === e.preset)) {
          const opt = document.createElement("option");
          opt.value = e.preset;
          opt.textContent = e.preset;
          sel.appendChild(opt);
        }
        sel.value = e.preset;
        sel.disabled = false;  // Always editable after inference
      }
      if (note) {
        const pct = Math.round((e.coverage || 0) * 100);
        const tag = e.decision === "exact"
          ? "matched"
          : e.decision === "supplement"
            ? "partial — supplement"
            : e.decision === "custom"
              ? "low — needs custom"
              : "unsupported";
        note.textContent = `Detected: ${e.preset} (${pct}% — ${tag})`;
        note.className = "field-note " + (e.decision === "exact" ? "configured" : "missing");
      }
      // Persist so a refresh keeps the inferred value sticky.
      try {
        const raw = localStorage.getItem("modpilot.config.v1");
        const cfg = raw ? JSON.parse(raw) : {};
        cfg.model_type = e.preset;
        localStorage.setItem("modpilot.config.v1", JSON.stringify(cfg));
      } catch (_) { /* ignore */ }
    },
  };

  document.body.addEventListener("htmx:sseMessage", (ev) => {
    const sseType = ev.detail.type;
    const fn = dispatchers[sseType];
    if (!fn) return;
    let payload;
    try {
      payload = JSON.parse(ev.detail.data);
    } catch (err) {
      console.warn("ModPilot: malformed SSE payload", ev.detail.data, err);
      return;
    }
    fn(payload);
  });

  // Optimistic user bubble + disable the input while a turn is in flight.
  // We rely on the SSE `done` (or `error`) event to re-enable.
  // Matches the chat form, the dynamically-inserted error-choice buttons
  // (issue #2), AND the confirmation widget forms (issue #7) so clicks feel
  // just as responsive as typed messages.
  document.body.addEventListener("htmx:configRequest", (ev) => {
    const elt = ev.detail.elt;
    if (!elt) return;
    const isForm = elt.id === "chat-form";
    const isErrorChoice = elt.classList && elt.classList.contains("error-choice-btn");
    const widgetForm = elt.classList && elt.classList.contains("widget-form") ? elt : null;
    if (!isForm && !isErrorChoice && !widgetForm) return;

    if (widgetForm) {
      // Optimistic preview: count rows that have a non-empty selection.
      const selected = widgetForm.querySelectorAll("select").length
        ? Array.from(widgetForm.querySelectorAll("select")).filter((s) => s.value).length
        : 0;
      const total = widgetForm.querySelectorAll("select").length;
      appendBubble("user", `[Confirmed ${selected}/${total} rows]`);
      widgetForm.classList.add("pending");
      widgetForm.querySelectorAll("button, select").forEach((el) => { el.disabled = true; });
    } else {
      const text = (ev.detail.parameters.message || "").trim();
      if (text) appendBubble("user", text);
    }
    btn().disabled = true;
    setStatus("thinking", "thinking");
  });

  // Issue #2: remove the error-choice button group right before htmx fires
  // the actual fetch. beforeRequest runs AFTER configRequest, so the
  // optimistic-bubble handler above sees the live button first and gets to
  // append the user bubble before this listener detaches the parent.
  document.body.addEventListener("htmx:beforeRequest", (ev) => {
    const elt = ev.detail.elt;
    if (elt && elt.classList && elt.classList.contains("error-choice-btn")) {
      const group = elt.closest(".error-choice-group");
      if (group) group.remove();
    }
  });

  document.body.addEventListener("htmx:afterRequest", (ev) => {
    if (ev.detail.elt && ev.detail.elt.id === "chat-form") {
      input().value = "";
      input().focus();
    }
    // Widget form (issue #7): on success, leave the form visible until the
    // tool_call dispatcher clears the slot. On 422 / network error, re-enable
    // so the user can fix their input and resubmit.
    const elt = ev.detail.elt;
    if (elt && elt.classList && elt.classList.contains("widget-form")) {
      if (!ev.detail.successful) {
        elt.classList.remove("pending");
        elt.querySelectorAll("button, select").forEach((el) => { el.disabled = false; });
        btn().disabled = false;
        setStatus("ready", "");
      }
    }
  });

  // Surface SSE-side connection issues.
  document.body.addEventListener("htmx:sseError", (ev) => {
    console.warn("ModPilot: SSE error", ev.detail);
    setStatus("disconnected", "error");
  });

  // ── Session config form (issue #3) ──────────────────────────────────────
  //
  // The form collects deterministic params (paths, names, toggles) before the
  // pipeline starts, so the agent doesn't have to ask for them mid-run. Values
  // are mirrored to localStorage so a refresh doesn't wipe the form.
  //
  // Wire-format note: htmx-ext-json-enc serializes ev.detail.parameters
  // verbatim, and the endpoint expects {session_id, config: {...}}. We
  // re-pack into that nested shape in htmx:configRequest below — input
  // names like `config.model_path` are flat keys from FormData, not real
  // nested objects.

  const CONFIG_STORAGE_KEY = "modpilot.config.v1";
  const REQUIRED_TEXT_NAMES = [
    "config.model_path",
    "config.model_type",
    "config.texture_dir",
    "config.mod_root",
    "config.author",
    "config.character_name",
  ];

  const configForm    = () => document.getElementById("config-form");
  const startBtn      = () => document.getElementById("config-start-btn");
  const errorsBox     = () => document.getElementById("config-errors");
  const savedBadge    = () => document.getElementById("config-saved-badge");
  const editBtn       = () => document.getElementById("config-edit-btn");
  const getSessionId  = () => document.body.dataset.sessionId || "";

  const readConfigForm = () => {
    const form = configForm();
    if (!form) return null;
    const cfg = {
      model_path:      form.elements["config.model_path"].value.trim(),
      model_type:      form.elements["config.model_type"].value,
      texture_dir:     form.elements["config.texture_dir"].value.trim(),
      mod_root:        form.elements["config.mod_root"].value.trim(),
      author:          form.elements["config.author"].value.trim(),
      character_name:  form.elements["config.character_name"].value.trim(),
      use_bone_system: form.elements["config.use_bone_system"].checked,
      body_parts: Array.from(form.querySelectorAll(
        'input[name="config.body_parts"]:checked'
      )).map((el) => el.value),
    };
    return cfg;
  };

  const populateConfigForm = (cfg) => {
    const form = configForm();
    if (!form || !cfg) return;
    const setVal = (name, val) => {
      const el = form.elements[name];
      if (el && val !== undefined && val !== null) el.value = val;
    };
    setVal("config.model_path",     cfg.model_path);
    setVal("config.model_type",     cfg.model_type);
    setVal("config.texture_dir",    cfg.texture_dir);
    setVal("config.mod_root",       cfg.mod_root);
    setVal("config.author",         cfg.author);
    setVal("config.character_name", cfg.character_name);
    if (form.elements["config.use_bone_system"]) {
      form.elements["config.use_bone_system"].checked = !!cfg.use_bone_system;
    }
    const wanted = new Set(Array.isArray(cfg.body_parts) ? cfg.body_parts : []);
    form.querySelectorAll('input[name="config.body_parts"]').forEach((el) => {
      el.checked = wanted.has(el.value);
    });
  };

  const isConfigComplete = () => {
    const form = configForm();
    if (!form) return false;
    for (const name of REQUIRED_TEXT_NAMES) {
      const el = form.elements[name];
      if (!el) return false;
      if (!String(el.value || "").trim()) return false;
    }
    const anyPart = form.querySelector('input[name="config.body_parts"]:checked');
    return !!anyPart;
  };

  const updateStartButtonState = () => {
    const sb = startBtn();
    if (sb) sb.disabled = !isConfigComplete();
  };

  const clearFieldErrors = () => {
    const eb = errorsBox();
    if (eb) eb.innerHTML = "";
    configForm()?.querySelectorAll("input.invalid").forEach((el) =>
      el.classList.remove("invalid"),
    );
  };

  const renderFieldErrors = (responseJson) => {
    const eb = errorsBox();
    const form = configForm();
    if (!eb || !form) return;
    clearFieldErrors();
    const fieldErrors =
      (responseJson && responseJson.detail && responseJson.detail.field_errors) || null;
    if (!fieldErrors) {
      eb.textContent = "Save failed. Check your inputs.";
      return;
    }
    const lines = [];
    Object.entries(fieldErrors).forEach(([name, msg]) => {
      const el = form.elements[`config.${name}`];
      if (el) el.classList.add("invalid");
      lines.push(`${name}: ${msg}`);
    });
    eb.textContent = lines.join(" • ");
  };

  // (a) Rehydrate on page load
  window.addEventListener("DOMContentLoaded", () => {
    if (!configForm()) return;
    // (a1) Populate the model_type dropdown from the live preset catalog.
    // Issue #4: the hardcoded MMD/VRChat/Other set is gone — options come
    // from /app/x_presets so newly-installed (or supplemented) presets are
    // selectable without a page rebuild.
    fetch("/app/x_presets", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { presets: [] }))
      .then(({ presets }) => {
        const sel = document.getElementById("model-type-select");
        if (!sel) return;
        const previous = sel.value;
        // Preserve the leading Auto-detect <option>; append the rest.
        for (const p of presets || []) {
          if (Array.from(sel.options).some((o) => o.value === p.name)) continue;
          const opt = document.createElement("option");
          opt.value = p.name;
          opt.textContent = p.name;
          if (p.description) opt.title = p.description;
          sel.appendChild(opt);
        }
        // Restore from localStorage if we had a saved value.
        if (previous && Array.from(sel.options).some((o) => o.value === previous)) {
          sel.value = previous;
        }
        updateStartButtonState();
      })
      .catch(() => { /* offline / 503 — Auto-detect option still works */ });

    try {
      const raw = localStorage.getItem(CONFIG_STORAGE_KEY);
      if (raw) populateConfigForm(JSON.parse(raw));
    } catch (e) {
      // Corrupt storage — clear it so the user can refill cleanly.
      console.warn("ModPilot: ignoring corrupt session config in localStorage", e);
      try { localStorage.removeItem(CONFIG_STORAGE_KEY); } catch (_) { /* ignore */ }
    }
    updateStartButtonState();
  });

  // (b) Live-validate to enable Start
  document.body.addEventListener("input",  (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest("#config-form")) {
      updateStartButtonState();
    }
  });
  document.body.addEventListener("change", (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest("#config-form")) {
      updateStartButtonState();
    }
  });

  // (c) Re-pack params into {session_id, config: {...}} before json-enc
  //     serializes the body. FormData yields flat `config.model_path` keys
  //     which Pydantic won't unfold into the nested SessionConfigRequest;
  //     we substitute the right object here.
  document.body.addEventListener("htmx:configRequest", (ev) => {
    if (!ev.detail.elt || ev.detail.elt.id !== "config-form") return;
    const cfg = readConfigForm();
    if (!cfg) return;
    ev.detail.parameters = { session_id: getSessionId(), config: cfg };
    try {
      localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(cfg));
    } catch (_) { /* ignore quota errors */ }
  });

  // (d) After save: success → hide form via .config-locked; 422 → show errors.
  document.body.addEventListener("htmx:afterRequest", (ev) => {
    if (!ev.detail.elt || ev.detail.elt.id !== "config-form") return;
    if (ev.detail.successful) {
      clearFieldErrors();
      document.body.classList.add("config-locked");
      return;
    }
    let body = null;
    try { body = JSON.parse(ev.detail.xhr.responseText); } catch (_) { /* noop */ }
    renderFieldErrors(body);
  });

  // (e) Edit button unlocks the form.
  document.body.addEventListener("click", (ev) => {
    if (ev.target && ev.target.id === "config-edit-btn") {
      document.body.classList.remove("config-locked");
    }
  });

  // ── Viewport side-panel (Stage 5 P0) ────────────────────────────────────
  //
  // Periodically pulls /viewport_screenshot and swaps the <img>. Uses fetch
  // instead of setting img.src to a URL with a cache-bust query string so
  // 503 (Blender unreachable) surfaces as a status badge rather than a
  // broken-image icon. Pauses while the tab is hidden so we don't hammer
  // Blender when nobody's looking.
  const VIEWPORT_INTERVAL_MS = 5000;
  const VIEWPORT_MAX_SIZE = 800;
  const viewportImg         = () => document.getElementById("viewport-img");
  const viewportAuto        = () => document.getElementById("viewport-auto");
  const viewportRefreshBtn  = () => document.getElementById("viewport-refresh");
  const viewportStatusEl    = () => document.getElementById("viewport-status");
  const viewportPlaceholder = () => document.getElementById("viewport-placeholder");

  let viewportInFlight = false;
  let lastViewportUrl  = null;
  let viewportTimer    = null;

  const setViewportStatus = (text, cls) => {
    const el = viewportStatusEl();
    if (!el) return;
    el.textContent = text;
    el.className = "viewport-status " + (cls || "");
  };

  const refreshViewport = async () => {
    const img = viewportImg();
    if (!img) return;
    if (viewportInFlight) return;
    viewportInFlight = true;
    const btn = viewportRefreshBtn();
    if (btn) btn.disabled = true;
    setViewportStatus("refreshing…", "");
    try {
      const resp = await fetch(`/viewport_screenshot?max_size=${VIEWPORT_MAX_SIZE}`, {
        cache: "no-store",
      });
      if (!resp.ok) {
        let detail = "";
        try { detail = (await resp.json()).detail || ""; } catch (_) { /* noop */ }
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      img.src = url;
      img.classList.add("loaded");
      if (lastViewportUrl) URL.revokeObjectURL(lastViewportUrl);
      lastViewportUrl = url;
      setViewportStatus(`updated ${new Date().toLocaleTimeString()}`, "ok");
    } catch (err) {
      img.classList.remove("loaded");
      const ph = viewportPlaceholder();
      if (ph) ph.textContent = "Blender unreachable";
      setViewportStatus(String(err.message || err), "error");
    } finally {
      viewportInFlight = false;
      if (btn) btn.disabled = false;
    }
  };

  const startViewportTimer = () => {
    if (viewportTimer) clearInterval(viewportTimer);
    viewportTimer = setInterval(() => {
      if (document.hidden) return;
      const auto = viewportAuto();
      if (!auto || !auto.checked) return;
      refreshViewport();
    }, VIEWPORT_INTERVAL_MS);
  };

  window.addEventListener("DOMContentLoaded", () => {
    if (!viewportImg()) return;  // page without the panel (e.g. /config)
    refreshViewport();
    startViewportTimer();
  });

  document.body.addEventListener("click", (ev) => {
    if (ev.target && ev.target.id === "viewport-refresh") refreshViewport();
  });
  document.body.addEventListener("change", (ev) => {
    // Re-enabling auto should give an immediate refresh — same affordance
    // a manual click provides — instead of waiting up to 5s.
    if (ev.target && ev.target.id === "viewport-auto" && ev.target.checked) {
      refreshViewport();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    const auto = viewportAuto();
    if (auto && auto.checked) refreshViewport();
  });
})();
