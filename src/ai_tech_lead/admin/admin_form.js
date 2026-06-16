import {
  FIELD_PLACEHOLDERS,
  LIST_FIELD_CONFIG,
  LIST_FIELDS,
  PROMPT_CLASS_OPTIONS,
  PROMPT_TEMPLATE_PLACEHOLDER,
  PROMPTS_API_ROUTE,
} from "./admin_config.js";

export function bootstrapAdminForm() {
  const form = document.querySelector("#settings-form");
  const statusElement = document.querySelector("#status");
  const saveButton = document.querySelector("#save-settings");
  const promptEditor = document.querySelector("[data-prompt-editor]");

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

  function getPromptTemplateValue(promptCard) {
    return promptCard.querySelector('[data-prompt-field="template"]').value;
  }

  function getPromptField(promptCard, fieldName) {
    const field = promptCard.querySelector(`[data-prompt-field="${fieldName}"]`);
    if (!field) {
      throw new Error(`Prompt editor field "${fieldName}" was not found.`);
    }

    return field;
  }

  function splitListValues(text) {
    return text
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function extractTemplateKeys(templateText) {
    const placeholderPattern = /\{([A-Za-z0-9_]+)\}/g;
    const placeholderKeys = [];
    const seenKeys = new Set();

    for (const match of templateText.matchAll(placeholderPattern)) {
      const placeholderKey = match[1];
      if (seenKeys.has(placeholderKey)) {
        continue;
      }
      seenKeys.add(placeholderKey);
      placeholderKeys.push(placeholderKey);
    }

    return placeholderKeys;
  }

  function createChipList(values, emptyLabel, formatValue = (value) => value) {
    const chipList = document.createElement("div");
    chipList.className = "prompt-chip-list";

    if (values.length === 0) {
      const emptyChip = document.createElement("span");
      emptyChip.className = "prompt-chip prompt-chip-empty";
      emptyChip.textContent = emptyLabel;
      chipList.appendChild(emptyChip);
      return chipList;
    }

    for (const value of values) {
      const chip = document.createElement("span");
      chip.className = "prompt-chip";
      chip.textContent = formatValue(value);
      chipList.appendChild(chip);
    }

    return chipList;
  }

  function createFieldBlock(fieldName, labelText, helpText, fieldElement) {
    const label = document.createElement("label");
    label.dataset.promptBlock = fieldName;

    const title = document.createElement("span");
    title.textContent = labelText;
    label.appendChild(title);

    if (helpText) {
      const help = document.createElement("span");
      help.className = "field-help";
      help.textContent = helpText;
      label.appendChild(help);
    }

    label.appendChild(fieldElement);
    return label;
  }

  function createPromptSelect(value) {
    const select = document.createElement("select");
    select.dataset.promptField = "prompt_class";

    for (const option of PROMPT_CLASS_OPTIONS) {
      const optionElement = document.createElement("option");
      optionElement.value = option.value;
      optionElement.textContent = option.label;
      select.appendChild(optionElement);
    }

    select.value = value || PROMPT_CLASS_OPTIONS[0].value;
    return select;
  }

  function createPromptCard(prompt = {}) {
    const card = document.createElement("article");
    card.className = "prompt-card";
    card.dataset.promptCard = "true";

    const header = document.createElement("div");
    header.className = "prompt-card-header";

    const headerCopy = document.createElement("div");
    headerCopy.className = "prompt-card-header-copy";

    const eyebrow = document.createElement("div");
    eyebrow.className = "prompt-card-eyebrow";
    eyebrow.textContent = "Prompt linkage";

    const title = document.createElement("div");
    title.className = "prompt-card-title";
    title.dataset.promptCardTitle = "true";

    const summary = document.createElement("div");
    summary.className = "prompt-card-summary";
    summary.dataset.promptCardSummary = "true";

    const keyBadge = document.createElement("div");
    keyBadge.className = "prompt-key-badge";
    keyBadge.dataset.promptKeyBadge = "true";

    headerCopy.append(eyebrow, title, summary, keyBadge);

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "prompt-remove";
    removeButton.dataset.promptRemove = "true";
    removeButton.textContent = "Delete prompt";
    removeButton.hidden = true;

    header.append(headerCopy, removeButton);

    const grid = document.createElement("div");
    grid.className = "prompt-card-grid";

    const keyInput = document.createElement("input");
    keyInput.type = "text";
    keyInput.dataset.promptField = "key";
    keyInput.placeholder = "risk_review";
    const keyField = createFieldBlock(
      "prompt-key",
      "Prompt key",
      "This is the key code uses to load the prompt. Changing it changes the linkage.",
      keyInput,
    );

    const classSelect = createPromptSelect(prompt.prompt_class);
    const classField = createFieldBlock(
      "prompt-class",
      "Prompt class",
      "A short label that describes how the prompt is used.",
      classSelect,
    );

    const purposeInput = document.createElement("input");
    purposeInput.type = "text";
    purposeInput.dataset.promptField = "purpose";
    purposeInput.placeholder = "Risk review decision prompt";
    const purposeField = createFieldBlock(
      "prompt-purpose",
      "Purpose",
      "Plain-English description of what this prompt is for.",
      purposeInput,
    );
    purposeField.classList.add("prompt-field-wide");

    const usedByGroup = document.createElement("div");
    usedByGroup.className = "prompt-linkage-group prompt-field-wide";

    const usedByHeading = document.createElement("div");
    usedByHeading.className = "field-heading";
    usedByHeading.textContent = "Used by";

    const usedByHelp = document.createElement("span");
    usedByHelp.className = "field-help";
    usedByHelp.textContent = "These files load this prompt. Put one file path or module per line.";

    const usedBySummary = document.createElement("div");
    usedBySummary.className = "prompt-linkage-summary";
    usedBySummary.dataset.promptUsedBySummary = "true";

    const usedByTextarea = document.createElement("textarea");
    usedByTextarea.dataset.promptField = "used_by";
    usedByTextarea.rows = 3;
    usedByTextarea.placeholder = "instruction_assembler.py\nrisk_reviewer.py";

    usedByGroup.append(usedByHeading, usedByHelp, usedBySummary, usedByTextarea);

    const templateField = document.createElement("label");
    templateField.className = "prompt-template-field prompt-field-wide";
    templateField.dataset.promptBlock = "template";

    const templateHeading = document.createElement("span");
    templateHeading.textContent = "Prompt text";

    const templateHelp = document.createElement("span");
    templateHelp.className = "field-help";
    templateHelp.textContent =
      "Write the prompt as normal text. Placeholders like {request}, {brief}, and {task_feedback} stay visible.";

    const templateTextarea = document.createElement("textarea");
    templateTextarea.dataset.promptField = "template";
    templateTextarea.className = "prompt-template";
    templateTextarea.rows = 12;
    templateTextarea.spellcheck = false;
    templateTextarea.placeholder = PROMPT_TEMPLATE_PLACEHOLDER;

    const placeholderHeading = document.createElement("div");
    placeholderHeading.className = "field-heading";
    placeholderHeading.textContent = "Placeholders found";

    const placeholderSummary = document.createElement("div");
    placeholderSummary.className = "prompt-placeholder-summary";
    placeholderSummary.dataset.promptPlaceholderSummary = "true";

    templateField.append(templateHeading, templateHelp, templateTextarea, placeholderHeading, placeholderSummary);

    grid.append(keyField, classField, purposeField, usedByGroup, templateField);
    card.append(header, grid);

    keyInput.value = prompt.key || "";
    purposeInput.value = prompt.purpose || "";
    usedByTextarea.value = (prompt.used_by || []).join("\n");
    templateTextarea.value = prompt.template || "";

    syncPromptCard(card);
    return card;
  }

  function syncPromptCard(promptCard) {
    const keyInput = getPromptField(promptCard, "key");
    const purposeInput = getPromptField(promptCard, "purpose");
    const usedByTextarea = getPromptField(promptCard, "used_by");
    const templateTextarea = getPromptField(promptCard, "template");

    const key = keyInput.value.trim();
    const purpose = purposeInput.value.trim();
    const usedBy = splitListValues(usedByTextarea.value);
    const placeholders = extractTemplateKeys(getPromptTemplateValue(promptCard));

    const titleElement = promptCard.querySelector("[data-prompt-card-title]");
    if (titleElement) {
      titleElement.textContent = key || "Untitled prompt";
    }

    const summaryElement = promptCard.querySelector("[data-prompt-card-summary]");
    if (summaryElement) {
      summaryElement.textContent = key
        ? "Code loads this prompt by the key above."
        : "Add a key to connect this prompt to code.";
    }

    const keyBadge = promptCard.querySelector("[data-prompt-key-badge]");
    if (keyBadge) {
      keyBadge.textContent = key ? `Link key: ${key}` : "Link key needed";
    }

    const usedBySummary = promptCard.querySelector("[data-prompt-used-by-summary]");
    if (usedBySummary) {
      const usedByChips = createChipList(usedBy, "No code linkage yet");
      usedBySummary.replaceChildren(...Array.from(usedByChips.childNodes));
    }

    const placeholderSummary = promptCard.querySelector("[data-prompt-placeholder-summary]");
    if (placeholderSummary) {
      const placeholderChips = createChipList(
        placeholders,
        "No placeholders found",
        (value) => `{${value}}`,
      );
      placeholderSummary.replaceChildren(...Array.from(placeholderChips.childNodes));
    }

    if (!purpose) {
      purposeInput.placeholder = "Describe what this prompt is for";
    }

    if (!templateTextarea.value.trim()) {
      templateTextarea.placeholder = PROMPT_TEMPLATE_PLACEHOLDER;
    }
  }

  function createPromptCardList(prompts) {
    if (!promptEditor) {
      return;
    }

    promptEditor.replaceChildren();

    if (prompts.length === 0) {
      promptEditor.appendChild(createPromptCard(blankPrompt()));
      return;
    }

    for (const prompt of prompts) {
      promptEditor.appendChild(createPromptCard(prompt));
    }
  }

  function blankPrompt() {
    return {
      key: "",
      prompt_class: PROMPT_CLASS_OPTIONS[0]?.value || "instruction",
      purpose: "",
      used_by: [],
      template: "",
    };
  }

  function readPromptCard(promptCard) {
    const key = getPromptField(promptCard, "key").value.trim();
    if (!key) {
      throw new Error("Each prompt needs a key.");
    }

    const promptClass = getPromptField(promptCard, "prompt_class").value.trim();
    if (!promptClass) {
      throw new Error(`Prompt "${key}" needs a prompt class.`);
    }

    const purpose = getPromptField(promptCard, "purpose").value.trim();
    if (!purpose) {
      throw new Error(`Prompt "${key}" needs a purpose.`);
    }

    const template = getPromptField(promptCard, "template").value.trim();
    if (!template) {
      throw new Error(`Prompt "${key}" needs template text.`);
    }

    return {
      key,
      prompt_class: promptClass,
      purpose,
      used_by: splitListValues(getPromptField(promptCard, "used_by").value),
      template,
    };
  }

  function readPromptRegistry() {
    const promptCards = [...document.querySelectorAll("[data-prompt-card]")];
    if (promptCards.length === 0) {
      throw new Error("Prompt registry cannot be empty.");
    }

    const seenKeys = new Set();
    const prompts = promptCards.map((promptCard) => {
      const prompt = readPromptCard(promptCard);
      if (seenKeys.has(prompt.key)) {
        throw new Error(`Duplicate prompt key: ${prompt.key}`);
      }
      seenKeys.add(prompt.key);
      return prompt;
    });

    return {
      prompts,
    };
  }

  function ensurePromptEditorHasCard() {
    if (!promptEditor) {
      return;
    }

    if (promptEditor.querySelector("[data-prompt-card]")) {
      return;
    }

    promptEditor.appendChild(createPromptCard(blankPrompt()));
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
    const prompts = Array.isArray(promptsPayload.prompts) ? promptsPayload.prompts : [];
    createPromptCardList(prompts);
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

    const promptAddButton = event.target.closest("[data-prompt-add]");
    if (promptAddButton) {
      event.preventDefault();
      if (promptEditor) {
        promptEditor.appendChild(createPromptCard(blankPrompt()));
        const lastPromptCard = promptEditor.lastElementChild;
        const firstField = lastPromptCard?.querySelector('[data-prompt-field="key"]');
        if (firstField instanceof HTMLElement) {
          firstField.focus();
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
      return;
    }

    const promptRemoveButton = event.target.closest("[data-prompt-remove]");
    if (promptRemoveButton) {
      event.preventDefault();
      const promptCard = promptRemoveButton.closest("[data-prompt-card]");
      if (promptCard) {
        promptCard.remove();
        ensurePromptEditorHasCard();
      }
    }
  });

  form.addEventListener("input", (event) => {
    const promptField = event.target.closest("[data-prompt-field]");
    if (!promptField) {
      return;
    }

    const promptCard = promptField.closest("[data-prompt-card]");
    if (promptCard) {
      syncPromptCard(promptCard);
    }
  });

  form.addEventListener("submit", saveSettings);

  void (async () => {
    setStatusState("info");
    setStatus("Loading local settings and prompt registry...");
    await loadSettings();
    await loadPrompts();
    ensurePromptEditorHasCard();
  })();
}
