import * as vscode from "vscode";
import type { ProxyStatus } from "./client";

export class StatusBarManager {
  private readonly item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      50
    );
    this.item.command = "aurabook.showSessionStats";
    this.item.tooltip = "AuraBook — click for session stats";
    this.setDisconnected();
  }

  show(): void {
    this.item.show();
  }

  hide(): void {
    this.item.hide();
  }

  update(status: ProxyStatus): void {
    if (!status.connected) {
      this.setDisconnected();
      return;
    }

    const parts: string[] = [];

    if (status.notesInjected > 0) {
      parts.push(`${status.notesInjected} injected`);
    }
    if (status.interventions > 0) {
      parts.push(`${status.interventions} interventions`);
    }

    const icon = status.cloudConnected ? "$(globe)" : "$(database)";
    const label =
      parts.length > 0
        ? `${icon} AuraBook: ${parts.join(", ")}`
        : `${icon} AuraBook`;

    this.item.text = label;
    this.item.backgroundColor = undefined;

    const tipLines = [
      `Mode: ${status.mode}`,
      `Cloud: ${status.cloudConnected ? "connected" : "offline"}`,
      `Notes in DB: ${status.noteCount}`,
      `Trajectories captured: ${status.trajectoriesCaptured}`,
    ];
    this.item.tooltip = tipLines.join("\n");
  }

  dispose(): void {
    this.item.dispose();
  }

  private setDisconnected(): void {
    this.item.text = "$(debug-disconnect) AuraBook: offline";
    this.item.backgroundColor = new vscode.ThemeColor(
      "statusBarItem.warningBackground"
    );
    this.item.tooltip = "AuraBook proxy not reachable. Run: aurabook start";
  }
}
