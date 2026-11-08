"""Bounded latest-window scheduling, with deterministic pre-model gates."""
import copy
import re
import time
BREAK = re.compile(r'更正|改为|撤回|不对|换个话题|新话题|接下来讲|下面讲|correction|instead|new topic', re.I)
FILLER = re.compile(r'^(嗯+|啊+|哦+|好+的?|对+的?|是的|谢谢|继续|测试|[\W_])+$')

def signature(row):
    return (row['id'], row.get('text', ''), row.get('speaker'))

def substantive(row):
    text = row.get('text', '').strip()
    return not FILLER.fullmatch(text) and (len(re.sub(r'\W', '', text)) >= 4 or len(text)>=4 and (BREAK.search(text) or re.search(r'\d|[一二三四五六七八九十百千万]+元|什么是|为什么|不懂|不理解|what is|why',text,re.I)))

def context_current(snapshot, current, started, max_age=20):
    """Appending is allowed; corrected/deleted evidence and topic breaks are not."""
    if time.monotonic() - started > max_age:
        return False
    lookup = {u['id']: signature(u) for u in current if u.get('definite', True)}
    if any(lookup.get(u['id']) != signature(u) for u in snapshot):
        return False
    known = {u['id'] for u in snapshot}
    tail = current[next((i + 1 for i in range(len(current)-1, -1, -1)
                         if current[i]['id'] in known), len(current)):]
    return not any(BREAK.search(u.get('text', '')) for u in tail)

class WindowGate:
    def __init__(self, seconds=4, max_wait=8, min_chars=24, max_rows=20, clock=time.monotonic):
        self.seconds, self.max_wait, self.min_chars = seconds, max_wait, min_chars
        self.max_rows, self.clock = max_rows, clock
        self.processed = {}; self.pending_since = None; self.last_attempt = -float('inf')
        self.metrics = {'windows': 0, 'ignored_rows': 0, 'coalesced_rows': 0}

    def take(self, rows, flush=False):
        rows = [u for u in rows if u.get('definite', True)]
        live = {u['id'] for u in rows}
        self.processed = {k: v for k, v in self.processed.items() if k in live}
        delta = [u for u in rows if self.processed.get(u['id']) != signature(u)]
        useful = []
        for u in delta:
            if substantive(u):
                useful.append(u)
            else:
                self.processed[u['id']] = signature(u); self.metrics['ignored_rows'] += 1
        if not useful:
            self.pending_since = None
            return None
        now = self.clock()
        if self.pending_since is None:
            arrivals=[u.get('completed_wall') for u in useful if isinstance(u.get('completed_wall'),(float,int))]
            already_waited=min(self.max_wait,max(0,time.time()-min(arrivals))) if arrivals else 0
            self.pending_since = now-already_waited
        age = now - self.pending_since
        chars = sum(len(u['text']) for u in useful)
        urgent=any(BREAK.search(u['text']) for u in useful)
        interval=min(self.seconds,1) if urgent else self.seconds
        if not flush and (now-self.last_attempt < interval or
            age < interval or (not urgent and chars < self.min_chars and age < self.max_wait)):
            return None
        # One latest snapshot, not a queue of obsolete windows.
        window = copy.deepcopy(rows[-self.max_rows:])
        while len(window)>1 and sum(len(u['text']) for u in window)>6000:window.pop(0)
        selected_ids = {u['id'] for u in window}
        new = copy.deepcopy([u for u in useful if u['id'] in selected_ids])
        for u in rows:
            self.processed[u['id']] = signature(u)
        self.last_attempt = now; self.pending_since = None
        if not new:return None
        self.metrics['windows'] += 1; self.metrics['coalesced_rows'] += len(useful)
        return {'transcript': window, 'delta': new, 'window_wait_seconds': round(age, 3)}
