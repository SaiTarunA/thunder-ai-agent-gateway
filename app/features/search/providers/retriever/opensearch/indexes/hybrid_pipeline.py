from app.features.search.providers.retriever.opensearch.indexes.search_pipeline import (
    OpenSearchSearchPipelineDefinition,
)


class HybridSearchPipeline(OpenSearchSearchPipelineDefinition):

    @property
    def name(self) -> str:
        return "streams-hybrid-pipeline"

    @property
    def definition(self) -> dict:
        return {
            "description": "Streams hybrid search pipeline",
            "phase_results_processors": [
                {
                    "normalization-processor": {
                        "normalization": {
                            "technique": "min_max",
                        },
                        "combination": {
                            "technique": "arithmetic_mean",
                            "parameters": {
                                "weights": [0.5, 0.5],
                            },
                        },
                    }
                }
            ],
        }