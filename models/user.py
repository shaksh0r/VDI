from pydantic import BaseModel,Field,field_validator

class Signup(BaseModel):
    username: str = Field(min_length=4,max_length=40)
    email: str 
    password: str = Field(min_length=8)
    role: str = "student"


    @field_validator("role")
    def validate_role(cls, value):
        roles = ["admin","teacher","student","researcher"]

        if value.lower() not in roles:
            raise ValueError("role not defined")

        return value.lower()
    
class Login(BaseModel):
    username: str = Field(min_length=4,max_length=40)
    email: str 
    password: str = Field(min_length=8)

