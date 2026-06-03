const form = document.querySelector("#settings-form");
const statusElement = document.querySelector("#status");

const listFields = [
  "coding_agent_args",
  "allowed_directories",
  "watched_directories",
  "brief_constraints",
  "acceptance_criteria",
  "risk_notes",
];

const promptFields = [
  "execution_brief_template",
  "agent_instruction_template",
];

function setStatus(message) {
  statusElement.textContent = message;
}

function setField(name, value) {
  form.elements[name].value = value;
}

function linesFromField(name) {
  return form.elements[name].value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function fillForm(settings) {
  setField("backlog_path", settings.backlog_path);
  setField("max_runtime_minutes", settings.max_runtime_minutes);
  setField("coding_agent_command", settings.coding_agent_command);
  form.elements.execute_coding_agent.checked = settings.execute_coding_agent;

  for (const fieldName of listFields) {
    setField(fieldName, settings[fieldName].join("\n"));
  }

  setField("execution_brief_template", settings.prompts.execution_brief_template);
  setField("agent_instruction_template", settings.prompts.agent_instruction_template);
}

function readForm() {
  const settings = {
    backlog_path: form.elements.backlog_path.value.trim(),
    max_runtime_minutes: Number.parseInt(form.elements.max_runtime_minutes.value, 10),
    coding_agent_command: form.elements.coding_agent_command.value.trim(),
    execute_coding_agent: form.elements.execute_coding_agent.checked,
    prompts: {},
  };

  for (const fieldName of listFields) {
    settings[fieldName] = linesFromField(fieldName);
  }

  for (const fieldName of promptFields) {
    settings.prompts[fieldName] = form.elements[fieldName].value.trim();
  }

  return settings;
}

async function loadSettings() {
  const response = await fetch("/api/settings");
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || "Unable to load settings.");
  }

  fillForm(data);
  setStatus("");
}

async function saveSettings(event) {
  event.preventDefault();
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(readForm()),
  });
  const data = await response.json();

  if (!response.ok) {
    setStatus(data.error || "Unable to save settings.");
    return;
  }

  fillForm(data);
  setStatus("Settings saved.");
}

form.addEventListener("submit", saveSettings);
loadSettings().catch((error) => setStatus(error.message));
