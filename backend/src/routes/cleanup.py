from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field
from src.config.database import db_session,engine
from src.utils.security import current,writer
from src.services import cleanup as service
from src.services.samples import ensure_unique_index
router=APIRouter()
class CleanupCreate(BaseModel):
    note:str=Field(default='',max_length=300)
@router.get('/cleanup/duplicates')
def duplicates(db=Depends(db_session),user=Depends(current)): return service.scan(db)
@router.get('/cleanup/jobs')
def jobs(db=Depends(db_session),user=Depends(current)): return service.list_jobs(db)
@router.get('/cleanup/jobs/{ident}')
def job(ident:int,db=Depends(db_session),user=Depends(current)): return service.get_job(db,ident)
@router.post('/cleanup/jobs',status_code=201)
def create(payload:CleanupCreate|None=None,db=Depends(db_session),user=Depends(writer)):
    note=payload.note if payload else ''
    return service.create_job(db,user,note)
@router.post('/cleanup/jobs/{ident}/run')
def run(ident:int,db=Depends(db_session),user=Depends(writer)):
    result=service.run(db,ident,user)
    # 批次完成后尝试建立归一化唯一索引兜底；仍有重号则静默保留，待后续收敛
    if result['status']=='completed':
        try: ensure_unique_index(engine)
        except Exception: pass
    return result
