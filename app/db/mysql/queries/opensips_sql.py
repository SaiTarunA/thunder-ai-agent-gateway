"""
OpenSIPs / Cloud / LivePBX queries.
"""

# =========== SELECT ===========

DB_SELECT_AI_SYSTEM_SETTINGS = (
    "select configuration from luna_ai_panel_configurations "
    "where feature_name = :feature_name"
)

DB_SELECT_CHAT_SUMMARY = (
    "SELECT id, archive_id, source_type, ref_id AS sid, ref_id, site_id, start_date, end_date, summary, extra_data "
    "FROM ai_agent_summary_info "
    "WHERE source_type = :source_type AND ref_id = :ref_id AND start_date >= :start_date AND end_date <= :end_date;"
)

DB_SELECT_QUERY_FOR_AUTHORIZATION = (
    "SELECT * FROM authkeys WHERE authkey = :authkey AND username = :username"
)

# =========== INSERT ===========

DB_INSERT_CHAT_SUMMARY = (
    "INSERT INTO ai_agent_summary_info "
    "(archive_id, source_type, ref_id, site_id, start_date, end_date, summary, extra_data) "
    "VALUES (:archive_id, :source_type, :ref_id, :site_id, :start_date, :end_date, :summary, :extra_data);"
)

# =========== UPDATE ===========

DB_UPDATE_CHAT_SUMMARY = (
    "UPDATE ai_agent_summary_info "
    "SET summary = :summary, extra_data = :extra_data, updated_date = CURRENT_TIMESTAMP(3) "
    "WHERE source_type = :source_type AND ref_id = :ref_id AND start_date = :start_date AND end_date = :end_date;"
)

DB_UPDATE_CHAT_SUMMARY_BY_ID = (
    "UPDATE ai_agent_summary_info "
    "SET summary = :summary, end_date = :end_date, extra_data = :extra_data, updated_date = CURRENT_TIMESTAMP(3) "
    "WHERE id = :id;"
)
