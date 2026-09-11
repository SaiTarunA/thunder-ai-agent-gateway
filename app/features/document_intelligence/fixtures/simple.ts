// Quarterly infrastructure report helper.

interface RegionEntry {
  region: string;
  nodes: number;
  utilization: number;
}

class RegionReport {
  constructor(
    public name: string,
    public nodes: number,
    public utilization: number
  ) {}

  isHealthy(): boolean {
    return this.utilization < 0.85;
  }
}

function loadReport(entries: RegionEntry[]): RegionReport[] {
  return entries.map(
    (entry) => new RegionReport(entry.region, entry.nodes, entry.utilization)
  );
}

export { RegionReport, loadReport };
