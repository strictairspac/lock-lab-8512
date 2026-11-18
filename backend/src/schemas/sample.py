from pydantic import BaseModel, Field, field_validator
class SampleInput(BaseModel):
    code: str=Field(min_length=2,max_length=80)
    title: str=Field(min_length=2,max_length=160)
    quantity: int=Field(ge=1,le=100000)
    location: str=Field(min_length=1,max_length=120)
    @field_validator('code')
    @classmethod
    def _normalize_code(cls,value):
        # 入口即按统一口径去首尾空格，避免纯空格编号落库
        value=value.strip()
        if len(value)<2: raise ValueError('批次编号去首尾空格后至少 2 个字符')
        return value
