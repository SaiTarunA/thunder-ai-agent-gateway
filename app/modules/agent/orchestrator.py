from app.modules.agent.planner import Planner
from app.modules.agent.intent_router import IntentRouter
from app.core.schemas import ChatRequest


class AgentOrchestrator:

    def __init__(
        self,
        planner: Planner,
        intent_router: IntentRouter,
    ):
        self.planner = planner
        self.intent_router = intent_router

    async def execute(self, request: ChatRequest):

        plan = self.planner.create_plan(request)

        workflow = self.intent_router.get_workflow(
            plan.intent
        )

        return await workflow.execute(
            request=request,
            plan=plan,
        )