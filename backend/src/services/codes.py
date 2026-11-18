"""样本编号口径与受理校验：去首尾空格、忽略大小写；清理期间冻结冲突号、禁止抢注。"""
from sqlalchemy import select, text
from fastapi import HTTPException
from src.models.entities import Sample, CodeReservation, SampleCodeAlias

# 冲突号暂停受理
FROZEN='frozen'
# 改号新号预占
NEWCODE='newcode'

def normalize_code(value):
    """统一口径：去掉首尾空格后忽略大小写。"""
    return (value or '').strip().lower()

def lock_key(db, normalized_key):
    """对单个归一化编号加事务级咨询锁，所有同号写操作在此串行化。"""
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':normalized_key})

def find_reservation(db, normalized_key):
    return db.scalar(select(CodeReservation).where(CodeReservation.normalized_key==normalized_key))

def find_alias(db, normalized_key):
    return db.scalar(select(SampleCodeAlias).where(SampleCodeAlias.normalized_key==normalized_key))

def find_sample_by_norm(db, normalized_key):
    return db.scalar(select(Sample).where(Sample.code_norm==normalized_key))

def reject_if_unavailable(db, normalized_key):
    """受理前按口径判定：冻结号 409，已归并别名号 409，已存在样本号 409。调用方须先持该 key 的咨询锁。"""
    reservation=find_reservation(db,normalized_key)
    if reservation and reservation.kind==FROZEN:
        raise HTTPException(409,'该编号属于重号清理范围，冲突号已暂停受理，请等待清理恢复后再登记')
    if find_alias(db,normalized_key):
        raise HTTPException(409,'该编号已归并到保留样本，原号的各种写法只指向保留样本，请使用其他编号')
    if find_sample_by_norm(db,normalized_key):
        raise HTTPException(409,'编号已存在（按去首尾空格、忽略大小写判定），请调整后重试')

def accept_new_code(db, raw_code):
    """新登记/改样本编号的统一入口：归一化 -> 同号串行锁 -> 冻结/抢注/重号校验，返回归一化值。"""
    key=normalize_code(raw_code)
    lock_key(db,key)
    reservation=find_reservation(db,key)
    if reservation and reservation.kind==FROZEN:
        raise HTTPException(409,'该编号属于重号清理范围，冲突号已暂停受理，请等待清理恢复后再登记')
    if reservation and reservation.kind==NEWCODE:
        raise HTTPException(409,'该新号已被改号清理预占，禁止抢注，请更换编号')
    reject_if_unavailable(db,key)
    return key

def resolve_code(db, raw_code):
    """按口径解析编号：别名（原号各种写法）只指向保留样本；否则按当前样本号匹配。"""
    key=normalize_code(raw_code)
    alias=find_alias(db,key)
    if alias: return db.get(Sample,alias.sample_id)
    return find_sample_by_norm(db,key)

def duplicate_groups(db):
    """扫描重号组：同 code_norm 出现多份样本即冲突，按 id 最小者保留原号。"""
    rows=db.execute(select(Sample).order_by(Sample.id)).scalars().all()
    groups={}
    for sample in rows:
        groups.setdefault(normalize_code(sample.code),[]).append(sample)
    return [{'normalized_key':key,'samples':items} for key,items in groups.items() if len(items)>1]
