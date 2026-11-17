"""从本地消息目录查找发送账号，只返回身份和计数，不返回聊天正文。"""

from .runs import RunRepository
from .customers import record


class SenderRepository(RunRepository):
    """基于已同步消息提供账号查找，不在网页查询时调用外部接口。"""

    def search(self, keyword, offset=0, limit=25):
        expressions = {name: "JSON_UNQUOTE(JSON_EXTRACT(payload,'$." + name + "'))"
                       for name in ("sender_id", "sender_name", "conversation_id", "conversation_name")}
        conditions = [expressions["sender_id"] + " IS NOT NULL", expressions["sender_id"] + "<>''"]
        args = []
        if keyword:
            term = "%" + keyword.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append("(" + " OR ".join("LOWER(" + expressions[key] + ") LIKE %s" for key in ("sender_name", "sender_id")) + ")")
            args.extend((term, term))
        selection = ("SELECT uid,source," + expressions["sender_id"] + " AS sender_id,"
            + expressions["conversation_id"] + " AS conversation_id,MAX(" + expressions["sender_name"] + ") AS sender_name,"
            + "MAX(" + expressions["conversation_name"] + ") AS conversation_name,COUNT(*) AS message_count,"
            + "MAX(sent_at) AS last_seen_at FROM messages WHERE " + " AND ".join(conditions)
            + " GROUP BY uid,source,sender_id,conversation_id")
        with self.connection() as store:
            counts, _ = store._execute("SELECT COUNT(*) AS total FROM (" + selection + ") observed", tuple(args))
            rows, _ = store._execute(selection + " ORDER BY last_seen_at DESC,uid,source,sender_id,conversation_id LIMIT %s OFFSET %s",
                tuple(args) + (limit, offset))
        return {"items": [record(row) for row in rows], "total": counts[0]["total"]}

    def observed(self, rule):
        conditions, args = ["uid=%s", "source=%s", "JSON_UNQUOTE(JSON_EXTRACT(payload,'$.sender_id'))=%s"], [rule.uid, rule.source, rule.sender_id]
        if rule.conversation_id:
            conditions.append("JSON_UNQUOTE(JSON_EXTRACT(payload,'$.conversation_id'))=%s")
            args.append(rule.conversation_id)
        with self.connection() as store:
            rows, _ = store._execute("SELECT message_id FROM messages WHERE " + " AND ".join(conditions) + " LIMIT 1", tuple(args))
        return bool(rows)
