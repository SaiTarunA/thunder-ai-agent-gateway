from app.core.schemas import ChatRequest, ExecutionPlan, DraftMessageFields
from app.core.enums import IntentType


class DraftMessageWorkflow:

    def __init__(self, model_router):
        self.model_router = model_router

    async def execute(
        self,
        request: ChatRequest,
        plan: ExecutionPlan,
    ):

        tone = request.additional_data.tone
        language = request.additional_data.language
        length = request.additional_data.length

        if plan.requires_parameter_inference:

            inferred_data = await self.model_router.generate(
                task="draft_message_parameter_inference",
                data={
                    "user_text": request.user_text,
                    "tone": request.additional_data.tone,
                    "language": request.additional_data.language,
                    "length": request.additional_data.length,
                },
            )

        data = {
            "user_text": request.user_text,
            "tone": tone,
            "language": language,
            "length": length,
            **inferred_data,
        }

        return await self.model_router.generate(
            task=IntentType.DRAFT_MESSAGE.value,
            data=data,
        )
