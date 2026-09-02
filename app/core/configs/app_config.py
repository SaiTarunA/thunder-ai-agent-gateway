# ==========   ROUTES    =========
from app.api.v1.luna_router import luna_router

ROUTES_V1 = {
    "luna": luna_router,       # Luna API
}