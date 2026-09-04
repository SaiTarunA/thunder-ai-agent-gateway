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
    "AND s.id = :sid AND m.isdeleted = 0 AND s.isdeleted = 0 AND s.teamstreamtype = 0 AND m.messagetime BETWEEN :start_date AND :end_date ORDER BY m.id;"
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

DB_GET_STREAMS_PARENT_MESSAGES = "SELECT  message,m.messagetime chatdate, m.msgtype, m.archiveid,m.sid, m.accountid ,CAST(smsgid AS CHAR) smsgid, nooflikes, noofcomments,role, COALESCE((SELECT 1 FROM streamlikes sl where chatnotifyid=m.smsgid  AND archiveid= :param_archiveid AND status=1 UNION SELECT 1 FROM streamlikes sl WHERE chatnotifyid=m.sharemsgid AND archiveid= :param_archiveid AND status=1),0)isuserliked, msgtype, CAST(commentvia AS CHAR) commentvia,extramsg,m.pinstatus,sharemsgid ,(CASE WHEN editedon!='0000-00-00 00:00:00' THEN 1 ELSE 0 END)isedited,m.sent_status,m.failure_reason, spi.username  FROM streammessages m JOIN streams s ON s.id = m.sid LEFT JOIN streammemberspersonalinfo spi ON spi.im_archiveid = m.archiveid  WHERE s.id=m.sid AND ( msgtype<200 or msgtype>300 ) AND msgtype NOT IN ( '251', '252', '253', '254', '256', '262', '263', '264', '257', '285', '220', '280', '281', '250', '290', '291', '292', '282', '258', '50', '51' ) and s.id= :param_sid AND m.smsgid = :param_smsgid AND m.isdeleted = 0 and s.isdeleted=0  order by m.messagetime desc;"

DB_GET_STREAMS_THREAD_MESSAGES = "SELECT sm.id, sm.accountid, sm.archiveid , sm.sid streamid ,CAST(sm.sharemsgid AS CHAR) sharemsgid, sm.message,sm.messagetime, CAST(sm.smsgid AS CHAR) smsgid, sm.nooflikes , sm.noofcomments , sm.msgtype ,sm.direct ,  0 isuserliked  , si.username, CAST(sm.commentvia AS CHAR) commentvia ,sm.extramsg  ,sm.pinstatus , sm.role , (CASE WHEN editedon!='0000-00-00 00:00:00' THEN 1 ELSE 0 END)is_edited   FROM streammessages sm ,streammemberspersonalinfo si  WHERE sm.msgtype in ( '20', '23', '24', '25', '26', '27', '28', '81' ) AND sm.sharemsgid  = :param_smsgid AND sm.archiveid=si.im_archiveid AND sm.isdeleted = 0  and si.isdeleted=0   UNION SELECT sm.id, sm.accountid, sm.archiveid , sm.sid streamid ,CAST(sm.sharemsgid AS CHAR) sharemsgid, sm.message,sm.messagetime, CAST(sm.smsgid AS CHAR) smsgid, sm.nooflikes , sm.noofcomments , sm.msgtype ,sm.direct ,  0 isuserliked  , si.username, CAST(sm.commentvia AS CHAR) commentvia ,sm.extramsg  ,sm.pinstatus , sm.role ,(CASE WHEN editedon!='0000-00-00 00:00:00' THEN 1 ELSE 0 END)isedited  FROM streammessages sm ,streammemberspersonalinfo si  WHERE sm.msgtype in ( '20', '23', '24', '25', '26', '27', '28', '81' ) AND sm.commentvia= :param_smsgid AND sm.archiveid=si.im_archiveid  AND sm.isdeleted = 0  and si.isdeleted=0  ORDER BY id;"

# =========== INSERT ===========

DB_GENERATE_AI_BILLING = (
    "INSERT INTO ai_billing_info "
    "( siteid, agentid, sitename, model_id, modelname, model_provider, "
    "ai_operation_type, bill_amount, extra_data ) "
    "VALUES (:siteid, :agentid, :sitename, :model_id, :modelname, "
    ":model_provider, :ai_operation_type, :bill_amount, :extra_data);"
)
