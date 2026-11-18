from sqlalchemy import select,text
from fastapi import HTTPException
from src.models.entities import Sample
from .common import save
from src.services import codes
from src.utils.serialization import record
def listing(db): return [record(x) for x in db.scalars(select(Sample).order_by(Sample.id))]
def create(db,data,user):
    raw=dict(data.model_dump());raw['code']=raw['code'].strip()
    # 同号咨询锁内判定冻结/抢注/重号，清理与新登记争同一新号时由数据库锁裁决
    key=codes.accept_new_code(db,raw['code'])
    return save(db,Sample(**raw,code_norm=key),user,'创建样本 '+raw['code'])
def update(db,ident,data,user):
    obj=db.get(Sample,ident)
    if not obj: raise HTTPException(404,'样本不存在')
    payload=data.model_dump();new_code=payload['code'].strip()
    if codes.normalize_code(new_code)!=codes.normalize_code(obj.code):
        # 改编号同样受冻结、预占与重号约束
        payload['code_norm']=codes.accept_new_code(db,new_code)
    else:
        payload['code_norm']=codes.normalize_code(new_code)
    payload['code']=new_code
    for k,v in payload.items(): setattr(obj,k,v)
    return save(db,obj,user,'更新样本 '+new_code)
def ensure_unique_index(engine):
    """全部重号清理完成后建立归一化唯一索引，作为并发受理的数据库级兜底。"""
    with engine.begin() as conn:
        conn.execute(text("UPDATE samples SET code_norm=lower(btrim(code)) WHERE code_norm IS NULL"))
        exists=conn.scalar(text("SELECT 1 FROM pg_indexes WHERE indexname='ux_samples_code_norm'"))
        if not exists:
            dupes=conn.scalar(text("SELECT count(*) FROM (SELECT 1 FROM samples GROUP BY code_norm HAVING count(*)>1) d"))
            # 仍有重号（清理未完成）时不创建，待后续批次收敛后由路由再次调用
            if not dupes:
                conn.execute(text('CREATE UNIQUE INDEX ux_samples_code_norm ON samples(code_norm)'))
