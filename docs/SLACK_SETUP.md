# Slack HITL connector — setup

One-time steps to enable the optional Slack approval connector. Nothing here is required for
the app to run — without these env vars, the Connectors tab shows the Slack toggle greyed out
and the app behaves exactly as it does today.

For this project workspace, the Slack app is already registered and installed. You only need
to put the resulting token, signing secret, and channel ID into `backend/.env`, restart the
backend, then enable Slack in the Connectors tab.

For a fresh workspace:

1. Create an app at <https://api.slack.com/apps> in your workspace.
2. **OAuth & Permissions** → Bot Token Scopes → add `chat:write`. Click **Install to
   Workspace**, then copy the **Bot User OAuth Token** (`xoxb-...`).
3. **Basic Information** → copy the **Signing Secret**.
4. Create (or choose) the channel approvals should post to, invite the bot
   (`/invite @YourBotName`), then open the channel details and copy its **Channel ID**
   (`C...`).
5. **Interactivity & Shortcuts** → toggle on → set **Request URL** to
   `https://<your-tunnel>/api/slack/interactions`.
6. For local development, expose your backend with `ngrok http 8000` and use the printed
   `https://*.ngrok.io` URL as `<your-tunnel>` above.
7. Set these three variables in `backend/.env` (see `.env.example`):

   ```env
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_SIGNING_SECRET=...
   SLACK_APPROVALS_CHANNEL_ID=C...
   ```

8. Restart the backend, open the app's **Connectors** tab, and click the Slack toggle to
   **Enabled**.
9. Request a booking in the app — the approval message should appear in the configured
   channel within a few seconds.

## Why this adapter is intentionally narrow

`app/adapters/slack_hitl.py` hand-rolls Slack signature verification and Block Kit message
building because the deliverable only needs one Slack approval message and one signed callback.
That keeps the protocol easy to explain in a take-home review.

If this grows into multiple chat connectors, the planned swap is
[`chat-sdk-python`](https://github.com/Chinchill-AI/chat-sdk-python): trusted prior work from a
former Chinchill-AI colleague with 30+ years of enterprise SWE experience. It already has Slack
webhook verification and cross-platform card/button primitives, so Discord, Teams, and other
chat surfaces should be a module swap around the current `notify_pending_approval` /
`resolve_approve` / `resolve_reject` boundary rather than a rewrite.
