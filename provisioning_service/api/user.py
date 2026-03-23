from fastapi import APIRouter,Depends,HTTPException,status
import bcrypt
import uuid
from models.user import Signup,Login
from ..database_connection import get_db
from datetime import datetime, timedelta

router = APIRouter()


@router.post("/signup")
async def signup(info: Signup,db = Depends(get_db)):
    username = info.username
    email = info.email
    password = info.password.encode("utf-8")
    role = info.role

    hashed_password = bcrypt.hashpw(password, bcrypt.gensalt()).decode()

    await db.execute("INSERT into users (username,email,password_hash,role) VALUES ($1,$2,$3,$4)",username,email,hashed_password,role)

    return "done"


@router.post("/login")
async def login(info: Login, db = Depends(get_db)):
    provided_username = info.username
    provided_email = info.email
    provided_password = info.password.encode("utf-8")

    user = await db.fetchrow("SELECT * FROM users WHERE username = $1",provided_username)

    if user == None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="user not found")

    user = dict(user)



    password_validation = bcrypt.checkpw(provided_password,user.get("password_hash").encode("utf-8"))

    if password_validation:
        token = uuid.uuid4()
        token_bytes = str(token).encode("utf-8")
        hashed_token = bcrypt.hashpw(token_bytes,salt=bcrypt.gensalt()).decode()

        now = datetime.now()
        expires_at = now + timedelta(minutes=30)

        user_session_conf = await db.execute("INSERT into user_sessions (user_id, token_hash, expires_at) VALUES ($1,$2,$3)",user.get("user_id"),
                                             hashed_token,expires_at)
        
        if user_session_conf:
            return {"token":token}
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="password invalid")

