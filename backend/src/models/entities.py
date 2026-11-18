from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Text, UniqueConstraint, Index
from datetime import datetime, timezone
from src.config.database import Base
class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True);email=Column(String(120),unique=True,nullable=False)
    password=Column(String(300),nullable=False);role=Column(String(20),nullable=False)
class Sample(Base):
    __tablename__='samples'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    # 归一化编号：去首尾空格并忽略大小写，重号清理与并发受理均以此口径为准
    code_norm=Column(String(80),nullable=True)
    title=Column(String(160),nullable=False);quantity=Column(Integer,nullable=False);location=Column(String(120),nullable=False)
    __table_args__=(Index('ix_samples_code_norm','code_norm'),)
class Transfer(Base):
    __tablename__='transfers'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False);destination=Column(String(120),nullable=False)
    quantity=Column(Integer,nullable=False);note=Column(String(300),default='')
class Trial(Base):
    __tablename__='trials'
    id=Column(Integer,primary_key=True);name=Column(String(120),nullable=False)
    transfer_id=Column(Integer,ForeignKey('transfers.id'),nullable=False);timeout_ms=Column(Integer,nullable=False)
    status=Column(String(20),default='queued',nullable=False);result=Column(JSON,nullable=True)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc));error=Column(Text,default='')
class TrialBalance(Base):
    __tablename__='trial_balances'
    trial_id=Column(Integer,ForeignKey('trials.id'),primary_key=True);remaining=Column(Integer,nullable=False);received=Column(Integer,nullable=False,default=0)
class Receipt(Base):
    __tablename__='receipts'
    id=Column(Integer,primary_key=True);trial_id=Column(Integer,ForeignKey('trials.id'),nullable=False)
    actor=Column(String(20),nullable=False);quantity=Column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('trial_id','actor'),)
class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer,primary_key=True);actor=Column(String(120),nullable=False);action=Column(String(160),nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class CleanupJob(Base):
    """重号清理批次：逐项独立事务推进，中断后可凭 status 续办，不重复改号。"""
    __tablename__='cleanup_jobs'
    id=Column(Integer,primary_key=True);status=Column(String(20),default='processing',nullable=False)
    note=Column(String(300),default='');total_items=Column(Integer,default=0,nullable=False);done_items=Column(Integer,default=0,nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    finished_at=Column(DateTime(timezone=True),nullable=True);error=Column(Text,default='')
class CleanupItem(Base):
    __tablename__='cleanup_items'
    id=Column(Integer,primary_key=True);job_id=Column(Integer,ForeignKey('cleanup_jobs.id'),nullable=False)
    normalized_key=Column(String(80),nullable=False);sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False)
    original_code=Column(String(80),nullable=False);action=Column(String(10),nullable=False)
    new_code=Column(String(80),nullable=True);status=Column(String(20),default='pending',nullable=False);error=Column(Text,default='')
    __table_args__=(UniqueConstraint('job_id','sample_id'),Index('ix_cleanup_items_job','job_id'))
class CodeReservation(Base):
    """清理期间的号码协调表：frozen=冲突号暂停受理；newcode=改号新号预占，禁止抢注。"""
    __tablename__='code_reservations'
    id=Column(Integer,primary_key=True);normalized_key=Column(String(80),unique=True,nullable=False)
    kind=Column(String(10),nullable=False);job_id=Column(Integer,ForeignKey('cleanup_jobs.id'),nullable=False)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=True)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class SampleCodeAlias(Base):
    """重号归并后，原号的各种写法只指向保留样本；登记时同口径拦截。"""
    __tablename__='sample_code_aliases'
    id=Column(Integer,primary_key=True);sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False)
    alias_code=Column(String(80),unique=True,nullable=False);normalized_key=Column(String(80),nullable=False)
    job_id=Column(Integer,ForeignKey('cleanup_jobs.id'),nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    __table_args__=(Index('ix_aliases_norm','normalized_key'),)
