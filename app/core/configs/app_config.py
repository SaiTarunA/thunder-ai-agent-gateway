# ==========   ROUTES    =========
from app.api.v1.luna_router import luna_router
from app.api.v1.auth_router import auth_router

ROUTES_V1 = {
    "luna": luna_router,       # Luna API
    "auth": auth_router,     # Auth API
}