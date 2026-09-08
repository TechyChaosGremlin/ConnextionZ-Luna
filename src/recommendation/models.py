class RankingModel:
    def __init__(self, weights):
        self.weights = weights

    def calculate_rank(self, signals):
        relevance = signals.get('relevance', 0) * self.weights['relevance']
        expected_enjoyment = signals.get('expected_enjoyment', 0) * self.weights['expected_enjoyment']
        quality_originality = signals.get('quality_originality', 0) * self.weights['quality_originality']
        freshness = signals.get('freshness', 0) * self.weights['freshness']
        discovery_opportunity = signals.get('discovery_opportunity', 0) * self.weights['discovery_opportunity']
        exploration = signals.get('exploration', 0) * self.weights['exploration']
        network_connection = signals.get('network_connection', 0) * self.weights['network_connection']

        total_rank = (
            relevance + expected_enjoyment + quality_originality + freshness +
            discovery_opportunity + exploration + network_connection
        )
        return total_rank