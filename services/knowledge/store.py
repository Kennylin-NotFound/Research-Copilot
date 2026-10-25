"""SQLite is authoritative; the small exact FAISS index is rebuilt from stored vectors."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4

import faiss
import numpy as np

faiss.omp_set_num_threads(1)

CHAT_ID='research-local'
DATASET_ID='research-local-dataset'

def tokens(text):
    words=re.findall(r'[a-z0-9]+',text.lower())
    for sequence in re.findall(r'[\u3400-\u9fff]+',text):
        words.extend(sequence[i:i+2] for i in range(max(1,len(sequence)-1)))
    return list(dict.fromkeys(words))

def split_pages(pages,size=700,overlap=100):
    chunks=[]
    for page,text in pages:
        text=re.sub(r'[ \t]+',' ',text).strip()
        for start in range(0,len(text),size-overlap):
            part=text[start:start+size].strip()
            if part:
                chunks.append({'page':page,'content':part,'ordinal':len(chunks)+1})
            if start+size>=len(text): break
    return chunks

def normalise(vectors):
    matrix=np.array(vectors,dtype='float32',copy=True,order='C')
    if matrix.ndim!=2 or not np.isfinite(matrix).all() or (np.linalg.norm(matrix,axis=1)==0).any():
        raise ValueError('Embedding vectors must be finite and nonzero')
    faiss.normalize_L2(matrix)
    return matrix

class Store:
    def __init__(self,path,fingerprint):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.fingerprint=fingerprint
        self.lock=threading.RLock()
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,name TEXT NOT NULL,sha256 TEXT NOT NULL,pages INTEGER NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,page INTEGER,ordinal INTEGER NOT NULL,content TEXT NOT NULL,vector BLOB NOT NULL,dimension INTEGER NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED,terms);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,name TEXT NOT NULL,created REAL NOT NULL);
            ''')
            if 'raw' not in {r['name'] for r in db.execute('PRAGMA table_info(documents)')}:
                db.execute('ALTER TABLE documents ADD COLUMN raw BLOB')
            row=db.execute("SELECT value FROM metadata WHERE key='embedding_fingerprint'").fetchone()
            if row and row['value']!=fingerprint:
                raise ValueError('Embedding configuration differs from stored index; rebuild explicitly')
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('embedding_fingerprint',fingerprint))
        self.rebuild()

    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=15)
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rebuild(self):
        with self.lock,self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT c.*,d.name FROM chunks c JOIN documents d ON d.id=c.document_id ORDER BY c.id')]
            index=None
            if rows:
                dimensions={r['dimension'] for r in rows}
                if len(dimensions)!=1: raise ValueError('Mixed embedding dimensions in database')
                matrix=np.stack([np.frombuffer(r['vector'],dtype='float32') for r in rows])
                index=faiss.IndexFlatIP(matrix.shape[1])
                index.add(matrix)
            self.rows=rows
            self.index=index

    def document_id(self,name,raw):
        return hashlib.sha256(name.encode('utf-8')+b'\0'+raw).hexdigest()[:32]

    def has_document(self,document_id):
        with self.db() as db:
            return db.execute('SELECT 1 FROM documents WHERE id=?',(document_id,)).fetchone() is not None

    def add_document(self,name,raw,pages,chunks,vectors):
        matrix=normalise(vectors)
        if len(matrix)!=len(chunks) or not chunks:
            raise ValueError('Chunk/vector count mismatch')
        identifier=self.document_id(name,raw)
        with self.lock:
            with self.db() as db:
                if db.execute('SELECT 1 FROM documents WHERE id=?',(identifier,)).fetchone():
                    return {'id':identifier,'deduplicated':True,'chunks':0}
                if self.index is not None and matrix.shape[1]!=self.index.d:
                    raise ValueError('Embedding dimension changed')
                db.execute('INSERT INTO documents(id,name,sha256,pages,created,raw) VALUES(?,?,?,?,?,?)',(identifier,name,hashlib.sha256(raw).hexdigest(),len(pages),time.time(),raw))
                for chunk,vector in zip(chunks,matrix):
                    chunk_id=f'{identifier}:{chunk["ordinal"]}'
                    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',(chunk_id,identifier,chunk['page'],chunk['ordinal'],chunk['content'],vector.tobytes(),len(vector)))
                    db.execute('INSERT INTO chunks_fts VALUES(?,?)',(chunk_id,' '.join(tokens(chunk['content']))))
            self.rebuild()
        return {'id':identifier,'deduplicated':False,'chunks':len(chunks)}

    def documents(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT d.id,d.name,d.sha256,d.pages,d.created,COUNT(c.id) AS chunks FROM documents d LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created')]

    def source(self,identifier):
        with self.db() as db:
            row=db.execute('SELECT name,raw FROM documents WHERE id=?',(identifier,)).fetchone()
            return dict(row) if row else None

    def delete_document(self,identifier):
        with self.lock:
            with self.db() as db:
                ids=[r['id'] for r in db.execute('SELECT id FROM chunks WHERE document_id=?',(identifier,))]
                for chunk_id in ids:
                    db.execute('DELETE FROM chunks_fts WHERE chunk_id=?',(chunk_id,))
                count=db.execute('DELETE FROM documents WHERE id=?',(identifier,)).rowcount
            self.rebuild()
        return count

    def search(self,question,vector,top_k=4):
        with self.lock:
            if not self.rows: return []
            query=normalise([vector])
            if query.shape[1]!=self.index.d: raise ValueError('Query embedding dimension changed')
            distances,positions=self.index.search(query,min(12,len(self.rows)))
            dense=[(self.rows[int(i)]['id'],float(score)) for score,i in zip(distances[0],positions[0]) if i>=0]
            lexical=[]
            terms=tokens(question)[:60]
            if terms:
                match=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
                with self.db() as db:
                    lexical=[r['chunk_id'] for r in db.execute('SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 12',(match,))]
            scores={}
            cosine=dict(dense)
            for ranking in ([i for i,_ in dense],lexical):
                for rank,identifier in enumerate(ranking,1):
                    scores[identifier]=scores.get(identifier,0)+1/(60+rank)
            lookup={r['id']:r for r in self.rows}
            result=[]
            for identifier in sorted(scores,key=lambda k:(-scores[k],k)):
                if cosine.get(identifier,-1)<0.25 and identifier not in lexical: continue
                row=lookup[identifier]
                result.append({'id':identifier,'document_id':row['document_id'],'document_name':row['name'],'page':row['page'],'chunk':row['ordinal'],'content':row['content'],'cosine':round(cosine.get(identifier,0),6),'rrf_score':round(scores[identifier],6)})
                if len(result)>=top_k: break
            return result

    def create_session(self,name):
        identifier=uuid4().hex
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE created<?',(time.time()-3600,))
            if db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]>=1000:
                raise ValueError('Temporary session limit reached')
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(identifier,CHAT_ID,name,time.time()))
        return {'id':identifier,'chat_id':CHAT_ID,'name':name,'messages':[]}

    def sessions(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM sessions WHERE chat_id=? ORDER BY created',(CHAT_ID,))]

    def session_exists(self,identifier):
        with self.db() as db:
            return db.execute('SELECT 1 FROM sessions WHERE id=? AND chat_id=? AND created>=?',(identifier,CHAT_ID,time.time()-3600)).fetchone() is not None

    def delete_sessions(self,ids,delete_all=False):
        with self.db() as db:
            if delete_all:
                db.execute('DELETE FROM sessions WHERE chat_id=?',(CHAT_ID,))
            else:
                for identifier in ids:
                    db.execute('DELETE FROM sessions WHERE id=? AND chat_id=?',(identifier,CHAT_ID))
