"""Read-only presentation helpers. They never grant access or approve proposals."""
from datetime import datetime, timezone, timedelta
import time

STATUSES = ['待审核', '审批执行中（需核对）', '已发布']


def project_members(records, clock=None):
    clock = time.time() if clock is None else clock
    latest = {}
    for item in sorted(records, key=lambda r: (r['created_at'], r['id'])):
        latest[(item['project_id'], item['union_id'])] = item
    result = []
    for item in latest.values():
        status = {'request':'待批准', 'grant':'已授权', 'revoke':'已撤销'}[item['action']]
        if item['action'] == 'grant' and item['expires_at'] <= clock:
            status = '已过期'
        result.append(dict(item, status=status))
    return result


def filter_proposals(entries, status='全部', keyword=''):
    query = keyword.strip().casefold()
    return [e for e in entries if (status == '全部' or e['status'] == status) and
            (not query or query in ' '.join(str(e.get(k,'')) for k in
                ('id','title','project','module','submitted_by')).casefold())]


def beijing_time(timestamp):
    return datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
