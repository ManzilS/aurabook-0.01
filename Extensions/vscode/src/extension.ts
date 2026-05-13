import * as vscode from "vscode";
import { StatusBarManager } from "./statusBar";
import { AuraBookClient } from "./client";
import { SetupWizard } from "./setupWizard";

let statusBar: StatusBarManager | undefined;
let client: AuraBookClient | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("aurabook");
  client = new AuraBookClient(
    config.get("proxyUrl", "http://127.0.0.1:8080"),
    config.get("cloudUrl", "http://127.0.0.1:8000")
  );
  statusBar = new StatusBarManager();

  context.subscriptions.push(
    vscode.commands.registerCommand("aurabook.showDashboard", () => {
      const cloudUrl = config.get("cloudUrl", "http://127.0.0.1:8000");
      vscode.env.openExternal(vscode.Uri.parse(cloudUrl));
    }),

    vscode.commands.registerCommand("aurabook.setup", () => {
      SetupWizard.run(context);
    }),

    vscode.commands.registerCommand("aurabook.toggleMode", async () => {
      const modes = ["local_only", "hybrid", "cloud_only"] as const;
      const pick = await vscode.window.showQuickPick(
        modes.map((m) => ({
          label: m.replace("_", " "),
          description:
            m === "local_only"
              ? "All data stays on your machine"
              : m === "hybrid"
                ? "Local + cloud community knowledge"
                : "Cloud backend only",
          value: m,
        })),
        { placeHolder: "Select connectivity mode" }
      );
      if (pick) {
        await config.update(
          "connectivityMode",
          pick.value,
          vscode.ConfigurationTarget.Global
        );
        vscode.window.showInformationMessage(
          `AuraBook mode set to: ${pick.label}`
        );
      }
    }),

    vscode.commands.registerCommand("aurabook.showSessionStats", async () => {
      if (!client) return;
      const stats = client.sessionStats;
      const msg = [
        `Interventions: ${stats.interventions}`,
        `Notes injected: ${stats.notesInjected}`,
        `Trajectories captured: ${stats.trajectoriesCaptured}`,
        `Uptime: ${Math.round(stats.uptimeSeconds / 60)}m`,
      ].join("  |  ");
      vscode.window.showInformationMessage(msg);
    })
  );

  if (config.get("showStatusBar", true)) {
    statusBar.show();
    startPolling(config.get("pollingIntervalMs", 5000));
  }

  vscode.workspace.onDidChangeConfiguration((e) => {
    if (e.affectsConfiguration("aurabook")) {
      const updated = vscode.workspace.getConfiguration("aurabook");
      client = new AuraBookClient(
        updated.get("proxyUrl", "http://127.0.0.1:8080"),
        updated.get("cloudUrl", "http://127.0.0.1:8000")
      );
      if (updated.get("showStatusBar", true)) {
        statusBar?.show();
        startPolling(updated.get("pollingIntervalMs", 5000));
      } else {
        stopPolling();
        statusBar?.hide();
      }
    }
  });
}

function startPolling(intervalMs: number): void {
  stopPolling();
  poll();
  pollTimer = setInterval(poll, intervalMs);
}

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
}

async function poll(): Promise<void> {
  if (!client || !statusBar) return;
  const status = await client.getStatus();
  statusBar.update(status);
}

export function deactivate(): void {
  stopPolling();
  statusBar?.dispose();
}
