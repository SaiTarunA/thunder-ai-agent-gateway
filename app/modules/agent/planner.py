from dataclasses import dataclass

from app.core.schemas import ChatRequest
from app.core.enums import ChatRequestType, IntentType


@dataclass
class ExecutionPlan:
    intent: IntentType
    requires_parameter_inference: bool = False


class Planner:

    def create_plan(self, request: ChatRequest) -> ExecutionPlan:
        match request.request_type:
            case ChatRequestType.DRAFT_MESSAGE:
                # If any of the additional fields are not present, set requires_parameter_inference to True
                requires_parameter_inference = not all([
                    request.additional_data.tone,
                    request.additional_data.language,
                    request.additional_data.length,
                ])
                return ExecutionPlan(
                    intent=IntentType.DRAFT_MESSAGE,
                    requires_parameter_inference=requires_parameter_inference,
                )
            case ChatRequestType.THREAD_REPLY:
                requires_parameter_inference = not all([
                    request.additional_data.main_message,
                    request.additional_data.recent_thread_messages,
                    request.additional_data.thread_summary,
                ])
                return ExecutionPlan(
                    intent=IntentType.THREAD_REPLY,
                    requires_parameter_inference=requires_parameter_inference,
                )
            case ChatRequestType.FREE_TEXT:
                # TODO: Implement logic for free_text intent
                # 1. Determine the intent of the request, requires context or not.
                # 2. Based on the intent, return an ExecutionPlan object with the appropriate flags set
                return ExecutionPlan(
                    intent=IntentType.THREAD_REPLY,
                )
            case _:
                # TODO: Implement logic for free_text intent
                # 1. Determine the intent of the request, requires context or not.
                # 2. Based on the intent, return an ExecutionPlan object with the appropriate flags set
                return ExecutionPlan(
                    intent=IntentType.THREAD_REPLY,
                )