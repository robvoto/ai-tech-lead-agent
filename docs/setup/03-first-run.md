# First Run

## Goal

Create the smallest useful first run before expanding the architecture.

## First run target

The first runnable prototype should prove:

- Telegram message input works
- The assistant can create a small task brief
- One model call can be made
- Token or cost metadata is logged
- State can be saved locally
- One approval checkpoint exists before a risky or expensive action

## Out of scope for first run

- Full backlog automation
- Full repository scanning
- Complex memory
- RAG
- Multiple autonomous agents

## Success check

A user can send a Telegram message and receive a controlled assistant response, with a local log entry recording what happened.
