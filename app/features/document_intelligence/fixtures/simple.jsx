// Quarterly infrastructure report card (React).

function RegionCard({ name, nodes, utilization }) {
  const status = utilization < 0.85 ? "Healthy" : "At capacity";

  return (
    <div className="region-card">
      <h3>{name}</h3>
      <p>Nodes: {nodes}</p>
      <p>Status: {status}</p>
    </div>
  );
}

export default RegionCard;
