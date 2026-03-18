from pydantic import BaseModel
from typing import List, Optional
from .common import Domain


class User(BaseModel):
    name: str
    domain: Domain
    password: str


class Password(BaseModel):
    user: User


class Identity(BaseModel):
    methods: List[str]
    password: Password


class Project(BaseModel):
    name: str
    domain: Domain


class Scope(BaseModel):
    project: Project


class ScopedAuth(BaseModel):
    identity: Identity
    scope: Scope


class ScopedAuthRequest(BaseModel):
    auth: ScopedAuth


class UnscopedAuth(BaseModel):
    identity: Identity


class UnscopedAuthRequest(BaseModel):
    auth: UnscopedAuth
