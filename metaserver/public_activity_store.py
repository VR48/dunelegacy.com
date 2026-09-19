"""Python SQLite fallback for the trusted local public-activity hook."""
import json
import re
import time
import unicodedata
from pathlib import Path

FIELDS=('event_id','kind','occurred_at','room_id','player_name','message','channel_id','mod_name',
        'mode','game_version','participant_id','role','start_id','players')
KINDS=('chat_message','public_game_created','public_game_joined','public_game_started')
def text(value, maximum, empty=False):
    return isinstance(value,str) and len(value.encode('utf-8'))<=maximum and (empty or bool(value.strip(" \t\n\r\0\x0b"))) and not any(unicodedata.category(c) in ('Cc','Cf','Zl','Zp') for c in value)
def token(value, pattern):
    return isinstance(value,str) and re.fullmatch(pattern,value) is not None
def integer(value, lo, hi):
    return type(value) is int and lo<=value<=hi

def normalize(e):
    valid=isinstance(e,dict) and set(e)==set(FIELDS)
    if not valid: raise ValueError('invalid public activity')
    valid=(token(e['event_id'],r'[a-f0-9]{32}') and e['kind'] in KINDS
        and integer(e['occurred_at'],0,4102444800) and text(e['player_name'],64)
        and text(e['message'],120,True) and text(e['mod_name'],64,True)
        and token(e['game_version'],r'[A-Za-z0-9._-]{0,64}') and integer(e['participant_id'],0,65535)
        and isinstance(e['players'],list) and len(e['players'])<=12)
    if not valid: raise ValueError('invalid public activity')
    chat=e['kind']=='chat_message'; start=e['kind']=='public_game_started'
    if chat:
        valid=(e['room_id']=='' and token(e['channel_id'],r'[a-f0-9]{64}') and text(e['message'],120)
            and e['participant_id']==0 and all(e[k]=='' for k in ('role','mode','mod_name','game_version','start_id')) and not e['players'])
    else:
        valid=(token(e['room_id'],r'[A-Za-z0-9_-]{22,64}') and e['channel_id']=='' and e['message']=='' and e['mode'] in ('custom','coop'))
        if start:
            valid=valid and token(e['start_id'],r'[a-f0-9]{32}') and bool(e['players']) and e['participant_id']==0 and e['role']==''
        else:
            valid=valid and e['participant_id']>0 and not e['players'] and e['start_id']=='' and e['role']==('host' if e['kind']=='public_game_created' else 'client')
    ids=set(); hosts=0
    for p in e['players']:
        if not isinstance(p,dict) or set(p)!= {'id','name','role','runtime'}: raise ValueError('invalid public roster')
        valid=valid and integer(p['id'],1,65535) and p['id'] not in ids and text(p['name'],64) and p['role'] in ('host','client') and p['runtime'] in ('browser','native')
        if not valid: raise ValueError('invalid public roster')
        ids.add(p['id'])
        if p['role']=='host': hosts+=1; valid=valid and p['name']==e['player_name']
    if not valid or (start and hosts!=1): raise ValueError('invalid public activity')
    return {k:e[k] for k in FIELDS}

def record(connection, event):
    e=normalize(event)
    connection.executescript(Path(__file__).with_name('public_activity.sql').read_text())
    with connection:
        inserted=connection.execute('''INSERT INTO analytics_public_activity
            (event_id,kind,occurred_at,received_at,room_id,player_name,message,details_json)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING''',
            (e['event_id'],e['kind'],e['occurred_at'],int(time.time()),e['room_id'],e['player_name'],e['message'],json.dumps(e,ensure_ascii=False,separators=(',',':'))))
        if not inserted.rowcount:
            old=connection.execute('SELECT details_json FROM analytics_public_activity WHERE event_id=?',(e['event_id'],)).fetchone()
            if json.loads(old[0])!=e: raise ValueError('conflicting public activity event')
