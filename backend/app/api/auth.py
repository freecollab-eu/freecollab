from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from ..services.auth import create_token, verify_password, hash_password, get_current_user, validate_password, revoke_token, security, normalize_email
from ..services.rate_limit import auth_rate_limit, limiter_for, request_address
from ..services.crypto import CryptoService
from ..services.group_crypto import encrypt_private_key
from ..services.account_security import state_for, create_mfa_challenge, queue_account_email
from ..services.mail import mail_available
from ..services.sso_scope import current_workspace_scope
from ..services.workforce_access import is_managed, principal_snapshot
from ..database import get_session
from ..models.user import User
from uuid import uuid4

router = APIRouter(prefix="/auth", tags=["auth"])


class Register(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=12)

    _validate_password = field_validator("password")(validate_password)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    _normalize_email = field_validator("email", mode="before")(normalize_email)


class Login(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: str

    _validate_password = field_validator("password")(validate_password)
    _normalize_email = field_validator("email", mode="before")(normalize_email)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    public_key: str
    encrypted_private_key: str | None = None


class MFARequired(BaseModel):
    mfa_required: bool = True
    challenge_token: str
    expires_in: int = 300


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    public_key: str | None
    role: str
    session_scope: str
    workspace_id: str | None
    account_type: str = "personal"


@router.post("/register", response_model=Token, dependencies=[Depends(auth_rate_limit)])
def register(data: Register, request: Request, session=Depends(get_session)):
    if data.email.rsplit("@", 1)[-1] == "identity.invalid":
        raise HTTPException(400, "This address is reserved for organization-managed accounts")
    existing = session.query(User).filter(func.lower(User.email) == data.email).first()
    if existing:
        raise HTTPException(400, "Email already registered")

    # Generate key material on the server; this is not client-side E2E encryption.
    private_key, public_key = CryptoService.generate_keypair()
    
    # Encrypt the generated private key with the user's password.
    encrypted_private_key = encrypt_private_key(private_key, data.password)

    user = User(
        id=uuid4().hex,
        name=data.name,
        email=data.email,
        password_hash=hash_password(data.password),
        public_key=public_key,
        encrypted_private_key=encrypted_private_key,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.query(User).filter(func.lower(User.email) == data.email).first():
            raise HTTPException(400, "Email already registered")
        raise

    if mail_available():
        try:
            queue_account_email(session, user, "email-verify")
            session.commit()
        except HTTPException:
            # Registration is complete, but no email success is claimed. The
            # signed-in security page can report/retry a misconfigured delivery.
            session.rollback()

    return Token(
        access_token=create_token(user.id, session, request, expected_auth_version=0),
        user_id=user.id,
        public_key=public_key,
        encrypted_private_key=encrypted_private_key,
    )


@router.post("/login", response_model=Token | MFARequired, dependencies=[Depends(auth_rate_limit)])
def login(data: Login, request: Request, session=Depends(get_session)):
    limiter = limiter_for(request)
    address = request_address(request)
    limiter.check_login(address, data.email)
    matches = session.query(User).filter(func.lower(User.email) == data.email).limit(2).all()
    # Legacy databases may contain case-only aliases created by old versions.
    # Do not choose an arbitrary identity when the canonical address is ambiguous.
    user = matches[0] if len(matches) == 1 else None
    state = state_for(session, user.id) if user else None
    version = state.auth_version if state else None
    if not user or is_managed(session, user.id) or not verify_password(data.password, user.password_hash):
        limiter.failed_login(address, data.email)
        raise HTTPException(401, "Invalid credentials")
    if user.is_blocked:
        raise HTTPException(403, "Account is blocked")
    limiter.successful_login(data.email)

    if state.totp_secret:
        return create_mfa_challenge(session, user, request, expected_auth_version=version)

    return Token(
        access_token=create_token(user.id, session, request, expected_auth_version=version),
        user_id=user.id,
        public_key=user.public_key or "",
        encrypted_private_key=user.encrypted_private_key,
    )


@router.post("/logout")
def logout(
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session=Depends(get_session),
):
    revoke_token(credentials.credentials, session)
    return {"logged_out": True}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user), session=Depends(get_session)):
    workspace_id = current_workspace_scope(session)
    principal = principal_snapshot(session, current_user.id)
    return UserResponse(
        id=current_user.id,
        name=current_user.name,
        email=(principal[2] if principal and principal[0] == workspace_id else current_user.email),
        public_key=current_user.public_key,
        role="user" if workspace_id else current_user.role,
        session_scope="workspace" if workspace_id else "account",
        workspace_id=workspace_id,
        account_type="managed" if principal else "personal",
    )


@router.get("/users/{user_id}/public-key")
def get_user_public_key(user_id: str, session=Depends(get_session)):
    """Return the legacy registration key, not a verified E2EE device identity."""
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    
    return {"user_id": user_id, "public_key": user.public_key}


from .account import auth_router as account_auth_router
router.include_router(account_auth_router)
