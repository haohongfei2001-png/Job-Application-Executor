import sqlite3,time
from executor.otp.messages import find_recent_sms_code,APPLE_EPOCH

def test_unique_recent_otp(tmp_path):
    db=tmp_path/'chat.db'; con=sqlite3.connect(db)
    con.executescript('CREATE TABLE handle(ROWID INTEGER PRIMARY KEY,id TEXT); CREATE TABLE message(ROWID INTEGER PRIMARY KEY,text TEXT,date INTEGER,handle_id INTEGER);')
    con.execute('INSERT INTO handle(ROWID,id) VALUES(1,?)',('10690000',))
    now=time.time(); apple_ns=int((now-APPLE_EPOCH)*1_000_000_000)
    con.execute('INSERT INTO message(text,date,handle_id) VALUES(?,?,1)',('Example verification code is 482731',apple_ns)); con.commit(); con.close()
    hit=find_recent_sms_code(db,window_seconds=180,sender_hint='1069',body_keyword='Example',now=now)
    assert hit['code']=='482731'

def test_ambiguous_otp_fails_closed(tmp_path):
    db=tmp_path/'chat.db'; con=sqlite3.connect(db)
    con.executescript('CREATE TABLE handle(ROWID INTEGER PRIMARY KEY,id TEXT); CREATE TABLE message(ROWID INTEGER PRIMARY KEY,text TEXT,date INTEGER,handle_id INTEGER);')
    con.execute('INSERT INTO handle VALUES(1,?)',('1069',)); now=time.time(); d=int((now-APPLE_EPOCH)*1_000_000_000)
    con.execute('INSERT INTO message(text,date,handle_id) VALUES(?,?,1)',('Code 111111',d)); con.execute('INSERT INTO message(text,date,handle_id) VALUES(?,?,1)',('Code 222222',d)); con.commit(); con.close()
    assert find_recent_sms_code(db,window_seconds=180,sender_hint='1069',now=now) is None
