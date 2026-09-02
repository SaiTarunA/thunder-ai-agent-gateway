from app.modules.workflows.draft_message import DraftMessageWorkflow
from app.modules.workflows.thread_reply import ThreadReplyWorkflow
from app.core.enums import IntentType


class IntentRouter:

    def __init__(
        self,
        draft_message: DraftMessageWorkflow,
        thread_reply: ThreadReplyWorkflow,
    ):
        self.workflows = {
            IntentType.DRAFT_MESSAGE: draft_message,
            IntentType.THREAD_REPLY: thread_reply,
        }

    def get_workflow(self, intent: IntentType):
        workflow = self.workflows.get(intent)

        if not workflow:
            raise ValueError(f"Unsupported intent: {intent}")

        return workflow
