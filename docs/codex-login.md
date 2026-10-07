# Codex login and GPT models

Open **Settings → Models → Codex / GPT** (also available under **Coding agents**).

- **Sign in to Codex** opens the official browser sign-in flow. Finish it in the browser; the panel updates automatically. An existing Codex sign-in is recognized.
- **Switch Codex account** starts a new sign-in. **Cancel sign-in** cancels the pending flow without logging out the existing account.
- **Refresh connection & models** reads the account and paginated model catalog from Codex. All returned models are included, with hidden entries labeled. Catalog membership does not guarantee account access.
- Choose the default model for new Codex launches. Per-workspace model choices take precedence. Existing terminals retain their running model; launch a new terminal or use Codex's own model picker to change it.
- **Test GPT** sends a short, read-only verification request for the selected model. Only a completed inference turn counts as success; this uses the account's normal model usage.

The Workspace composer also loads the model catalog and reasoning levels dynamically. Model and reasoning choices are passed to both headless runs and terminal launches.

Credentials remain in Codex's own authentication store. This integration does not read or copy auth tokens. It requires an installed Codex CLI supporting the [official app-server protocol](https://learn.chatgpt.com/docs/app-server).

This sign-in covers the Codex coding agent. Image-generation providers and other direct OpenAI API integrations continue to use their own configured API keys.

Validation: `backend/.venv/Scripts/python.exe -m unittest codex_account_test -v` from `backend`, and `npm run build` from `frontend`.
