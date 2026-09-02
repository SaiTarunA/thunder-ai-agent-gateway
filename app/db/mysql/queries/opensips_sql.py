"""
OpenSIPs / Cloud / LivePBX queries.
"""

# =========== SELECT ===========

DB_SELECT_AI_SYSTEM_SETTINGS = (
    "select configuration from luna_ai_panel_configurations "
    "where feature_name = :feature_name"
)

DB_SELECT_CHAT_SUMMARY = (
    "SELECT sid, start_date, end_date, summary, extra_data "
    "FROM streams_ai_chat_summary_info "
    "WHERE sid = :sid AND start_date >= :start_date AND end_date <= :end_date;"
)

DB_SELECT_QUERY_FOR_AUTHORIZATION = (
    "SELECT * FROM authkeys WHERE authkey = :authkey AND username = :username"
)

# =========== INSERT ===========

DB_INSERT_CHAT_SUMMARY = (
    "INSERT INTO streams_ai_chat_summary_info "
    "(sid, start_date, end_date, summary, extra_data) "
    "VALUES (:sid, :start_date, :end_date, :summary, :extra_data);"
)

# =========== UPDATE ===========

DB_UPDATE_CHAT_SUMMARY = (
    "UPDATE streams_ai_chat_summary_info "
    "SET summary = :summary, updated_date = CURRENT_TIMESTAMP "
    "WHERE sid = :sid AND start_date = :start_date AND end_date = :end_date;"
)
