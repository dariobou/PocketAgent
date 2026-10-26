/**
 * GhostPilot Edge Doorbell & Cron Gateway for Cloudflare Workers
 * - Handles incoming Telegram Webhooks when no runner is active
 * - Dispatches ephemeral GitHub Actions workflows
 * - Runs a Cron Trigger (every 15 min) to guarantee Webhook self-healing
 */

export default {
  /**
   * HTTP Webhook Handler
   */
  async fetch(request, env, ctx) {
    // Only accept POST requests from Telegram
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    // 1. Security Check: Validate Secret Token Header
    const secretToken = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (env.TELEGRAM_WEBHOOK_SECRET && secretToken !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    let payload;
    try {
      payload = await request.json();
    } catch (e) {
      return new Response("Invalid JSON", { status: 400 });
    }

    const message = payload.message || payload.edited_message;
    if (!message || !message.from || !message.text) {
      return new Response("OK", { status: 200 });
    }

    const userId = String(message.from.id);
    const chatId = message.chat.id;
    const text = message.text.trim();

    // 2. Validate Allowed Telegram Users
    const allowedUsers = (env.TELEGRAM_ALLOWED_USER_IDS || "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);

    if (allowedUsers.length > 0 && !allowedUsers.includes(userId)) {
      console.warn(`Unauthorized access attempt by Telegram User ID: ${userId}`);
      return new Response("OK", { status: 200 });
    }

    // 3. Parse Quick Commands/Prefixes (e.g. "/repo owner/repo", "/agent codex")
    let targetRepo = env.DEFAULT_TARGET_REPO || "";
    let agent = "claude";
    let initialPrompt = text;

    if (text.startsWith("/repo ")) {
      const parts = text.split(" ");
      if (parts[1]) {
        targetRepo = parts[1];
        initialPrompt = parts.slice(2).join(" ") || "Repository switched.";
      }
    } else if (text.startsWith("/agent ")) {
      const parts = text.split(" ");
      if (parts[1]) {
        agent = parts[1].toLowerCase();
        initialPrompt = parts.slice(2).join(" ") || `Switched agent to ${agent}.`;
      }
    }

    // 4. Send Instant Telegram Reply to mobile
    const telegramApi = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}`;
    ctx.waitUntil(
      fetch(`${telegramApi}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId,
          text: "🚀 Waking up GhostPilot ephemeral sandbox on GitHub Actions... (takes ~15s)",
        }),
      }).catch((err) => console.error("Failed to send wake-up ack:", err))
    );

    // 5. Trigger GitHub Actions workflow_dispatch
    const ghUrl = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/ghostpilot-runtime.yml/dispatches`;
    const ghPayload = {
      ref: env.GH_BRANCH || "main",
      inputs: {
        chat_id: String(chatId),
        initial_prompt: initialPrompt,
        target_repo: targetRepo,
        agent: agent,
      },
    };

    const ghResp = await fetch(ghUrl, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GH_DISPATCH_TOKEN}`,
        "User-Agent": "GhostPilot-Cloudflare-Worker",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify(ghPayload),
    });

    if (!ghResp.ok) {
      const errorText = await ghResp.text();
      console.error(`GitHub Actions dispatch failed (${ghResp.status}):`, errorText);
      await fetch(`${telegramApi}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId,
          text: `❌ Failed to trigger GitHub Actions sandbox (${ghResp.status}). Check GH_DISPATCH_TOKEN permissions.`,
        }),
      });
      return new Response("GitHub dispatch error", { status: 500 });
    }

    return new Response("Triggered", { status: 200 });
  },

  /**
   * Cron Self-Healing Trigger (every 15 min: "*/15 * * * *")
   * Ensures Telegram Webhook points back to Cloudflare Worker if no sandbox is running.
   */
  async scheduled(event, env, ctx) {
    if (!env.GH_OWNER || !env.GH_REPO || !env.GH_DISPATCH_TOKEN || !env.WORKER_PUBLIC_URL) {
      return;
    }

    const runsUrl = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/ghostpilot-runtime.yml/runs?status=in_progress`;
    try {
      const res = await fetch(runsUrl, {
        headers: {
          "Authorization": `Bearer ${env.GH_DISPATCH_TOKEN}`,
          "User-Agent": "GhostPilot-Cloudflare-Worker",
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
        },
      });

      if (!res.ok) {
        console.error("Failed to query workflow runs:", res.status);
        return;
      }

      const data = await res.json();
      const inProgressCount = data.total_count || 0;

      // If no workflow is in progress, self-heal and point webhook back to this worker
      if (inProgressCount === 0) {
        const setWebhookUrl = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/setWebhook`;
        const webhookPayload = {
          url: env.WORKER_PUBLIC_URL,
          secret_token: env.TELEGRAM_WEBHOOK_SECRET || "",
        };

        const whResp = await fetch(setWebhookUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(webhookPayload),
        });
        const whData = await whResp.json();
        console.log("Self-healing Webhook restore result:", whData);
      }
    } catch (err) {
      console.error("Cron self-healing execution error:", err);
    }
  },
};
