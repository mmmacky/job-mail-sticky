# -*- coding: utf-8 -*-
import email
import hashlib
import imaplib
import re
import ssl
import socket
from datetime import datetime, timedelta
from email import policy

KEYWORDS = re.compile(r'笔试|测评|在线测试|能力测试|性格测试|assessment|online test|coding test', re.I)

def error_message(error):
    if isinstance(error, (socket.timeout, TimeoutError)): return '连接超时，稍后会自动重试。'
    if isinstance(error, ssl.SSLError): return '安全连接失败，请检查电脑时间和网络。'
    if isinstance(error, imaplib.IMAP4.error): return '邮箱认证或收信失败，请检查 IMAP 开关和授权码。'
    if isinstance(error, OSError): return '网络或本地数据读写失败，请检查网络和磁盘。'
    return '更新未完成，已有事项保留；稍后自动重试。'

def test_connection(address, password):
    with imaplib.IMAP4_SSL('imap.qq.com', 993, ssl_context=ssl.create_default_context(), timeout=25) as client:
        client.login(address, password)
        if client.select('INBOX', readonly=True)[0] != 'OK': raise imaplib.IMAP4.error('select failed')

def sync_mail(address, password, cursor, directory, body_reader, evidence_reader):
    account = hashlib.sha256(address.casefold().encode()).hexdigest()
    checkpoint = dict(cursor or {})
    items = []
    since = datetime.now() - timedelta(days=90)
    months = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
    date = f'{since.day:02d}-{months[since.month-1]}-{since.year}'
    with imaplib.IMAP4_SSL('imap.qq.com', 993, ssl_context=ssl.create_default_context(), timeout=25) as client:
        client.login(address, password)
        if client.select('INBOX', readonly=True)[0] != 'OK': raise imaplib.IMAP4.error('select failed')
        valid = client.response('UIDVALIDITY')[1]
        validity = valid[0].decode() if valid and valid[0] else ''
        if not validity: raise imaplib.IMAP4.error('missing UIDVALIDITY')
        same = checkpoint.get('account') == account and checkpoint.get('validity') == validity
        last = int(checkpoint.get('uid', 0)) if same else 0
        criteria = ('UID', f'{last+1}:*') if last else ('SINCE', date)
        status, found = client.uid('search', None, *criteria)
        if status != 'OK': raise imaplib.IMAP4.error('search failed')
        ids = sorted(int(x) for x in found[0].split() if int(x) > last)
        raw_folder = directory / 'originals'
        for uid in ids:
            status, rows = client.uid('fetch', str(uid), '(BODY.PEEK[])')
            if status != 'OK': raise imaplib.IMAP4.error('fetch failed')
            raw = next((r[1] for r in rows if isinstance(r, tuple)), None)
            if raw is None: raise imaplib.IMAP4.error('missing body')
            msg = email.message_from_bytes(raw, policy=policy.default)
            subject = str(msg.get('Subject', '无主题'))
            body = body_reader(msg)
            if KEYWORDS.search(subject + '\n' + body):
                ident = hashlib.sha256(f'{account}/INBOX/{validity}/{uid}'.encode()).hexdigest()
                legacy = hashlib.sha256((address + str(msg.get('Message-ID', ''))).encode() + (raw if not msg.get('Message-ID') else b'')).hexdigest()
                raw_folder.mkdir(parents=True, exist_ok=True)
                destination = raw_folder / (ident + '.eml')
                temp = destination.with_suffix('.tmp'); temp.write_bytes(raw); temp.replace(destination)
                items.append(dict(id=ident, legacy_id=legacy, account=account, uid=uid, source='mail', original=destination.name,
                    title=subject, sender=str(msg.get('From', '')), received=str(msg.get('Date', '')), evidence=evidence_reader(body), text=body, due='', done=False))
        return items, len(ids), dict(account=account, validity=validity, uid=max(ids, default=last))

def merge_items(current, incoming, update_item):
    by_id = {i['id']: i for i in current}
    new_count = 0
    for mail in incoming:
        old = by_id.get(mail['id'])
        if old is None:
            old = next((i for i in current if not i.get('uid') and i['id'] == mail.get('legacy_id')), None)
        if old is None:
            update_item(mail); current.append(mail); by_id[mail['id']] = mail; new_count += 1
        else:
            for key in ('id','uid','original','account','sender','received','text','evidence','source'):
                if key in mail: old[key] = mail[key]
            old['parser_version'] = -1
            update_item(old)
            by_id[old['id']] = old
    return new_count

