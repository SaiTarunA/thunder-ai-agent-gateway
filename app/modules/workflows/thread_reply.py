from app.core.enums import IntentType
from app.core.schemas import ChatRequest, ExecutionPlan


class ThreadReplyWorkflow:

    def __init__(
        self,
        thread_context_provider,
        permission_filter,
        model_router,
    ):
        self.thread_context_provider = thread_context_provider
        self.permission_filter = permission_filter
        self.model_router = model_router

    async def execute(
        self,
        request: ChatRequest,
        plan: ExecutionPlan,
    ):
        main_message = request.additional_data.main_message
        recent_thread_messages = request.additional_data.recent_thread_messages
        thread_summary = request.additional_data.thread_summary

        if plan.requires_parameter_inference:
            if not request.additional_data.main_message:
                main_message = await self.thread_context_provider.get_main_message(
                    smsgid=request.smsgid, commented_via=request.commented_via
                )
            if not request.additional_data.recent_thread_messages:
                recent_thread_messages = (
                    await self.thread_context_provider.get_recent_thread_messages(
                        smsgid=request.smsgid, commented_via=request.commented_via
                    )
                )
            if not request.additional_data.thread_summary:
                thread_summary = await self.thread_context_provider.get_thread_summary(
                    smsgid=request.smsgid, commented_via=request.commented_via
                )

        data = {
            "user_text": request.user_text,
            "main_message": main_message,
            "recent_thread_messages": recent_thread_messages,
            "thread_summary": thread_summary,
        }

        return await self.model_router.generate(
            task=IntentType.THREAD_REPLY.value,
            data=data,
        )
