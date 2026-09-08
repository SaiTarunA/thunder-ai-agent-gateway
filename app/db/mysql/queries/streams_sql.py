"""
Streams database queries.
"""

# =========== SELECT ===========

DB_GET_STREAMS_USER_CHAT_BY_DURATION = "SELECT m.message, m.messagetime, m.archiveid, m.sid, m.accountid, sm.username, sm.siteid, sm.firstname, sm.lastname FROM streammessages m, streams s, streammemberspersonalinfo sm WHERE s.id = m.sid AND sm.im_archiveid = m.archiveid AND (m.msgtype < 200 OR m.msgtype > 300) AND m.msgtype NOT IN (116,251,252,253,254,256,262,263,264,257,285,220,280,281,250,290,291,292,282,258,222,223,261,224,225,50,51) AND s.id = :param_sid AND m.isdeleted = 0 AND s.isdeleted = 0 AND s.teamstreamtype = 0 AND m.messagetime BETWEEN :param_start_date AND :param_end_date ORDER BY m.id;"

DB_GET_STREAMS_USER_CHAT_BY_MESSAGE_COUNT = "SELECT m.message, m.messagetime, m.archiveid, m.sid, m.accountid, sm.username, sm.siteid, sm.firstname, sm.lastname FROM streammessages m, streams s, streammemberspersonalinfo sm WHERE s.id = m.sid AND sm.im_archiveid = m.archiveid AND (m.msgtype < 200 OR m.msgtype > 300) AND m.msgtype NOT IN (116,251,252,253,254,256,262,263,264,257,285,220,280,281,250,290,291,292,282,258,222,223,261,224,225,50,51) AND s.id = :param_sid AND m.isdeleted = 0 AND s.isdeleted = 0 AND s.teamstreamtype = 0 ORDER BY m.id DESC LIMIT :param_message_count;"

DB_GET_STREAMS_USER_CHAT_BY_UNREAD_MESSAGES = "SELECT m.message, m.messagetime, m.archiveid, m.sid, m.accountid, spi.username, spi.siteid, spi.firstname, spi.lastname FROM streammessages m INNER JOIN streams s ON s.id = m.sid AND s.isdeleted = 0 AND s.teamstreamtype = 0 INNER JOIN streammembersinfo smi ON smi.sid = m.sid AND smi.archiveid = :param_archiveid AND smi.isdeleted = 0 INNER JOIN streammemberspersonalinfo spi ON spi.im_archiveid = m.archiveid WHERE s.id = :param_sid AND m.isdeleted = 0 AND m.archiveid != :param_archiveid AND m.commentvia = 0 AND (m.msgtype < 200 OR m.msgtype > 300) AND m.msgtype NOT IN (116,251,252,253,254,256,262,263,264,257,285,220,280,281,250,290,291,292,282,258,222,223,261,224,225,50,51) AND m.smsgid > smi.lastreadsmsgid ORDER BY m.id DESC;"

DB_GET_STREAMS_PARENT_MESSAGES = "SELECT m.message, m.messagetime, m.archiveid, m.sid, m.accountid, spi.username, spi.siteid, spi.firstname, spi.lastname FROM streammessages m JOIN streams s ON s.id = m.sid LEFT JOIN streammemberspersonalinfo spi ON spi.im_archiveid = m.archiveid WHERE (m.msgtype < 200 OR m.msgtype > 300) AND m.msgtype NOT IN (251,252,253,254,256,262,263,264,257,285,220,280,281, 250,290,291,292,282,258,50,51) AND s.id = :param_sid AND m.smsgid = :param_smsgid AND m.isdeleted = 0 AND s.isdeleted = 0 ORDER BY m.messagetime DESC"

DB_GET_STREAMS_THREAD_MESSAGES = "SELECT sm.message, sm.messagetime, sm.archiveid, sm.sid, sm.accountid, si.username, si.siteid, si.firstname, si.lastname FROM streammessages sm, streammemberspersonalinfo si WHERE sm.msgtype IN (20,23,24,25,26,27,28,81) AND sm.sharemsgid = :param_smsgid AND sm.archiveid = si.im_archiveid AND sm.isdeleted = 0 AND si.isdeleted = 0 UNION SELECT sm.message, sm.messagetime, sm.archiveid, sm.sid, sm.accountid, si.username, si.siteid, si.firstname, si.lastname FROM streammessages sm, streammemberspersonalinfo si WHERE sm.msgtype IN (20,23,24,25,26,27,28,81) AND sm.commentvia = :param_smsgid AND sm.archiveid = si.im_archiveid AND sm.isdeleted = 0 AND si.isdeleted = 0 ORDER BY messagetime"

# =========== INSERT ===========

DB_GENERATE_AI_BILLING = "INSERT INTO ai_billing_info ( siteid, agentid, sitename, model_id, modelname, model_provider, ai_operation_type, bill_amount, extra_data ) VALUES (:siteid, :agentid, :sitename, :model_id, :modelname, :model_provider, :ai_operation_type, :bill_amount, :extra_data);"
