import {
  FIELD_PLACEHOLDERS,
  LIST_FIELD_CONFIG,
  LIST_FIELDS,
  PROMPTS_API_ROUTE,
} from "./admin_config.js";

export function bootstrapAdminForm() {
  const form = document.querySelector("#settings-form");
  const statusElement = document.querySelector("#status");
  const saveButton = document.querySelector("#save-settings");
  const promptEditor = form.elements.prompts_json;

  function setStatus(message) {
    statusElement.textContent = message;
  }

  function setStatusState(state) {
    statusElement.dataset.state = state;
  }

  function setSaving(isSaving) {
    saveButton.disabled = isSaving;
    saveButton.textContent = isSaving ? "Saving..." : "Save settings and prompts";
  }

  function setField(name, value) {
    form.elements[name].value = value;
  }

  function setPlaceholder(name, value) {
    form.elements[name].placeholder = value;
  }

  function createListRow(fieldName, value = "") {
    const row = document.createElement("div");
    row.className = "list-row";
    row.dataset.listRow = fieldName;

    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.placeholder = LIST_FIELD_CONFIG[fieldName]?.placeholder || "";
    input.autocomplete = "off";

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "list-remove";
    removeButton.dataset.listRemove = fieldName;
    removeButton.setAttribute("aria-label", `Remove one ${fieldName} item`);
    removeButton.title = "Remove row";
    removeButton.innerHTML = "&#128465;&#65039; Remove";

    row.append(input, removeButton);
    return row;
  }

  function renderListEditor(fieldName, values) {
    const container = document.querySelector(`[data-list-editor="${fieldName}"]`);
    if (!container) {
      return;
    }

    container.innerHTML = "";

    const items = values.length > 0 ? values : [""];
    for (const value of items) {
      container.appendChild(createListRow(fieldName, value));
    }
  }

  function readListEditor(fieldName) {
    const container = document.querySelector(`[data-list-editor="${fieldName}"]`);
    if (!container) {
      return [];
    }

    return [...container.querySelectorAll(".list-row input")]
      .map((input) => input.value.trim())
      .filter(Boolean);
  }

  function ensureListEditorHasRow(fieldName) {
    const container = document.querySelector(`[data-list-editor="${fieldName}"]`);
    if (!container || container.querySelector(".list-row")) {
      return;
    }

    container.appendChild(createListRow(fieldName));
  }

  function fillForm(settings) {
    for (const [fieldName, placeholder] of Object.entries(FIELD_PLACEHOLDERS)) {
      setPlaceholder(fieldName, placeholder);
    }

    setField("project_root", settings.project_root);
    setField("backlog_path", settings.backlog_path);
    setField("max_runtime_minutes", settings.max_runtime_minutes);
    setField("coding_agent_command", settings.coding_agent_command);
    form.elements.execute_coding_agent.checked = settings.execute_coding_agent;
    form.elements.telegram_enabled.checked = settings.telegram_enabled;
    form.elements.orchestrator_ai_enabled.checked = settings.orchestrator_ai_enabled;
    setField("admin_bind_host", settings.admin_bind_host);
    setField("admin_bind_port", settings.admin_bind_port);
    setField("orchestrator_ai_model", settings.orchestrator_ai_model);
    setField("orchestrator_ai_max_output_tokens", settings.orchestrator_ai_max_output_tokens);
    setField("orchestrator_ai_timeout_seconds", settings.orchestrator_ai_timeout_seconds);
    setField("telegram_transport", settings.telegram_transport);
    setField("telegram_api_base_url", settings.telegram_api_base_url);
    setField("telegram_long_poll_timeout_seconds", settings.telegram_long_poll_timeout_seconds);
    setField("telegram_max_code_request_chars", settings.telegram_max_code_request_chars);
    setField("telegram_max_message_chars", settings.telegram_max_message_chars);
    setField("telegram_webhook_url", settings.telegram_webhook_url);
    setField("telegram_webhook_bind_host", settings.telegram_webhook_bind_host);
    setField("telegram_webhook_bind_port", settings.telegram_webhook_bind_port);
    setField("telegram_webhook_secret_token", settings.telegram_webhook_secret_token);

    for (const fieldName of LIST_FIELDS) {
      renderListEditor(fieldName, settings[fieldName]);
    }
  }

  function fillPrompts(promptsPayload) {
    promptEditor.value = JSON.stringify(promptsPayload, null, 2);
  }

  function readPromptRegistry() {
    const text = promptEditor.value.trim();
    if (!text) {
      throw new Error("Prompt registry cannot be empty.");
    }

    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Prompt registry must be a JSON object.");
      }
      if (!Array.isArray(parsed.prompts) || parsed.prompts.length === 0) {
        throw new Error("Prompt registry must include a non-empty prompts array.");
      }
      return parsed;
    } catch (error) {
      throw new Error(`Prompt registry must be valid JSON: ${error.message}`);
    }
  }

  function readForm() {
    const settings = {
      project_root: form.elements.project_root.value.trim(),
      backlog_path: form.elements.backlog_path.value.trim(),
      max_runtime_minutes: Number.parseInt(form.elements.max_runtime_minutes.value, 10),
      coding_agent_command: form.elements.coding_agent_command.value.trim(),
      execute_coding_agent: form.elements.execute_coding_agent.checked,
      telegram_enabled: form.elements.telegram_enabled.checked,
      admin_bind_host: form.elements.admin_bind_host.value.trim(),
      admin_bind_port: Number.parseInt(form.elements.admin_bind_port.value, 10),
      orchestrator_ai_enabled: form.elements.orchestrator_ai_enabled.checked,
      orchestrator_ai_model: form.elements.orchestrator_ai_model.value.trim(),
      orchestrator_ai_max_output_tokens: Number.parseInt(
        form.elements.orchestrator_ai_max_output_tokens.value,
        10,
      ),
      orchestrator_ai_timeout_seconds: Number.parseInt(
        form.elements.orchestrator_ai_timeout_seconds.value,
        10,
      ),
      telegram_transport: form.elements.telegram_transport.value.trim(),
      telegram_api_base_url: form.elements.telegram_api_base_url.value.trim(),
      telegram_long_poll_timeout_seconds: Number.parseInt(
        form.elements.telegram_long_poll_timeout_seconds.value,
        10,
      ),
      telegram_max_code_request_chars: Number.parseInt(
        form.elements.telegram_max_code_request_chars.value,
        10,
      ),
      telegram_max_message_chars: Number.parseInt(
        form.elements.telegram_max_message_chars.value,
        10,
      ),
      telegram_webhook_url: form.elements.telegram_webhook_url.value.trim(),
      telegram_webhook_bind_host: form.elements.telegram_webhook_bind_host.value.trim(),
      telegram_webhook_bind_port: Number.parseInt(
        form.elements.telegram_webhook_bind_port.value,
        10,
      ),
      telegram_webhook_secret_token: form.elements.telegram_webhook_secret_token.value.trim(),
    };

    for (const fieldName of LIST_FIELDS) {
      settings[fieldName] = readListEditor(fieldName);
    }

    return settings;
  }

  async function loadSettings() {
    setStatusState("info");
    setStatus("Loading local settings...");

    try {
      const settingsResponse = await fetch("/api/settings");
      const settingsData = await settingsResponse.json();

      if (!settingsResponse.ok) {
        throw new Error(settingsData.error || "Unable to load settings.");
      }

      fillForm(settingsData);
      setStatusState("success");
      setStatus("Local settings loaded.");
    } catch (error) {
      setStatusState("error");
      setStatus(error.message || "Unable to load local settings.");
    }
  }

  async function loadPrompts() {
    setStatusState("info");
    setStatus("Loading prompt registry...");

    try {
      const promptResponse = await fetch(PROMPTS_API_ROUTE);
      const promptData = await promptResponse.json();

      if (!promptResponse.ok) {
        throw new Error(promptData.error || "Unable to load prompt registry.");
      }

      fillPrompts(promptData);
      setStatusState("success");
      setStatus("Prompt registry loaded.");
    } catch (error) {
      setStatusState("error");
      setStatus(error.message || "Unable to load prompt registry.");
    }
  }

  async function savePrompts(promptsPayload) {
    const promptResponse = await fetch(PROMPTS_API_ROUTE, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(promptsPayload),
    });
    const promptData = await promptResponse.json();

    if (!promptResponse.ok) {
      throw new Error(promptData.error || "Unable to save prompt registry.");
    }

    fillPrompts(promptData);
  }

  async function saveSettings(event) {
    event.preventDefault();
    setSaving(true);
    setStatusState("info");
    setStatus("Saving local settings and prompt registry...");

    try {
      const promptRegistry = readPromptRegistry();
      const settingsResponse = await fetch("/api/settings", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(readForm()),
      });
      const settingsData = await settingsResponse.json();

      if (!settingsResponse.ok) {
        throw new Error(settingsData.error || "Unable to save settings.");
      }

      fillForm(settingsData);
      await savePrompts(promptRegistry);
      setStatusState("success");
      setStatus("Settings and prompt registry saved.");
    } catch (error) {
      setStatusState("error");
      setStatus(error.message || "Unable to save settings.");
    } finally {
      setSaving(false);
    }
  }

  form.addEventListener("click", (event) => {
    const addButton = event.target.closest("[data-list-add]");
    if (addButton) {
      event.preventDefault();
      const fieldName = addButton.dataset.listAdd;
      const container = document.querySelector(`[data-list-editor="${fieldName}"]`);
      if (container) {
        container.appendChild(createListRow(fieldName));
        const lastInput = container.querySelector(".list-row:last-child input");
        if (lastInput) {
          lastInput.focus();
        }
      }
      return;
    }

    const removeButton = event.target.closest("[data-list-remove]");
    if (removeButton) {
      event.preventDefault();
      const fieldName = removeButton.dataset.listRemove;
      const row = removeButton.closest(".list-row");
      if (row) {
        row.remove();
        ensureListEditorHasRow(fieldName);
      }
    }
  });

  form.addEventListener("submit", saveSettings);

  void (async () => {
    setStatusState("info");
    setStatus("Loading local settings and prompt registry...");
    await loadSettings();
    await loadPrompts();
  })();
}
