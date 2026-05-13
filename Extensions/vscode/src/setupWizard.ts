import * as vscode from "vscode";
import * as http from "http";

interface DetectedTool {
  name: string;
  configHint: string;
  detected: boolean;
}

export class SetupWizard {
  static async run(context: vscode.ExtensionContext): Promise<void> {
    const config = vscode.workspace.getConfiguration("aurabook");
    const proxyUrl = config.get("proxyUrl", "http://127.0.0.1:8080");

    const proxyAlive = await checkHealth(proxyUrl);

    if (!proxyAlive) {
      const action = await vscode.window.showWarningMessage(
        "AuraBook proxy is not running. Start it first, then re-run setup.",
        "Show Install Instructions",
        "Cancel"
      );
      if (action === "Show Install Instructions") {
        vscode.env.openExternal(
          vscode.Uri.parse("https://github.com/ManzilS/full-book#quickstart")
        );
      }
      return;
    }

    const mode = await vscode.window.showQuickPick(
      [
        {
          label: "Local Only",
          description: "All data stays on your machine — zero cloud calls",
          value: "local_only",
        },
        {
          label: "Hybrid",
          description:
            "Local brain + cloud community knowledge (recommended)",
          value: "hybrid",
        },
        {
          label: "Cloud Only",
          description: "Full cloud backend — community-powered fixes",
          value: "cloud_only",
        },
      ],
      { placeHolder: "Choose your connectivity mode" }
    );

    if (!mode) return;

    await config.update(
      "connectivityMode",
      mode.value,
      vscode.ConfigurationTarget.Global
    );

    const tools = detectTools();
    const detectedNames = tools
      .filter((t) => t.detected)
      .map((t) => t.name)
      .join(", ");

    const configSteps = tools
      .filter((t) => t.detected)
      .map((t) => `  ${t.name}: ${t.configHint}`)
      .join("\n");

    const panel = vscode.window.createWebviewPanel(
      "aurabook.setup",
      "AuraBook Setup",
      vscode.ViewColumn.One,
      {}
    );

    panel.webview.html = getSetupHtml({
      mode: mode.label,
      proxyUrl,
      detectedTools: detectedNames || "none detected",
      configSteps: configSteps || "  No AI tools detected in PATH",
      connected: true,
    });

    vscode.window.showInformationMessage(
      `AuraBook configured in ${mode.label} mode. ${
        detectedNames
          ? `Detected: ${detectedNames}`
          : "Point your AI tools at " + proxyUrl
      }`
    );
  }
}

function detectTools(): DetectedTool[] {
  const envPath = process.env.PATH ?? "";
  const lower = envPath.toLowerCase();
  return [
    {
      name: "Claude Code",
      configHint: "claude config set apiBase http://127.0.0.1:8080",
      detected:
        lower.includes("claude") || !!process.env.CLAUDE_CODE_USE_BEDROCK,
    },
    {
      name: "Codex",
      configHint: "export OPENAI_BASE_URL=http://127.0.0.1:8080/v1",
      detected: lower.includes("codex"),
    },
    {
      name: "Cursor",
      configHint:
        'Settings > Models > OpenAI API Base: "http://127.0.0.1:8080/v1"',
      detected: lower.includes("cursor"),
    },
    {
      name: "Aider",
      configHint: "export OPENAI_API_BASE=http://127.0.0.1:8080/v1",
      detected: lower.includes("aider"),
    },
    {
      name: "Continue",
      configHint:
        'config.json > "models" > "apiBase": "http://127.0.0.1:8080/v1"',
      detected: lower.includes("continue"),
    },
  ];
}

async function checkHealth(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(`${url}/health`, { timeout: 2000 }, (res) => {
      resolve(res.statusCode === 200);
      res.resume();
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

function getSetupHtml(opts: {
  mode: string;
  proxyUrl: string;
  detectedTools: string;
  configSteps: string;
  connected: boolean;
}): string {
  return `<!DOCTYPE html>
<html>
<head>
  <style>
    body { font-family: var(--vscode-font-family); padding: 24px; color: var(--vscode-foreground); background: var(--vscode-editor-background); }
    h1 { font-size: 1.6em; margin-bottom: 8px; }
    .status { padding: 12px; border-radius: 6px; margin: 16px 0; }
    .ok { background: var(--vscode-testing-iconPassed); color: #fff; }
    .section { margin: 20px 0; }
    .section h2 { font-size: 1.1em; margin-bottom: 8px; }
    code { background: var(--vscode-textCodeBlock-background); padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }
    pre { background: var(--vscode-textCodeBlock-background); padding: 12px; border-radius: 6px; overflow-x: auto; }
  </style>
</head>
<body>
  <h1>AuraBook Setup Complete</h1>
  <div class="status ok">Proxy connected at <code>${opts.proxyUrl}</code></div>

  <div class="section">
    <h2>Mode: ${opts.mode}</h2>
    <p>Detected AI tools: <strong>${opts.detectedTools}</strong></p>
  </div>

  <div class="section">
    <h2>Configure Your Tools</h2>
    <p>Point each tool's API base URL at the AuraBook proxy:</p>
    <pre>${opts.configSteps}</pre>
  </div>

  <div class="section">
    <h2>What Happens Next</h2>
    <ul>
      <li>AuraBook intercepts AI requests and injects community fixes</li>
      <li>Error-to-success trajectories are captured automatically</li>
      <li>Check the status bar for live injection counts</li>
    </ul>
  </div>
</body>
</html>`;
}
