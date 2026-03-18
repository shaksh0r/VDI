from pydantic import BaseModel


class Domain(BaseModel):
    name: str


class SecurityGroup(BaseModel):
    name: str


class NetworkRef(BaseModel):
    uuid: str
