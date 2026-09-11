// Quarterly infrastructure report helper.

namespace Example.Infra
{
    public class RegionReport
    {
        public string Name { get; }
        public int Nodes { get; }
        public double Utilization { get; }

        public RegionReport(string name, int nodes, double utilization)
        {
            Name = name;
            Nodes = nodes;
            Utilization = utilization;
        }

        public bool IsHealthy() => Utilization < 0.85;
    }
}
