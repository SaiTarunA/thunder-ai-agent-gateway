Quarterly Infrastructure Report
================================

This report summarizes platform capacity, notable changes, and planned
work for the third quarter.

Overview
--------

Total cluster capacity grew by 18 percent this quarter, driven mainly
by the expansion of the eu-central-1 region.

- Managed compute across all four production regions
- Scheduled batch workloads running on shared nodes
- Reserved capacity held for failover

Capacity by Region
-------------------

.. list-table::
   :header-rows: 1

   * - Region
     - Nodes
     - Status
   * - us-east-1
     - 120
     - Healthy
   * - us-west-2
     - 95
     - Healthy

Health check snippet::

    def is_healthy(utilization):
        return utilization < 0.85

Next Steps
----------

Three actions are proposed for the coming quarter, in priority order.

1. Add twelve nodes to eu-central-1
2. Migrate batch workloads off ap-south-1
3. Review reserved failover capacity
