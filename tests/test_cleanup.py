"""重号清理端到端演练：HTTP + 真实 PostgreSQL 行锁/咨询锁，无 mock。

覆盖：
1. 去首尾空格、忽略大小写的重号识别与每组保留规则
2. 清理期间：无关编号照常登记/移交、冲突号暂停受理、新号禁止抢注
3. 改号与恢复受理同事务生效，原号各种写法只指向保留样本，旧移交仍归原样本
4. 中断续办、重试不重复改号
5. 并发争号：新登记之间争同一新号；清理改号与新登记争同一新号
运行：cd backend && PYTHONPATH=. python ../tests/test_cleanup.py
"""
import concurrent.futures, sys, threading, time
import psycopg
from sqlalchemy import text
from src.config.database import engine, Session
from src.models.entities import User, Sample, Transfer, CleanupItem, Audit
from src.utils.security import password_hash
from src.services import cleanup
import urllib.request, json

BASE='http://127.0.0.1:19242'
PASS, FAILS = 0, []
def check(name, cond, detail=''):
    global PASS
    if cond: PASS += 1; print(f'  ✓ {name}')
    else: FAILS.append(f'{name} {detail}'); print(f'  ✗ {name} {detail}')

def http(method, path, token=None, body=None):
    req=urllib.request.Request(BASE+path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json', **({'Authorization':'Bearer '+token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

def login(role):
    _,data=http('POST','/auth/login',body={'email':f'{role}@qingyu.test','password':'Demo2026!'})
    return data['token']

def reset_world():
    with engine.begin() as conn:
        # 唯一索引是清理完成后的兜底；重置为“存在存量重号”的初始态需先移除
        conn.execute(text('DROP INDEX IF EXISTS ux_samples_code_norm'))
        conn.execute(text('TRUNCATE audit_logs, receipts, trial_balances, trials, transfers, '
                          'sample_code_aliases, code_reservations, cleanup_items, cleanup_jobs, samples, users RESTART IDENTITY CASCADE'))
    with Session() as db:
        for role in ('admin','operator','viewer'):
            db.add(User(email=f'{role}@qingyu.test', password=password_hash('Demo2026!'), role=role))
        # 两组重号：差异仅在首尾空格/大小写；另在重号样本上挂一条历史移交
        rows=[('QY-261117-A','A组保留件',120,'A-03'),('  qy-261117-a ','A组重复件1',40,'A-05'),
              ('QY-261117-a','A组重复件2',25,'A-06'),
              ('QY-261117-B','B组保留件',80,'B-12'),('QY-261117-B ','B组重复件',10,'B-18'),
              ('QY-261117-C','无关样本',60,'A-07')]
        for code,title,qty,loc in rows:
            db.add(Sample(code=code,title=title,quantity=qty,location=loc,code_norm=code.strip().lower()))
        db.flush()
        dup=db.query(Sample).filter(Sample.code=='  qy-261117-a ').one()
        keep=db.query(Sample).filter(Sample.code=='QY-261117-A').one()
        db.add(Transfer(code='MOVE-OLD-1',sample_id=dup.id,destination='理化检测室',quantity=5,note='清理前旧移交'))
        db.add(Transfer(code='MOVE-OLD-2',sample_id=keep.id,destination='复核室',quantity=7,note='保留件旧移交'))
        db.commit()

print('== 0. 环境重置与重号扫描 ==')
reset_world()
tok=login('operator'); viewer=login('viewer')
st,dups=http('GET','/cleanup/duplicates',tok)
check('扫描出 2 组重号', st==200 and len(dups)==2, str(dups))
g={d['normalized_key']:d for d in dups}
check('每组仅 id 最小者 keep', all(sum(1 for s in d['samples'] if s['keep'])==1 for d in dups))
check('keep 即每组首登记件', g['qy-261117-a']['samples'][0]['code']=='QY-261117-A' and g['qy-261117-a']['samples'][0]['keep'])

print('== 1. 建批即冻结冲突号、预占新号 ==')
st,job=http('POST','/cleanup/jobs',tok,{'note':'首轮清理'})
check('批次创建 201', st==201, str(job))
job_id=job['id']
renames=[i for i in job['items'] if i['action']=='rename']
check('3 个改号项新号互不相同', len({i['new_code'] for i in renames})==3)
st,viewer_job=http('POST',f'/cleanup/jobs/{job_id}/run',viewer)
check('viewer 无权推进清理 (403)', st==403)
for code in ('QY-261117-A',' qy-261117-a ','QY-261117-B '):
    st,_=http('POST','/samples',tok,{'code':code,'title':'撞冻结号','quantity':1,'location':'X'})
    check(f'冲突号暂停受理 {code!r}', st==409)
for code in ('QY-261117-A-R01',' qy-261117-a-r02 ','QY-261117-B-R01'):
    st,_=http('POST','/samples',tok,{'code':code,'title':'抢注','quantity':1,'location':'X'})
    check(f'新号禁止抢注 {code!r}', st==409)
st,_=http('POST','/samples',tok,{'code':' QY-FREE-01 ','title':'无关新号','quantity':9,'location':'C-9'})
check('清理期间无关编号照常登记', st==201)
with Session() as db:
    sid=db.query(Sample).filter(Sample.code=='  qy-261117-a ').one().id
st,_=http('POST','/transfers',tok,{'code':'MOVE-DURING-1','sample_id':sid,'destination':'金属分析室','quantity':2,'note':'清理期间移交'})
check('清理期间移交照常发起', st==201)

print('== 2. 中断后续办：只处理第一项后中断 ==')
with Session() as db:
    first=db.query(CleanupItem).filter(CleanupItem.job_id==job_id).order_by(CleanupItem.id).first()
    first_id=first.id
cleanup._process_item(first_id)
with Session() as db:
    done=db.query(CleanupItem).filter(CleanupItem.job_id==job_id, CleanupItem.status=='done').count()
check('中断时仅 1 项完成', done==1, f'done={done}')
st,job2=http('POST',f'/cleanup/jobs/{job_id}/run',tok)
check('续办成功', st==200 and job2['status']=='completed', str(job2.get('status')))
check('5 项全部 done', job2['done_items']==5 and all(i['status']=='done' for i in job2['items']))
snapshot_before={}
with Session() as db:
    for s in db.query(Sample).all(): snapshot_before[s.id]=s.code
st,job3=http('POST',f'/cleanup/jobs/{job_id}/run',tok)
check('重复 run 幂等不报错', st==200 and job3['status']=='completed')
with Session() as db:
    snapshot_after={s.id:s.code for s in db.query(Sample).all()}
check('重试不重复改号（编号无变化）', snapshot_before==snapshot_after)

print('== 3. 改号结果、旧移交归属、别名归并、唯一索引 ==')
with Session() as db:
    by_code={s.code:s for s in db.query(Sample).all()}
    id_keep_a=by_code['QY-261117-A'].id
    id_keep_b=by_code['QY-261117-B'].id
    ren={s.code:s for s in db.query(Sample).filter(Sample.code.like('%R0%')).all()}
    id_dup_a=db.query(Sample).filter(Sample.code=='  qy-261117-a ').count()
    # 原重复件已改号，通过清理项记录定位其当前 id
    moved_sample_id=db.execute(text("SELECT sample_id FROM cleanup_items WHERE original_code='  qy-261117-a ' AND action='rename'")).scalar()
    transfers={t.code:(t.sample_id) for t in db.query(Transfer).all()}
    alias_rows=db.execute(text('SELECT sample_id, alias_code FROM sample_code_aliases ORDER BY id')).all()
    frozen=db.execute(text("SELECT count(*) FROM code_reservations WHERE kind='frozen'")).scalar()
    newcodes=db.execute(text("SELECT count(*) FROM code_reservations WHERE kind='newcode'")).scalar()
    idx=db.execute(text("SELECT 1 FROM pg_indexes WHERE indexname='ux_samples_code_norm'")).scalar()
check('保留件维持原号', 'QY-261117-A' in by_code and 'QY-261117-B' in by_code)
check('重复件已改号（原写法已不存在）', id_dup_a==0 and len(ren)==3 and all(c.endswith(('-R01','-R02')) for c in ren))
check('旧移交仍归原样本（sample_id 未漂移）',
      transfers['MOVE-OLD-1']==moved_sample_id and transfers['MOVE-OLD-2']==id_keep_a and transfers['MOVE-DURING-1']==moved_sample_id)
check('原号每种写法都有别名', len(alias_rows)==5 and all(a[0] in (id_keep_a,id_keep_b) for a in alias_rows))
check('别名只指向保留样本', {a[0] for a in alias_rows if a[1].strip().lower()=='qy-261117-a'}=={id_keep_a})
check('冻结已随完成释放', frozen==0)
check('新号预占保留防回流', newcodes==3)
check('归一化唯一索引已建立', idx==1)

print('== 4. 恢复受理：原号写法只指向保留样本，新号可登记 ==')
for code in ('QY-261117-A',' qy-261117-a ','QY-261117-a','QY-261117-B '):
    st,data=http('POST','/samples',tok,{'code':code,'title':'复用原号','quantity':1,'location':'X'})
    check(f'原号写法被别名拦截 {code!r}', st==409 and '归并' in data['detail'], str(data))
st,_=http('POST','/samples',tok,{'code':'QY-AFTER-01','title':'恢复后新号','quantity':3,'location':'D-1'})
check('受理恢复后新号可登记', st==201)
st,_=http('POST','/transfers',tok,{'code':'MOVE-AFTER-1','sample_id':1,'destination':'复核室','quantity':1,'note':'恢复后移交'})
check('恢复后移交可发起', st==201)

print('== 5. 并发争号 A：8 个新登记争同一个全新号 ==')
barrier=threading.Barrier(8)
def register_fresh(_):
    barrier.wait()
    return http('POST','/samples',tok,{'code':' QY-RACE-X ','title':'并发新号','quantity':1,'location':'R'})
with concurrent.futures.ThreadPoolExecutor(8) as ex:
    results=list(ex.map(register_fresh, range(8)))
wins=[r for r in results if r[0]==201]
check('仅 1 个登记成功', len(wins)==1, str([r[0] for r in results]))
check('其余全部 409', all(r[0]==409 for r in results if r[0]!=201))
with Session() as db:
    n=db.query(Sample).filter(Sample.code_norm=='qy-race-x').count()
check('库里恰好一行', n==1, f'n={n}')

print('== 6. 并发争号 B：清理改号与新登记争同一个新号 ==')
# 直接构造一组“存量”重号：唯一索引是清理完成后的兜底，需先临时降级再恢复
with engine.begin() as conn:
    conn.execute(text('DROP INDEX IF EXISTS ux_samples_code_norm'))
with Session() as db:
    db.add(Sample(code='QY-RACE-G',title='并发组保留',quantity=5,location='G-1',code_norm='qy-race-g'))
    db.add(Sample(code=' qy-race-g ',title='并发组重复',quantity=5,location='G-2',code_norm='qy-race-g'))
    db.commit()
st,jobb=http('POST','/cleanup/jobs',tok,{'note':'并发清理批'})
target=[i['new_code'] for i in jobb['items'] if i['action']=='rename'][0]
bid=jobb['id']
barrier2=threading.Barrier(6)
def race_cleanup(_):
    barrier2.wait(); return http('POST',f'/cleanup/jobs/{bid}/run',tok)
def race_register(_):
    barrier2.wait(); return http('POST','/samples',tok,{'code':target,'title':'并发抢注','quantity':1,'location':'R'})
with concurrent.futures.ThreadPoolExecutor(6) as ex:
    # 必须先统一提交，再统一收集；顺序 map 会让先提交的线程独自死等 Barrier
    futures=[ex.submit(race_cleanup,i) for i in range(1)]+[ex.submit(race_register,i) for i in range(5)]
    results=[f.result() for f in futures]
cr,rr=results[:1],results[1:]
check('清理改号提交成功', cr[0][0]==200 and cr[0][1]['status']=='completed', str(cr[0]))
check('并发登记无一抢注成功', all(r[0]==409 for r in rr), str([r[0] for r in rr]))
with Session() as db:
    n=db.query(Sample).filter(Sample.code_norm==target.strip().lower()).count()
    renamed=db.query(Sample).filter(Sample.code==target).count()
check('目标新号恰好归属改号样本一行', n==1 and renamed==1, f'n={n}')

print('== 7. 审计留痕 ==')
with Session() as db:
    actions=[a.action for a in db.query(Audit).all()]
check('清理关键动作均有审计', any('创建重号清理' in a for a in actions) and any('改号' in a for a in actions) and any('恢复受理' in a for a in actions))

print(f'\n结果：{PASS} 项通过，{len(FAILS)} 项失败')
if FAILS:
    print('\n'.join(' - '+x for x in FAILS)); sys.exit(1)
