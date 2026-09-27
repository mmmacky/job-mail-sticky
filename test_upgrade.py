# -*- coding: utf-8 -*-
import os,sys,tempfile,unittest,copy,ctypes,json
from pathlib import Path
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
import schedule_parser as parser
import mail_sync
SENT='Wed, 02 Sep 2026 12:03:33 +0800'

class Dates(unittest.TestCase):
    def test_pm(self):
        self.assertEqual(parser.parse_schedule('笔试开始时间：2026-09-05 下午 2:30',SENT)['start'],'2026-09-05 14:30')
    def test_midnight(self):
        self.assertEqual(parser.parse_schedule('截止时间：2026-09-05 24:00',SENT)['due'],'2026-09-06 00:00')
    def test_year(self):
        self.assertEqual(parser.parse_schedule('笔试时间：1月2日 14:00','Thu, 31 Dec 2026 12:00:00 +0800')['due'],'2027-01-02 14:00')
    def test_hours(self):
        self.assertEqual(parser.parse_schedule('收到邮件72小时内完成测评',SENT)['due'],'2026-09-05 12:03')
    def test_calendar(self):
        r=parser.parse_schedule('测评有效期5个自然日，请完成',SENT)
        self.assertEqual(r['due'],'2026-09-07 12:03'); self.assertTrue(r['needs_review'])
    def test_leap(self):
        self.assertEqual(parser.parse_schedule('收到邮件48小时内完成测评','Wed, 28 Feb 2024 12:00:00 +0800')['due'],'2024-03-01 12:00')
    def test_entry_end(self):
        r=parser.parse_schedule('笔试开始时间：2026-09-05 19:00。最晚进入时间：2026-09-05 19:15。结束时间：2026-09-05 21:00。',SENT)
        self.assertEqual(r['start'],'2026-09-05 19:00'); self.assertEqual(r['entry'],'2026-09-05 19:15'); self.assertEqual(r['end'],'2026-09-05 21:00')
    def test_range(self):
        r=parser.parse_schedule('考试时间：2026-09-05 19:00 -- 21:00',SENT)
        self.assertEqual(r['end'],'2026-09-05 21:00')
    def test_explicit(self):
        self.assertEqual(parser.parse_schedule('收到邮件72小时内完成。截止时间2026-09-04 18:00。',SENT)['deadline'],'2026-09-04 18:00')
    def test_negative(self):
        for t in ['验证码有效期5分钟。','我们将在3个工作日内回复。','答题时长72小时。']:
            self.assertEqual(parser.parse_schedule(t,SENT)['due'],'')
    def test_manual(self):
        i=dict(text='收到邮件72小时内完成测评',received=SENT,due='2026-09-04 08:00',manual_time=True,done=True,removed=True)
        parser.update_item(i); self.assertEqual(i['due'],'2026-09-04 08:00'); self.assertTrue(i['removed'])

class FakeIMAP:
    searches=[]; downloads=[]; fail=False; validity=b'42'; ids=b'1 2'
    def __init__(self,*a,**k): pass
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def login(self,*a): pass
    def select(self,*a,**k):
        assert k['readonly']; return 'OK', [b'2']
    def response(self,*a): return 'UIDVALIDITY',[self.validity]
    def uid(self,op,*args):
        if op=='search':
            self.searches.append(args); return 'OK',[self.ids]
        uid=args[0]; self.downloads.append(uid)
        if self.fail and uid=='2': return 'NO',[]
        # duplicate Message-ID must remain separate physical messages
        raw=('Subject: =?utf-8?b?5rWL6K+V56yU6K+V?=\r\nMessage-ID: <same@example.com>\r\nDate: '+SENT+'\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n收到邮件72小时内完成测评').encode()
        return 'OK',[(b'body',raw)]

class Sync(unittest.TestCase):
    def test_incremental(self):
        FakeIMAP.searches=[]; FakeIMAP.downloads=[]; FakeIMAP.fail=False
        with tempfile.TemporaryDirectory() as d, patch.object(mail_sync.imaplib,'IMAP4_SSL',FakeIMAP):
            from sticky import mail_text,evidence
            rows,n,cursor=mail_sync.sync_mail('test@qq.com','fake',{},Path(d),mail_text,evidence)
            self.assertEqual(n,2); self.assertNotEqual(rows[0]['id'],rows[1]['id'])
            self.assertEqual(len(list((Path(d)/'originals').glob('*.eml'))),2)
            rows2,n2,c2=mail_sync.sync_mail('test@qq.com','fake',cursor,Path(d),mail_text,evidence)
            self.assertEqual(n2,0); self.assertEqual(len(FakeIMAP.downloads),2)
            self.assertEqual(FakeIMAP.searches[-1][1: ],('UID','3:*'))
            current=[]; mail_sync.merge_items(current,rows,parser.update_item)
            current[0].update(done=True,removed=True,manual_time=True,due='2026-09-10 09:00')
            mail_sync.merge_items(current,rows,parser.update_item)
            self.assertEqual(len(current),2); self.assertTrue(current[0]['removed'])
    def test_failure_checkpoint(self):
        FakeIMAP.fail=True
        with tempfile.TemporaryDirectory() as d, patch.object(mail_sync.imaplib,'IMAP4_SSL',FakeIMAP):
            from sticky import mail_text,evidence
            original={}
            with self.assertRaises(Exception):
                mail_sync.sync_mail('test@qq.com','fake',original,Path(d),mail_text,evidence)
            self.assertEqual(original,{})
        FakeIMAP.fail=False

class UI(unittest.TestCase):
    def test_dialogs_and_tray(self):
        import sticky
        with tempfile.TemporaryDirectory() as d:
            sticky.DATA=Path(d)
            r=sticky.tk.Tk(); app=sticky.App(r); r.update()
            w=app.add(); r.update()
            self.assertEqual(str(w.transient()),'')
            self.assertFalse(w.attributes('-topmost')); self.assertIsNone(w.grab_current())
            r.lift(); r.update()
            order=r.tk.call('wm','stackorder',r)
            self.assertEqual(str(order[-1]),str(r))
            w.lift(); r.update()
            self.assertEqual(str(r.tk.call('wm','stackorder',r)[-1]),str(w))
            w.destroy()
            class Tray:
                def stop(self): pass
            app.tray=Tray(); app.ui['tray_explained']=True
            app.close(); r.update(); self.assertEqual(r.state(),'withdrawn')
            app.show(); r.update(); self.assertEqual(r.state(),'normal')
            app.quit()
    def test_privacy(self):
        import sticky
        with tempfile.TemporaryDirectory() as d:
            sticky.DATA=Path(d); r=sticky.tk.Tk(); app=sticky.App(r); app.privacy(); r.update(); r.destroy()

if __name__=='__main__': unittest.main(verbosity=2)

