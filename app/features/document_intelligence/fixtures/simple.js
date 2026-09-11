// Quarterly infrastructure report helper.

class RegionReport {
  constructor(name, nodes, utilization) {
    this.name = name;
    this.nodes = nodes;
    this.utilization = utilization;
  }

  isHealthy() {
    return this.utilization < 0.85;
  }
}

function loadReport(entries) {
  return entries.map(
    (entry) => new RegionReport(entry.region, entry.nodes, entry.utilization)
  );
}

module.exports = { RegionReport, loadReport };
