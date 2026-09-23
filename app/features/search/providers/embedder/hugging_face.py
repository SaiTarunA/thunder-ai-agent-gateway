from app.features.search.providers.interfaces import EmbeddingProvider
from threading import Lock
from sentence_transformers import SentenceTransformer


class HuggingFaceEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.initialized = False  # Flag to indicate if the model has been initialized
        self.initialization_lock = (
            Lock()
        )  # Lock to ensure thread-safe initialization of the model

    async def initialize_model(self):
        """Initialize the model if it hasn't been initialized yet."""
        if not self.initialized:
            with self.initialization_lock:
                if not self.initialized:
                    self.model = SentenceTransformer(self.model_name)
                    self.initialized = True

    async def embed_text(self, text: str) -> list[float]:
        """Return the embedding for the given text."""
        await self.initialize_model()
        return self.model.encode(text).tolist()

    async def embed_documents(self, documents: list[str], batch_size: int) -> list[list[float]]:
        """Return the embeddings for the given list of documents."""
        await self.initialize_model()
        return self.model.encode(documents, batch_size=batch_size).tolist()
