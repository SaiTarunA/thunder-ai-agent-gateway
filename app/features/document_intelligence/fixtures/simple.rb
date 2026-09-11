# Quarterly infrastructure report helper.

class RegionReport
  attr_reader :name, :nodes, :utilization

  def initialize(name, nodes, utilization)
    @name = name
    @nodes = nodes
    @utilization = utilization
  end

  def healthy?
    utilization < 0.85
  end
end
