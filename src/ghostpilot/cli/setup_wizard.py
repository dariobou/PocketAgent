"""Interactive local CLI setup wizard (ghostpilot-setup) to configure BYOS tokens & GitHub Secrets."""

import base64
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

console = Console()


def run_gh_secret_set(secret_name: str, secret_value: str) -> bool:
    """Upload a secret to the current GitHub repository using `gh secret set`."""
    try:
        proc = subprocess.Popen(
            ["gh", "secret", "set", secret_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(input=secret_value)
        if proc.returncode == 0:
            console.print(f"  [green]✔[/green] Secret [bold]{secret_name}[/bold] uploaded to GitHub repository.")
            return True
        else:
            console.print(f"  [red]✘[/red] Failed to set [bold]{secret_name}[/bold]: {stderr.strip()}")
            return False
    except Exception as exc:
        console.print(f"  [red]✘[/red] Error running gh: {exc}")
        return False


def check_gh_installed() -> bool:
    """Check if GitHub CLI (gh) is installed and authenticated."""
    if not shutil.which("gh"):
        console.print(
            "[red]Error:[/red] GitHub CLI (`gh`) is not installed or not in PATH.\n"
            "Please install it from https://cli.github.com/ and run `gh auth login`."
        )
        return False

    res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if res.returncode != 0:
        console.print(
            "[red]Error:[/red] GitHub CLI (`gh`) is not authenticated.\n"
            "Please run `gh auth login` before running this wizard."
        )
        return False

    return True


def detect_and_upload_byos_credentials():
    """Detect local credential files for Codex and Gemini, and guide Claude setup."""
    home = Path.home()
    console.print("\n[bold cyan]1. Detecting Local AI Subscriptions (BYOS)...[/bold cyan]")

    # 1. Claude Code
    console.print("\n[bold]• Claude Code (Anthropic Pro/Max or API):[/bold]")
    claude_token = Prompt.ask(
        "  Paste your [yellow]CLAUDE_CODE_OAUTH_TOKEN[/yellow] (from `claude setup-token`) or ANTHROPIC_API_KEY (leave empty to skip)",
        default="",
        password=True,
    )
    if claude_token.strip():
        if claude_token.startswith("sk-ant-api") or claude_token.startswith("sk-ant-"):
            if "setup-token" in claude_token or len(claude_token) > 50:
                run_gh_secret_set("CLAUDE_CODE_OAUTH_TOKEN", claude_token.strip())
            else:
                run_gh_secret_set("ANTHROPIC_API_KEY", claude_token.strip())
        else:
            run_gh_secret_set("CLAUDE_CODE_OAUTH_TOKEN", claude_token.strip())

    # 2. OpenAI Codex CLI
    console.print("\n[bold]• OpenAI Codex CLI (ChatGPT Plus/Pro or API):[/bold]")
    codex_auth_path = home / ".codex" / "auth.json"
    if codex_auth_path.exists():
        console.print(f"  [green]Found[/green] local credentials at `{codex_auth_path}`")
        if Confirm.ask("  Upload this Codex subscription to GitHub Secrets?", default=True):
            raw_bytes = codex_auth_path.read_bytes()
            b64_val = base64.b64encode(raw_bytes).decode("utf-8")
            run_gh_secret_set("CODEX_AUTH_JSON_B64", b64_val)
    else:
        openai_key = Prompt.ask("  Enter [yellow]OPENAI_API_KEY[/yellow] fallback (leave empty to skip)", default="", password=True)
        if openai_key.strip():
            run_gh_secret_set("OPENAI_API_KEY", openai_key.strip())

    # 3. Google Gemini CLI
    console.print("\n[bold]• Google Gemini CLI (OAuth or API):[/bold]")
    gemini_oauth_path = home / ".gemini" / "oauth_creds.json"
    if gemini_oauth_path.exists():
        console.print(f"  [green]Found[/green] local OAuth credentials at `{gemini_oauth_path}`")
        if Confirm.ask("  Upload this Gemini OAuth subscription to GitHub Secrets?", default=True):
            raw_bytes = gemini_oauth_path.read_bytes()
            b64_val = base64.b64encode(raw_bytes).decode("utf-8")
            run_gh_secret_set("GEMINI_OAUTH_B64", b64_val)
    else:
        gemini_key = Prompt.ask("  Enter [yellow]GEMINI_API_KEY[/yellow] fallback (leave empty to skip)", default="", password=True)
        if gemini_key.strip():
            run_gh_secret_set("GEMINI_API_KEY", gemini_key.strip())


def main():
    console.print(
        Panel.fit(
            "[bold white]GhostPilot Configuration Wizard[/bold white]\n"
            "[dim]Export subscription credentials & sync GitHub Secrets in 2 minutes[/dim]",
            border_style="cyan",
        )
    )

    if not check_gh_installed():
        sys.exit(1)

    # Telegram Credentials
    console.print("\n[bold cyan]2. Telegram & Cloudflare Configuration...[/bold cyan]")
    bot_token = Prompt.ask("Enter your [yellow]TELEGRAM_BOT_TOKEN[/yellow] (from @BotFather)", password=True)
    if bot_token.strip():
        run_gh_secret_set("TELEGRAM_BOT_TOKEN", bot_token.strip())

    allowed_ids = Prompt.ask("Enter authorized [yellow]TELEGRAM_ALLOWED_USER_IDS[/yellow] (comma-separated numbers)")
    if allowed_ids.strip():
        run_gh_secret_set("TELEGRAM_ALLOWED_USER_IDS", allowed_ids.strip())

    cf_url = Prompt.ask("Enter your [yellow]CLOUDFLARE_WORKER_URL[/yellow] (e.g. https://ghostpilot-edge.<subdomain>.workers.dev)")
    if cf_url.strip():
        run_gh_secret_set("CLOUDFLARE_WORKER_URL", cf_url.strip())

    webhook_secret = Prompt.ask("Enter a random string for [yellow]TELEGRAM_WEBHOOK_SECRET[/yellow] (or press Enter to generate)", default="")
    if not webhook_secret.strip():
        import secrets
        webhook_secret = secrets.token_hex(16)
        console.print(f"  Generated random webhook secret: `{webhook_secret}`")
    run_gh_secret_set("TELEGRAM_WEBHOOK_SECRET", webhook_secret.strip())

    # GitHub PAT for push / PRs
    console.print("\n[bold cyan]3. GitHub Personal Access Token (for PRs & push)...[/bold cyan]")
    gh_pat = Prompt.ask("Enter [yellow]GITHUB_PAT[/yellow] with `repo` / `workflow` permissions", password=True)
    if gh_pat.strip():
        run_gh_secret_set("GITHUB_PAT", gh_pat.strip())

    # BYOS Detection
    detect_and_upload_byos_credentials()

    console.print("\n[bold green]✔ GhostPilot setup complete![/bold green]")
    console.print(
        "Next steps:\n"
        "1. Deploy your Cloudflare Worker: `cd edge && wrangler deploy`\n"
        "2. Send a message to your Telegram bot to test the wake-up trigger!\n"
    )


if __name__ == "__main__":
    main()
