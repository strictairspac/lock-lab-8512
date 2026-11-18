"""重号清理批次服务。

规则：
- 归一化口径（去首尾空格、忽略大小写）出现多份即重号；每组 id 最小的一份保留原号，其余改号。
- 清理期间冲突号经 code_reservations(frozen) 暂停受理，新号经 newcode 预占，禁止抢注。
- 每项独立事务推进，中断后可续办；已改号项凭状态跳过，重试不重复改号。
- 组内最后一项完成时，同事务删除冻结、登记别名（原号各种写法只指向保留样本），受理与改号一起恢复。
"""
from datetime import datetime,timezone
from sqlalchemy import select,text
from fastapi import HTTPException
from src.config.database import Session
from src.models.entities import (Sample,CleanupJob,CleanupItem,CodeReservation,SampleCodeAlias,Audit)
from src.services import codes
from src.utils.serialization import record

# 清理批次编排用全局锁，避免两批清理并发推进
GATE_LOCK_KEY='sample-cleanup-gate'

def _generate_new_code(db, base_code, normalized_key, planned):
    """生成不与任何样本/别名/预占/本批计划冲突的改号新号：原号-R01、-R02 顺延。"""
    suffix=1
    while True:
        candidate=f'{base_code}-R{suffix:02d}'
        key=codes.normalize_code(candidate)
        if key in planned or codes.find_reservation(db,key) or codes.find_alias(db,key) or codes.find_sample_by_norm(db,key):
            suffix+=1;continue
        planned.add(key)
        return candidate

def _serialize_job(db, job):
    items=list(db.scalars(select(CleanupItem).where(CleanupItem.job_id==job.id).order_by(CleanupItem.id)))
    data=record(job);data['items']=[record(x) for x in items];return data

def list_jobs(db):
    jobs=list(db.scalars(select(CleanupJob).order_by(CleanupJob.id.desc())))
    return [_serialize_job(db,job) for job in jobs]

def get_job(db, ident):
    job=db.get(CleanupJob,ident)
    if not job: raise HTTPException(404,'清理批次不存在')
    return _serialize_job(db,job)

def scan(db):
    """返回当前重号组，供样本页面确认每组保留哪一份、其余改号。"""
    return [{'normalized_key':g['normalized_key'],
             'samples':[{'id':s.id,'code':s.code,'title':s.title,'keep':s.id==min(x.id for x in g['samples'])}
                        for s in sorted(g['samples'],key=lambda x:x.id)]}
            for g in codes.duplicate_groups(db)]

def create_job(db, user, note=''):
    """按当前重号快照建批并冻结冲突号；无冲突则拒绝建批。"""
    codes.lock_key(db,GATE_LOCK_KEY)
    active=db.scalar(select(CleanupJob).where(CleanupJob.status=='processing'))
    if active: raise HTTPException(409,f'清理批次 #{active.id} 仍在处理中，请先续办完成后再建新批次')
    groups=codes.duplicate_groups(db)
    if not groups: raise HTTPException(422,'当前没有按统一口径判定的重号，无需清理')
    job=CleanupJob(note=note or '重号清理批次',total_items=sum(len(g['samples']) for g in groups))
    db.add(job);db.flush()
    planned=set()
    for g in groups:
        keep_id=min(s.id for s in g['samples'])
        keep_sample=db.get(Sample,keep_id)
        for sample in sorted(g['samples'],key=lambda x:x.id):
            action='keep' if sample.id==keep_id else 'rename'
            new_code=None
            if action=='rename':
                # 新号沿用保留样本原号写法，建批即预占，杜绝执行前抢注窗口
                new_code=_generate_new_code(db,keep_sample.code,g['normalized_key'],planned)
                db.add(CodeReservation(normalized_key=codes.normalize_code(new_code),kind=codes.NEWCODE,
                                       job_id=job.id,sample_id=sample.id))
            db.add(CleanupItem(job_id=job.id,normalized_key=g['normalized_key'],sample_id=sample.id,
                               original_code=sample.code,action=action,new_code=new_code))
        # 冲突号暂停受理（同组归一化键唯一，重复建批也不会重复占用）
        if not codes.find_reservation(db,g['normalized_key']):
            db.add(CodeReservation(normalized_key=g['normalized_key'],kind=codes.FROZEN,
                                   job_id=job.id,sample_id=keep_id))
    db.add(Audit(actor=user.email,action=f'创建重号清理批次 #{job.id}，冲突号 {len(groups)} 组暂停受理'))
    db.commit()
    return get_job(db,job.id)

def _process_item(item_id):
    """单项独立事务：幂等推进。任何异常回滚该项全部写入，标记 failed，不影响其它项与批次续办。"""
    with Session() as db:
        item=db.get(CleanupItem,item_id)
        if not item or item.status=='done': return
        job=db.get(CleanupJob,item.job_id)
        sample=db.get(Sample,item.sample_id)
        if not sample:
            item.status='failed';item.error='样本已不存在';db.commit();return
        try:
            if item.action=='rename':
                # 对目标新号加串行锁，与并发新登记争同一新号时在此排队，先到者得
                new_key=codes.normalize_code(item.new_code)
                codes.lock_key(db,new_key)
                # 重试不重复改号：样本仍挂原号才执行
                if codes.normalize_code(sample.code)==item.normalized_key:
                    reservation=codes.find_reservation(db,new_key)
                    if not reservation or reservation.kind!=codes.NEWCODE or reservation.sample_id!=sample.id:
                        raise RuntimeError('目标新号预占失效或已被其他流程占用')
                    sample.code=item.new_code.strip();sample.code_norm=new_key
                    db.add(Audit(actor='cleanup',action=f'样本 #{sample.id} 改号 {item.original_code} -> {item.new_code}'))
                item.status='done';item.error=''
            else:
                # 保留项：确认原号仍在保留样本上
                if codes.normalize_code(sample.code)!=item.normalized_key:
                    raise RuntimeError('保留样本编号已被改动')
                item.status='done';item.error=''
            db.flush()
            # 组内全部完成：恢复受理与改号一起生效（单事务原子提交）
            pending=db.scalar(select(CleanupItem).where(CleanupItem.normalized_key==item.normalized_key,
                                                        CleanupItem.job_id==job.id,
                                                        CleanupItem.status!='done'))
            if not pending:
                keep=db.scalar(select(CleanupItem).where(CleanupItem.job_id==job.id,
                                                         CleanupItem.normalized_key==item.normalized_key,
                                                         CleanupItem.action=='keep'))
                keep_sample=db.get(Sample,keep.sample_id)
                if codes.normalize_code(keep_sample.code)!=item.normalized_key:
                    raise RuntimeError('保留样本编号与原号不一致')
                # 原号的各种写法（库里出现过的精确写法）登记为别名，只指向保留样本
                seen=set()
                for old in db.scalars(select(CleanupItem).where(CleanupItem.job_id==job.id,
                                                                CleanupItem.normalized_key==item.normalized_key)):
                    if old.original_code not in seen:
                        seen.add(old.original_code)
                        db.add(SampleCodeAlias(sample_id=keep.sample_id,alias_code=old.original_code,
                                               normalized_key=item.normalized_key,job_id=job.id))
                # 释放冲突号冻结；新号预占保留，防止恢复后抢注回旧新号
                frozen=codes.find_reservation(db,item.normalized_key)
                if frozen: db.delete(frozen)
                db.add(Audit(actor='cleanup',
                             action=f'重号组 {item.normalized_key} 清理完成，恢复受理并归并到保留样本 #{keep.sample_id}'))
            item_query=select(CleanupItem).where(CleanupItem.job_id==job.id)
            job.done_items=len([x for x in db.scalars(item_query).all() if x.status=='done'])
            remaining=db.scalar(select(CleanupItem).where(CleanupItem.job_id==job.id,
                                                          CleanupItem.status!='done'))
            if not remaining:
                job.status='completed';job.finished_at=datetime.now(timezone.utc);job.error=''
                db.add(Audit(actor='cleanup',action=f'重号清理批次 #{job.id} 全部完成，受理恢复'))
            db.commit()
        except Exception as exc:
            db.rollback()
            with Session() as fail_db:
                again=fail_db.get(CleanupItem,item_id)
                if again and again.status!='done':
                    again.status='failed';again.error=str(exc)[:300]
                fail_job=fail_db.get(CleanupJob,job.id)
                if fail_job and fail_job.status=='processing':
                    fail_job.error=f'存在失败项，可调用续办接口重试：{exc}'[:300]
                fail_db.commit()

def run(db, ident, user):
    """推进批次：处理全部待办/失败项。逐项独立事务，可重复调用，不重复改号。"""
    codes.lock_key(db,GATE_LOCK_KEY)
    job=db.get(CleanupJob,ident)
    if not job: raise HTTPException(404,'清理批次不存在')
    if job.status=='completed': return get_job(db,job.id)
    job.status='processing';job.error='';db.commit()
    items=list(db.scalars(select(CleanupItem).where(CleanupJob.id==job.id,
                                                    CleanupItem.status!='done').order_by(CleanupItem.id)))
    db.add(Audit(actor=user.email,action=f'推进重号清理批次 #{job.id}，待处理 {len(items)} 项'))
    db.commit()
    for item in items: _process_item(item.id)
    # 各项在独立会话/事务中提交，丢弃本会话缓存后读取最新状态
    db.expire_all()
    return get_job(db,job.id)
