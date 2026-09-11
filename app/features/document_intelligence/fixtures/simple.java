// Quarterly infrastructure report helper.

package com.example.infra;

public class RegionReport {
    private final String name;
    private final int nodes;
    private final double utilization;

    public RegionReport(String name, int nodes, double utilization) {
        this.name = name;
        this.nodes = nodes;
        this.utilization = utilization;
    }

    public boolean isHealthy() {
        return this.utilization < 0.85;
    }
}
