// Quarterly infrastructure report helper.

#include <string>

class RegionReport {
public:
    RegionReport(std::string name, int nodes, double utilization)
        : name_(std::move(name)), nodes_(nodes), utilization_(utilization) {}

    bool IsHealthy() const { return utilization_ < 0.85; }

private:
    std::string name_;
    int nodes_;
    double utilization_;
};
