# -*- coding: utf-8 -*-
"""Local extraction of exam starts and assessment deadlines from mail text."""
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

VERSION = 2
DATE = re.compile(
    r'(?<!\d)(?:(?P<y>20\d{2})\s*(?:年\s*-?|[-/.])\s*)?'
    r'(?P<m>1[0-2]|0?[1-9])\s*(?:月\s*-?|[-/.])\s*'
    r'(?P<d>3[01]|[12]\d|0?[1-9])(?!\d)\s*日?'
    r'(?:\s*[（(]?\s*(?:星期|周)[一二三四五六日天]\s*[）)]?)?'
    r'(?:[\sT-]*(?P<ampm>上午|下午|晚上|中午|早上|晚)?\s*'
    r'(?P<h>[01]?\d|2[0-4])\s*(?:[:：]|[时点]\s*-?)\s*'
    r'(?P<minute>[0-5]?\d)\s*分?(?:[:：](?P<sec>[0-5]\d))?)?'
)
REL = re.compile(r'(?P<n>\d+|[一二三四五六七八九十两]+)\s*个?\s*(?P<unit>自然日|工作日|小时|天|日)\s*(?:之?内)?')
DEAD = r'截止(?:时间|日期)?|截至|最晚|结束时间|失效时间|到期时间|有效期(?:至|到)|有效期截至|有效期截止'
ENTRY = r'最晚(?:进入|入场|开始|登录)(?:时间)?|入场截止(?:时间)?'
START = r'笔试开始时间|考试开始时间|开始时间|开考时间|开考|笔试时间|考试时间|笔试将于|考试将于|测评时间'


def number(text):
    if text.isdigit():
        return int(text)
    digits = dict(zip('一二三四五六七八九', range(1, 10)))
    digits['两'] = 2
    if '十' in text:
        a, b = text.split('十', 1)
        return digits.get(a, 1) * 10 + digits.get(b, 0)
    return digits.get(text, 0)


def stamp(value):
    return value.astimezone().strftime('%Y-%m-%d %H:%M')


def parse_schedule(body, sent, title=''):
    text = re.sub(r'[\u200b-\u200f\ufeff]', '', body.replace('\xa0', ' '))
    text = re.sub(r'https?://\S+', ' [链接] ', text)
    try:
        base = parsedate_to_datetime(sent)
        if base.tzinfo is None:
            base = base.astimezone()
    except (ValueError, TypeError, OverflowError):
        base = None
    # An email server's Date-header timezone does not define the exam timezone.
    # Unqualified event times use the user's local timezone.
    tz = None
    if re.search(r'北京时间|中国标准时间|(?:UTC|GMT)\s*\+\s*0?8', text, re.I):
        tz = timezone(timedelta(hours=8))
    else:
        zone = re.search(r'(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::(\d{2}))?', text, re.I)
        if zone and int(zone[2]) <= 14 and int(zone[3] or 0) < 60:
            offset = timedelta(hours=int(zone[2]), minutes=int(zone[3] or 0))
            tz = timezone(offset if zone[1] == '+' else -offset)
    starts, deadlines, active, notes, sources = [], [], [], [], []
    entries = []
    ends = []
    matches = list(DATE.finditer(text))
    for idx, match in enumerate(matches):
        # A yearless date is resolved against the email's year, never today's year.
        if not match['y'] and not base:
            continue
        year = int(match['y']) if match['y'] else base.year
        hour = int(match['h'] or 0)
        minute = int(match['minute'] or 0)
        ampm = match['ampm']
        if ampm in ('下午', '晚上', '晚') and hour < 12: hour += 12
        if ampm == '中午' and hour < 11: hour += 12
        if ampm in ('上午', '早上') and hour == 12: hour = 0
        if hour == 24 and minute:
            continue
        try:
            value = datetime(year, int(match['m']), int(match['d']), min(hour, 23), minute, tzinfo=tz)
            if hour == 24:
                value = value.replace(hour=0) + timedelta(days=1)
            if tz is None: value = value.astimezone()
            if not match['y'] and base.month == 12 and int(match['m']) == 1:
                value = value.replace(year=year + 1)
        except ValueError:
            continue
        previous = matches[idx-1].end() if idx else 0
        following = matches[idx+1].start() if idx+1 < len(matches) else len(text)
        left = text[max(previous, match.start()-90):match.start()]
        right = text[match.end():min(following, match.end()+60)]
        left_clause = re.split(r'[。！？;；]', left)[-1]
        kind = None
        # A suffix refers to this date; do not inherit a previous date's label.
        if re.match(r'\s*(?:即将)?\s*(?:失效|到期|截止|截至)', right):
            kind = 'deadline'
        elif re.match(r'\s*(?:之?前|前)\s*(?:完成|提交|作答|参加)', right):
            kind = 'deadline'
        elif re.match(r'\s*生效', right):
            kind = 'active'
        else:
            labels = list(re.finditer(f'(?P<entry>{ENTRY})|(?P<deadline>{DEAD})|(?P<start>{START})', left_clause))
            if labels:
                kind = labels[-1].lastgroup
            elif re.search(r'(?:笔试|考试).{0,25}将于\s*$', left_clause):
                kind = 'start'
        if not kind and idx and starts and re.fullmatch(r'\s*(?:--?|—|~|～|至|到)\s*', left):
            kind = 'deadline'
            left_clause = '考试结束'
        if not kind:
            continue
        precision = 'minute' if match['h'] else 'date'
        snippet = re.sub(r'\s+', ' ', text[max(0, match.start()-35):match.end()+25]).strip()
        if precision == 'date' and kind == 'deadline':
            # Internal end-of-day sorting only. The UI must not claim a supplied time.
            value = value.replace(hour=23, minute=59)
        entry = (value, precision, snippet)
        if kind == 'entry':
            entries.append(entry)
        elif kind == 'deadline':
            if re.search(r'结束时间|考试结束|笔试结束', left_clause):
                ends.append(entry)
            else:
                deadlines.append(entry)
        elif kind == 'active':
            active.append(entry)
        elif kind == 'start':
            starts.append(entry)
            if precision == 'minute':
                end = re.match(r'\s*(?:--?|—|~|～|至|到)\s*([0-2]?\d)[:：]([0-5]\d)(?:[:：][0-5]\d)?', right)
                if end and int(end[1]) < 24:
                    ending = value.replace(hour=int(end[1]), minute=int(end[2]))
                    if ending < value:
                        ending += timedelta(days=1)
                    ends.append((ending, 'minute', snippet + '（考试结束）'))
    for match in REL.finditer(text):
        left = text[max(0, match.start()-65):match.start()]
        right = text[match.end():match.end()+40]
        clause = re.split(r'[。！？;；\n]', left)[-1] + match[0] + re.split(r'[。！？;；\n]', right)[0]
        prefix = re.split(r'[。！？;；，,\n]', left)[-1]
        if re.search(r'验证码|客服|回复|服务电话|测评结果|成绩有效|答题时长|考试时长|答题用时', prefix) or re.match(r'\s*(?:之?内)?\s*(?:回复|答复)', right):
            continue
        relevant = (re.search(r'收到|发出|发送|发起|有效期|链接|结束时间', left)
                    and re.search(r'内|有效|完成|截止|结束', match[0]+right+left[-15:]))
        if not relevant:
            continue
        if match['unit'] == '工作日':
            notes.append('工作日期限涉及节假日，需补充确认。')
            continue
        if re.search(r'首次(?:打开|登录)|开始作答|点击链接后|激活后', left):
            notes.append('期限从首次操作起算，邮件发送时间不能代替。')
            continue
        count = number(match['n'])
        if not count or count > 365:
            continue
        anchor = base
        basis = '邮件发送时间'
        if re.search(r'发起之日|生效|邀请之日', left) and active:
            anchor = active[0][0]
            basis = '邮件注明的生效时间'
        if anchor is None:
            notes.append('邮件发送日期缺失，无法计算相对期限。')
            continue
        amount = timedelta(hours=count) if match['unit'] == '小时' else timedelta(days=count)
        # Elapsed hours cross offset changes in UTC; calendar days keep wall time.
        value = (anchor.astimezone(timezone.utc) + amount).astimezone(anchor.tzinfo) if match['unit'] == '小时' else anchor + amount
        if match['unit'] != '小时' and re.search(r'含当天|包含当天|当日计入', clause):
            value = (anchor + timedelta(days=count-1)).replace(hour=23, minute=59, second=0)
        elif match['unit'] != '小时' and re.search(r'次日起|次日开始', clause):
            value = (anchor + timedelta(days=count)).replace(hour=23, minute=59, second=0)
        snippet = re.sub(r'\s+', ' ', clause).strip()
        explanation = f'{basis} {stamp(anchor)} + {count}{match["unit"]} = {stamp(value)}'
        if match['unit'] != '小时':
            explanation += '；不跳过周末'
            if re.search(r'含当天|包含当天|当日计入|次日起|次日开始', clause):
                explanation += '；按原文计日起点计算，到期日按日末排序'
            else:
                explanation += '；原文未说明含不含当天，暂按发送时刻加日历天，请查看原文确认'
        if '收到' in left:
            explanation += '；收到时间以邮件发送时间代算'
        # An explicit expiry overrides relative wording in the same email.
        if deadlines and not all(e[2].startswith('相对期限：') for e in deadlines):
            explanation = '正文另有明确截止时间，优先采用；相对说明供参考：' + explanation
        sources.append(explanation)
        if not deadlines or all(e[2].startswith('相对期限：') for e in deadlines):
            deadlines.append((value, 'minute', '相对期限：' + snippet))
    result = dict(start='', deadline='', entry='', end='', due='', time_kind='', time_precision='', time_note='', time_evidence='', parser_version=VERSION)
    def choose(entries, label):
        unique = sorted(set(entries), key=lambda e: e[0])
        if not unique:
            return None
        if len({stamp(e[0]) for e in unique}) > 1:
            notes.append(f'存在多个{label}，暂按最早时间提醒，请查看原文。')
        chosen = unique[0]
        if chosen[1] == 'date':
            notes.append(f'{label}只写了日期，未注明时刻；请提前完成准备。')
        return chosen
    start = choose(starts, '开考时间')
    deadline = choose(deadlines, '截止时间')
    ending = choose(ends, '结束时间')
    entry_time = choose(entries, '最晚进入时间')
    if ending:
        result['end'] = stamp(ending[0]); result['end_precision'] = ending[1]
    if entry_time:
        result['entry'] = stamp(entry_time[0]); result['entry_precision'] = entry_time[1]
    if start:
        result['start'] = stamp(start[0])
        result['start_precision'] = start[1]
    if deadline:
        result['deadline'] = stamp(deadline[0])
        result['deadline_precision'] = deadline[1]
    selected = start or entry_time or deadline or ending
    if selected:
        result.update(due=stamp(selected[0]), time_kind='start' if start else ('entry' if entry_time else ('deadline' if deadline else 'end')), time_precision=selected[1])
    else:
        notes.append('正文未识别到明确的开考或截止说明，可查看原邮件或手动补充。')
    result['needs_review'] = any('多个' in n or '需补充' in n or '未说明含不含当天' in n for n in notes + sources)
    result['time_note'] = '\n'.join(dict.fromkeys(notes + sources))
    result['time_evidence'] = '\n'.join(dict.fromkeys(e[2] for e in starts + entries + deadlines + ends))
    return result


def update_item(item):
    """Migrate cached mail while preserving user edits, completion and removal."""
    if item.get('parser_version') == VERSION:
        return False
    if item.get('due') and 'parser_version' not in item:
        item['manual_time'] = True
    parsed = parse_schedule(item.get('text', ''), item.get('received', ''), item.get('title', ''))
    if item.get('manual_time'):
        parsed['due'] = item['due']
        parsed['time_kind'] = item.get('time_kind') or 'deadline'
        parsed['time_precision'] = 'minute'
    item.update(parsed)
    return True
