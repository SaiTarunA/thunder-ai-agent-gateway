<?php
// Quarterly infrastructure report helper.

class RegionReport
{
    public function __construct(
        private string $name,
        private int $nodes,
        private float $utilization
    ) {
    }

    public function isHealthy(): bool
    {
        return $this->utilization < 0.85;
    }
}
