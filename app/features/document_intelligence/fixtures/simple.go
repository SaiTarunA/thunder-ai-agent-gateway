// Quarterly infrastructure report helper.

package infra

type RegionReport struct {
	Name        string
	Nodes       int
	Utilization float64
}

func (r RegionReport) IsHealthy() bool {
	return r.Utilization < 0.85
}
