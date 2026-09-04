from abc import ABC, abstractmethod


class BaseAIProvider(ABC):

    @abstractmethod
    async def process_responses_api_call(self, *args, **kwargs):
        pass

    @abstractmethod
    async def create_conversation_id(self, *args, **kwargs):
        pass
