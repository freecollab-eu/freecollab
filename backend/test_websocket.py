"""WebSocket real-time test for FreeCollab"""
import asyncio
import socketio
import requests

BASE_URL = "http://localhost:8000"

# Create Socket.IO client
sio = socketio.AsyncClient()

@sio.event
async def connect():
    print("✓ Connected to WebSocket!")

@sio.event
async def disconnect():
    print("✗ Disconnected from WebSocket")

@sio.on("new_message")
async def on_new_message(data):
    print(f"📨 NEW MESSAGE: {data}")

@sio.on("typing")
async def on_typing(data):
    print(f"⌨️  TYPING: {data}")

@sio.on("presence_update")
async def on_presence(data):
    print(f"👤 PRESENCE: {data}")

@sio.on("reaction_added")
async def on_reaction(data):
    print(f"👍 REACTION: {data}")

@sio.on("*")
async def catch_all(event, data):
    print(f"📡 EVENT [{event}]: {data}")

async def main():
    print("=" * 50)
    print("   FreeCollab WebSocket Test")
    print("=" * 50)
    
    # Login first
    print("\n1. Logging in...")
    r = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "complete@test.com",
        "password": "Pass123!"
    })
    if r.status_code != 200:
        print(f"   Login failed: {r.text}")
        return
    
    token = r.json()["access_token"]
    user_id = r.json()["user_id"]
    print(f"   ✓ Logged in as {user_id[:8]}...")
    
    # Connect to WebSocket
    print("\n2. Connecting to WebSocket...")
    try:
        await sio.connect(
            BASE_URL,
            auth={"token": token},
            transports=["websocket", "polling"]
        )
    except Exception as e:
        print(f"   Connection error: {e}")
        return
    
    # Join a channel
    print("\n3. Joining channel room...")
    await sio.emit("join_channel", {"channel_id": "test-channel"})
    
    # Update presence
    print("\n4. Setting presence to 'online'...")
    await sio.emit("presence_update", {"status": "online"})
    
    # Start typing
    print("\n5. Emitting typing indicator...")
    await sio.emit("typing_start", {"channel_id": "test-channel"})
    
    # Wait for events
    print("\n6. Listening for events (5 seconds)...")
    await asyncio.sleep(5)
    
    # Disconnect
    print("\n7. Disconnecting...")
    await sio.disconnect()
    
    print("\n" + "=" * 50)
    print("   WebSocket Test Complete!")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(main())
