"""Bounded, expiring browser credentials and single-use audio tickets."""
import secrets,time
class Vault:
 def __init__(self,clock=time.monotonic,limit=64):self.clock=clock;self.limit=limit;self.rows={}
 def prune(self):
  self.rows={k:v for k,v in self.rows.items() if v[0]>self.clock()}
 def issue(self,payload,ttl):
  self.prune()
  if len(self.rows)>=self.limit:raise ValueError('too many outstanding tickets')
  token=secrets.token_urlsafe(32);self.rows[token]=(self.clock()+ttl,payload);return token
 def get(self,token):
  self.prune();row=self.rows.get(token);return row[1] if row else None
 def consume(self,token,expected=None):
  value=self.get(token)
  if value is None or (expected is not None and value!=expected):return None
  del self.rows[token];return value
