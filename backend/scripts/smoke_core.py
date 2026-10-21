"""Exercise disposable API/Vite services: python scripts/smoke_core.py [API origin] [socket origin]."""
import sys
from threading import Event
from uuid import uuid4

import requests
import socketio


def main():
    origin = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    socket_origin = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else "http://127.0.0.1:3000"
    sessions = [requests.Session() for _ in range(3)]
    socket = socketio.Client()

    def call(index, method, path, expected=200, **kwargs):
        response = sessions[index].request(method, origin + path, timeout=20, **kwargs)
        assert response.status_code == expected, f"{method} {path}: expected {expected}, got {response.status_code}"
        return response

    try:
        assert call(0, "GET", "/health").json()["status"] == "ok"
        users = []
        for index in range(3):
            email = f"smoke-{uuid4().hex}@example.com"
            credentials = {"email": email, "password": "local-smoke-password"}
            call(index, "POST", "/auth/register", json={"name": f"Smoke {index}", **credentials})
            login = call(index, "POST", "/auth/login", json=credentials).json()
            users.append(login)
            sessions[index].headers["Authorization"] = "Bearer " + login["access_token"]
        channel = call(0, "POST", "/channels", json={"name": "Smoke private channel", "member_ids": [users[1]["user_id"]]}).json()["id"]
        received = Event()
        socket.on("message", lambda message: received.set() if message.get("content") == "smoke message" else None)
        socket.connect(socket_origin, auth={"token": users[1]["access_token"]}, transports=["websocket"])
        assert socket.call("join", {"channel_id": channel}, timeout=10) == {"ok": True}
        assert "error" in socket.call("join", {"channel_id": f'user:{users[0]["user_id"]}'}, timeout=10)
        message = call(0, "POST", f"/messages/channels/{channel}", json={"content": "smoke message"}).json()
        assert message["content"] == "smoke message" and message["encrypted"] is True
        assert received.wait(10), "Member did not receive the realtime message"
        assert call(1, "GET", f"/messages/channels/{channel}").json()[0]["content"] == "smoke message"
        call(2, "GET", f"/messages/channels/{channel}", expected=403)
        call(2, "POST", f"/threads/messages/{message['id']}/reply", expected=403, json={"content": "intrusion"})
        reply = call(1, "POST", f"/threads/messages/{message['id']}/reply", json={"content": "smoke reply"}).json()
        assert reply["content"] == "smoke reply"
        uploaded = call(0, "POST", f"/files/channels/{channel}", files={"file": ("smoke.txt", b"smoke file", "text/plain")}).json()
        assert uploaded["encrypted"] is False and "storage_path" not in uploaded
        assert call(1, "GET", f'/files/{uploaded["id"]}').content == b"smoke file"
        call(2, "GET", f'/files/{uploaded["id"]}', expected=403)
        call(1, "DELETE", f'/messages/{message["id"]}', expected=403)
        call(0, "PUT", f'/messages/{message["id"]}', json={"content": "edited"})
        call(0, "DELETE", f'/messages/{message["id"]}')
        print("PASS: login, member chat/history/realtime, threads, files, author actions and outsider denials")
    finally:
        if socket.connected:
            socket.disconnect()
        for session in sessions:
            session.close()


if __name__ == "__main__":
    main()
