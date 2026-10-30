# AgentCore Browser integration milestone

The web app and API are deliberately separate from the browser agent. The next milestone is to add a Strands agent using the official AgentCore Browser tool and drive this deployed site remotely.

Important:
- Use AgentCore Browser, not `playwright.chromium.launch()` (that starts local Chromium).
- Install/verify the current AWS-supported `bedrock-agentcore`, `strands-agents`, and `strands-agents-tools` packages and use the current official `AgentCoreBrowser` tool API for the SDK versions pinned by the project.
- Register a Playwright `page.on("dialog", handler)` for JavaScript alert/confirm/prompt cases when using Playwright directly. AgentCore Browser's supported DOM/CDP path or OS-level actions may differ for native dialogs.
- For CAPTCHA, pause for a human; do not bypass it.
- For OTP, wait for the user to enter the emailed code, then continue.
- The application audit log is not the same thing as AgentCore session recording.
