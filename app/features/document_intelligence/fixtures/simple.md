# Quarterly Infrastructure Report

This report summarizes platform capacity, notable changes, and planned work for the third quarter. It is intended for the infrastructure team and their partners in product engineering.

## Overview

Total cluster capacity grew by 18 percent this quarter, driven mainly by the expansion of the eu-central-1 region. Request volume grew faster, at 26 percent, so average utilization rose despite the added hardware.

No customer-visible outages were recorded. Two internal incidents were raised, both resolved within the same working day, and neither breached the error budget.

### Scope

The figures below cover managed compute only. Object storage, the data warehouse, and third-party services are reported separately and are out of scope for this document.

- Managed compute across all four production regions
- Scheduled batch workloads running on shared nodes
- Internal staging clusters, excluding developer sandboxes
- Reserved capacity held for failover

## Capacity by Region

The table below shows node counts and average utilization for the final week of the quarter. Utilization is measured as mean CPU allocation during business hours.

| Region | Nodes | Utilization | Status |
| --- | --- | --- | --- |
| us-east-1 | 48 | 72% | Healthy |
| us-west-2 | 32 | 64% | Healthy |
| eu-central-1 | 24 | 88% | At capacity |
| ap-south-1 | 16 | 41% | Underused |

## Next Steps

Three actions are proposed for the coming quarter, in priority order.

1. Add twelve nodes to eu-central-1 before the end of month one.
2. Migrate batch workloads off ap-south-1 and consolidate them in us-west-2.
3. Review reserved failover capacity, which has not been exercised in three quarters.

Questions and corrections should go to the platform team. A detailed cost breakdown is available on request.
