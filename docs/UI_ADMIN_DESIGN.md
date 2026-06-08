# Admin UI Design Notes

## Purpose

The admin UI is the local control panel for the AI Technical Lead Orchestrator. It should make runtime configuration understandable and safe without becoming the main product.

## Design Goal

Make the page clear enough that the human can safely control Telegram, Codex execution, settings, and prompts without reading code.

## Layout

Use a simple two-zone layout:

- A short status/header area explaining what is running and what requires restart.
- Grouped cards for Runtime, Telegram, Safety, Directories, Brief Content, and Prompts.

## Safety UX

The `Execute coding agent` toggle is dangerous because it allows Codex to modify files. It must be visually distinct and clearly labelled as a real execution switch.

The `Enable Telegram operator` toggle should explain that changes apply on restart until hot reload exists.

## Field Behaviour

HTML owns structure. CSS owns layout and visual emphasis. JavaScript owns behaviour and API calls.

Do not add frontend framework complexity. Keep the local admin page static and understandable.

## Near-Term Improvements

Improve spacing, visual hierarchy, warning states, button styling, list editor readability, and status messages. Do not change backend behaviour as part of this UI cleanup.

## Telegram Message UX

Telegram messages are phone-first. Approval and status messages should be short, human-readable, and action-oriented. Do not show raw graph state, raw internal prompts, duplicated request bodies, or long approval reasons in Telegram. Keep detailed diagnostic text in logs or graph state.

## Admin Save UX

The save action must stay visible. Keep the save bar fixed at the bottom of the screen so settings can be saved without scrolling to the end of the form.

List row deletion should use a clear trash-can affordance with text, not a vague `Remove` label alone.
