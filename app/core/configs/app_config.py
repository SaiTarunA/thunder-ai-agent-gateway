# ==========   ROUTES    =========
from app.api.v1.luna_router import luna_router
from app.api.v1.auth_router import auth_router
from app.api.v1.search_router import search_router
from app.api.v1.gdrive_router import gdrive_router

ROUTES_V1 = {
    "luna": luna_router,       # Luna API
    "auth": auth_router,     # Auth API
    "search": search_router,
    "gdrive": gdrive_router,     # Google Drive connection and search API
}