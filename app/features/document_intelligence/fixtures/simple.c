/* Quarterly infrastructure report helper. */

#include <stdbool.h>

struct region_report {
    const char *name;
    int nodes;
    double utilization;
};

bool region_is_healthy(const struct region_report *report) {
    return report->utilization < 0.85;
}
