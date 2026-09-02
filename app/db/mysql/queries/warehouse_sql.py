"""
Warehouse / auth token queries.
"""

# =========== SELECT ===========

DB_SELECT_UNIQUE_ID = "select * from portalloginauthentication where uniqueid = :uniqueid;"
DB_GET_UNIQUE_ID = "SELECT UUID() AS uniqueid;"
DB_SELECT_ADMIN_LOGIN_TOKENS = (
    "select * from vpbx_admin_login_tokens "
    "where (token = :token or refresh_token = :refresh_token)"
)

# =========== INSERT ===========

DB_INSERT_UNIQUE_ID = (
    "INSERT INTO portalloginauthentication(agentid,uniqueid,logintime,siteid) "
    "VALUES (:agentid, :uniqueid, NOW(), :siteid)"
)

# =========== UPDATE ===========

DB_UPDATE_RES_BODY = (
    "UPDATE vpbx_api_external_logs SET res_body = :res_body WHERE id = :id;"
)

# =========== DELETE ===========

DB_DELETE_UUINQUQ_ID = (
    "DELETE FROM portalloginauthentication WHERE uniqueid = :uniqueid;"
)
