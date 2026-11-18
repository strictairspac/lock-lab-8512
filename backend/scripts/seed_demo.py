from sqlalchemy import select,func
from src.config.database import Session,Base,engine
from src.models.entities import User,Sample,Transfer,Trial
from src.utils.security import password_hash
from src.services.codes import normalize_code

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@qingyu.test'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,password=password_hash('Demo2026!'),role=role))
        # 含两组重号演示数据：仅首尾空格与大小写不同，归一化后重号
        samples=[('QY-261117-A','近海水质留样 · A组',120,'冷藏间 A-03'),
                 ('QY-261117-B','土壤重金属平行样',80,'样本柜 B-12'),
                 ('QY-261117-C','饮用水复测留样',60,'冷藏间 A-07'),
                 ('  qy-261117-a ','近海水质留样 · 重复登记',40,'冷藏间 A-05'),
                 ('QY-261117-a','近海水质留样 · 大小写重号',25,'冷藏间 A-06'),
                 ('QY-261117-B ','土壤平行样 · 尾随空格重号',10,'样本柜 B-18')]
        for code,title,qty,loc in samples:
            if not db.scalar(select(Sample).where(Sample.code==code)):
                db.add(Sample(code=code,title=title,quantity=qty,location=loc,code_norm=normalize_code(code)))
        db.flush()
        base=[('QY-261117-A','MOVE-1117-01','理化检测室',30),('QY-261117-B','MOVE-1117-02','金属分析室',20),
              ('QY-261117-C','MOVE-1117-03','复核室',15)]
        for code,req,dest,qty in base:
            if not db.scalar(select(Transfer).where(Transfer.code==req)):
                sample=db.scalar(select(Sample).where(Sample.code==code))
                db.add(Transfer(code=req,sample_id=sample.id,destination=dest,quantity=qty,note='09:15 交接班复核'))
        db.commit()
        counts={cls.__tablename__:db.scalar(select(func.count()).select_from(cls)) for cls in (User,Sample,Transfer,Trial)}
        print(counts)
if __name__=='__main__': seed()
