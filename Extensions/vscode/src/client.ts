import * as http from "http";

export interface ProxyStatus {
  connected: boolean;
  mode: string;
  notesInjected: number;
  interventions: number;
  trajectoriesCaptured: number;
  cloudConnected: boolean;
  noteCount: number;
  error?: string;
}

export interface SessionStats {
  interventions: number;
  notesInjected: number;
  trajectoriesCaptured: number;
  uptimeSeconds: number;
}

export class AuraBookClient {
  private readonly proxyUrl: string;
  private readonly cloudUrl: string;
  private readonly startedAt = Date.now();
  private _cumulative = { interventions: 0, notesInjected: 0, trajectories: 0 };

  constructor(proxyUrl: string, cloudUrl: string) {
    this.proxyUrl = proxyUrl.replace(/\/+$/, "");
    this.cloudUrl = cloudUrl.replace(/\/+$/, "");
  }

  get sessionStats(): SessionStats {
    return {
      interventions: this._cumulative.interventions,
      notesInjected: this._cumulative.notesInjected,
      trajectoriesCaptured: this._cumulative.trajectories,
      uptimeSeconds: Math.round((Date.now() - this.startedAt) / 1000),
    };
  }

  async getStatus(): Promise<ProxyStatus> {
    const [proxy, cloud] = await Promise.allSettled([
      this.fetchJson(`${this.proxyUrl}/health`),
      this.fetchJson(`${this.cloudUrl}/health`),
    ]);

    const proxyOk = proxy.status === "fulfilled";
    const proxyData = proxyOk ? proxy.value : {};
    const cloudOk = cloud.status === "fulfilled";
    const cloudData = cloudOk ? cloud.value : {};

    const status: ProxyStatus = {
      connected: proxyOk,
      mode: proxyData.connectivity_mode ?? "unknown",
      notesInjected: proxyData.notes_injected ?? 0,
      interventions: proxyData.interventions ?? 0,
      trajectoriesCaptured: proxyData.trajectories_captured ?? 0,
      cloudConnected: cloudOk,
      noteCount: cloudData.note_count ?? 0,
    };

    if (!proxyOk) {
      status.error = "Proxy not reachable";
    }

    this._cumulative.interventions = Math.max(
      this._cumulative.interventions,
      status.interventions
    );
    this._cumulative.notesInjected = Math.max(
      this._cumulative.notesInjected,
      status.notesInjected
    );
    this._cumulative.trajectories = Math.max(
      this._cumulative.trajectories,
      status.trajectoriesCaptured
    );

    return status;
  }

  private fetchJson(url: string): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      const req = http.get(url, { timeout: 3000 }, (res) => {
        let data = "";
        res.on("data", (chunk: Buffer) => {
          data += chunk.toString();
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch {
            reject(new Error("Invalid JSON"));
          }
        });
      });
      req.on("error", reject);
      req.on("timeout", () => {
        req.destroy();
        reject(new Error("Timeout"));
      });
    });
  }
}
