"""
Streams database queries.
"""

# =========== SELECT ===========

DB_GET_STREAMS_USER_CHAT_BY_DURATION = (
    "SELECT m.message, m.messagetime, m.archiveid, m.sid, m.accountid, "
    "sm.username, sm.siteid, sm.firstname, sm.lastname "
    "FROM streammessages m, streams s, streammemberspersonalinfo sm "
    "WHERE s.id = m.sid AND sm.im_archiveid = m.archiveid "
    "AND (m.msgtype < 200 OR m.msgtype > 300) "
    "AND m.msgtype NOT IN (116,251,252,253,254,256,262,263,264,257,285,"
    "220,280,281,250,290,291,292,282,258,222,223,261,224,225,50,51) "
    "AND s.id = :sid AND m.isdeleted = 0 AND s.isdeleted = 0 "
    "AND s.teamstreamtype = 0 "
    "AND m.messagetime BETWEEN :start_date AND :end_date "
    "ORDER BY m.id;"
)

DB_GET_STREAMS_USER_CHAT_BY_MESSAGE_COUNT = (
    "SELECT m.message, m.messagetime, m.archiveid, m.sid, m.accountid, "
    "sm.username, sm.siteid, sm.firstname, sm.lastname "
    "FROM streammessages m, streams s, streammemberspersonalinfo sm "
    "WHERE s.id = m.sid AND sm.im_archiveid = m.archiveid "
    "AND (m.msgtype < 200 OR m.msgtype > 300) "
    "AND m.msgtype NOT IN (116,251,252,253,254,256,262,263,264,257,285,"
    "220,280,281,250,290,291,292,282,258,222,223,261,224,225,50,51) "
    "AND s.id = :sid AND m.isdeleted = 0 AND s.isdeleted = 0 "
    "AND s.teamstreamtype = 0 "
    "ORDER BY m.id DESC LIMIT :message_count;"
)

# =========== INSERT ===========

DB_GENERATE_AI_BILLING = (
    "INSERT INTO ai_billing_info "
    "( siteid, agentid, sitename, model_id, modelname, model_provider, "
    "ai_operation_type, bill_amount, extra_data ) "
    "VALUES (:siteid, :agentid, :sitename, :model_id, :modelname, "
    ":model_provider, :ai_operation_type, :bill_amount, :extra_data);"
)
