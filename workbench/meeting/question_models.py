from typing import Literal
from pydantic import BaseModel,Field

class SubtitleQuestion(BaseModel):
 request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{8,64}$')
 utterance_id:str=Field(min_length=1,max_length=400)
 source:Literal['original','translation']='original'
 quote:str=Field(min_length=1,max_length=1200)
 question:str=Field(min_length=1,max_length=600)
 model_config={'extra':'forbid'}

class QuestionLoginCode(BaseModel):
 code:str=Field(min_length=20,max_length=100)
