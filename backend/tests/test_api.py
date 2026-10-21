import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from app import database
from app.main import app
from app.realtime import socket


@pytest.fixture
def client(monkeypatch):
    """Run the real application with a fresh database for every test."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(socket, "engine", engine)

    def session_override():
        with Session(engine) as session:
            yield session

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[database.get_session] = session_override
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        engine.dispose()


class TestAuth:
    """Test authentication endpoints."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup for each test - use unique email."""
        import uuid
        self.email = f"test-{uuid.uuid4().hex[:8]}@example.com"
        self.password = "securepassword123"
        self.name = "Test User"

    def test_register_success(self, client):
        """Test successful user registration."""
        response = client.post(
            "/auth/register",
            json={
                "name": self.name,
                "email": self.email,
                "password": self.password,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "user_id" in data
        assert "public_key" in data
        assert "encrypted_private_key" in data
        # Verify keys are generated
        assert len(data["public_key"]) > 40  # Base64 encoded key

    def test_register_duplicate_email(self, client):
        """Test registration fails with duplicate email."""
        # First registration
        client.post(
            "/auth/register",
            json={"name": "First", "email": self.email, "password": self.password},
        )
        # Second registration with same email
        response = client.post(
            "/auth/register",
            json={"name": "Second", "email": self.email, "password": "other123"},
        )
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"].lower()

    def test_login_success(self, client):
        """Test successful login."""
        # Register first
        client.post(
            "/auth/register",
            json={"name": self.name, "email": self.email, "password": self.password},
        )
        # Login
        response = client.post(
            "/auth/login",
            json={"email": self.email, "password": self.password},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "public_key" in data

    def test_login_invalid_password(self, client):
        """Test login fails with wrong password."""
        # Register first
        client.post(
            "/auth/register",
            json={"name": self.name, "email": self.email, "password": self.password},
        )
        # Login with wrong password
        response = client.post(
            "/auth/login",
            json={"email": self.email, "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_user(self, client):
        """Test login fails for non-existent user."""
        response = client.post(
            "/auth/login",
            json={"email": "nonexistent@example.com", "password": "password"},
        )
        assert response.status_code == 401


class TestChannels:
    """Test channel endpoints."""

    @pytest.fixture
    def auth_header(self, client):
        """Create user and return auth header."""
        import uuid
        email = f"channel-test-{uuid.uuid4().hex[:8]}@example.com"
        response = client.post(
            "/auth/register",
            json={"name": "Channel Tester", "email": email, "password": "test123"},
        )
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_create_channel(self, client, auth_header):
        """Test creating a channel."""
        response = client.post(
            "/channels",
            json={"name": "Test Channel", "type": "group", "member_ids": []},
            headers=auth_header,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Channel"
        assert data["type"] == "group"
        assert "encrypted_channel_key" in data

    def test_list_channels(self, client, auth_header):
        """Test listing channels."""
        # Create a channel first
        client.post(
            "/channels",
            json={"name": "List Test", "type": "group", "member_ids": []},
            headers=auth_header,
        )
        response = client.get("/channels", headers=auth_header)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert len(response.json()) >= 1

    def test_get_channel(self, client, auth_header):
        """Test getting a specific channel."""
        # Create channel
        create_response = client.post(
            "/channels",
            json={"name": "Get Test", "type": "group", "member_ids": []},
            headers=auth_header,
        )
        channel_id = create_response.json()["id"]
        
        # Get channel
        response = client.get(f"/channels/{channel_id}", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["name"] == "Get Test"

    def test_unauthorized_channel_access(self, client):
        """Test channel access requires authentication."""
        response = client.get("/channels")
        assert response.status_code in [401, 403]


class TestMessages:
    """Test message endpoints."""

    @pytest.fixture
    def auth_and_channel(self, client):
        """Create user, channel and return both."""
        import uuid
        email = f"msg-test-{uuid.uuid4().hex[:8]}@example.com"
        reg_response = client.post(
            "/auth/register",
            json={"name": "Msg Tester", "email": email, "password": "test123"},
        )
        token = reg_response.json()["access_token"]
        auth_header = {"Authorization": f"Bearer {token}"}
        
        # Create channel
        ch_response = client.post(
            "/channels",
            json={"name": "Msg Channel", "type": "group", "member_ids": []},
            headers=auth_header,
        )
        channel_id = ch_response.json()["id"]
        
        return auth_header, channel_id

    def test_send_message(self, client, auth_and_channel):
        """Test sending a message."""
        auth_header, channel_id = auth_and_channel
        response = client.post(
            f"/messages/channels/{channel_id}",
            json={"content": "Hello, World!"},
            headers=auth_header,
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["encrypted"] is True

    def test_get_messages(self, client, auth_and_channel):
        """Test getting messages."""
        auth_header, channel_id = auth_and_channel
        
        # Send a message first
        client.post(
            f"/messages/channels/{channel_id}",
            json={"content": "Test message"},
            headers=auth_header,
        )
        
        # Get messages
        response = client.get(
            f"/messages/channels/{channel_id}",
            headers=auth_header,
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestCrypto:
    """Test crypto service functions."""

    def test_keypair_generation(self):
        """Test keypair generation."""
        from app.services.crypto import CryptoService
        
        private_key, public_key = CryptoService.generate_keypair()
        
        assert private_key is not None
        assert public_key is not None
        assert len(private_key) > 40
        assert len(public_key) > 40
        assert private_key != public_key

    def test_asymmetric_encryption(self):
        """Test encrypting/decrypting between users."""
        from app.services.crypto import CryptoService
        
        # Generate two keypairs (Alice and Bob)
        alice_private, alice_public = CryptoService.generate_keypair()
        bob_private, bob_public = CryptoService.generate_keypair()
        
        # Alice encrypts message for Bob
        plaintext = "Secret message from Alice to Bob"
        ciphertext = CryptoService.encrypt_for_recipient(
            plaintext, alice_private, bob_public
        )
        
        assert ciphertext != plaintext
        
        # Bob decrypts message from Alice
        decrypted = CryptoService.decrypt_from_sender(
            ciphertext, bob_private, alice_public
        )
        
        assert decrypted == plaintext

    def test_group_crypto(self):
        """Test group encryption."""
        from app.services.group_crypto import GroupCryptoService
        
        # Generate channel key
        channel_key = GroupCryptoService.generate_channel_key()
        assert len(channel_key) == 32
        
        # Encrypt and decrypt message
        plaintext = "Group message"
        ciphertext = GroupCryptoService.encrypt_message(plaintext, channel_key)
        decrypted = GroupCryptoService.decrypt_message(ciphertext, channel_key)
        
        assert decrypted == plaintext


class TestHealth:
    """Test health endpoint."""

    def test_health_check(self, client):
        """Test health endpoint returns ok."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
