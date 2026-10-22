#!/usr/bin/env python3
"""Integration tests for FreeCollab API"""
import requests
import sys

BASE_URL = "http://localhost:8000"

def test_full_flow():
    print("\n" + "="*50)
    print("  FreeCollab Full API Integration Test")
    print("="*50 + "\n")
    
    errors = []
    token = None
    channel_id = None
    message_id = None
    
    # 1. Health Check
    print("[1/15] Health Check...")
    try:
        r = requests.get(f"{BASE_URL}/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        print(f"  ✓ OK - Version {r.json()['version']}")
    except Exception as e:
        errors.append(f"Health: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 2. Register
    print("[2/15] Register...")
    try:
        r = requests.post(f"{BASE_URL}/auth/register", json={
            "name": "Alice Test",
            "email": "alice@test.com",
            "password": "Secret-api-test123!"
        })
        if r.status_code == 400:  # Already exists
            print(f"  ⚠ User exists, skipping")
        else:
            assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
            print(f"  ✓ OK - User {r.json()['user_id'][:8]}... created")
    except Exception as e:
        errors.append(f"Register: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 3. Login
    print("[3/15] Login...")
    try:
        r = requests.post(f"{BASE_URL}/auth/login", json={
            "email": "alice@test.com",
            "password": "Secret-api-test123!"
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        token = r.json()["access_token"]
        print(f"  ✓ OK - Token acquired")
    except Exception as e:
        errors.append(f"Login: {e}")
        print(f"  ✗ FAIL: {e}")
        return 1
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # 4. Get Me
    print("[4/15] Get Me...")
    try:
        r = requests.get(f"{BASE_URL}/auth/me", headers=headers)
        assert r.status_code == 200
        me = r.json()
        print(f"  ✓ OK - {me['name']} <{me['email']}>")
    except Exception as e:
        errors.append(f"GetMe: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 5. Create Channel
    print("[5/15] Create Channel...")
    try:
        r = requests.post(f"{BASE_URL}/channels", headers=headers, json={
            "name": "Test Channel",
            "type": "group",
            "member_ids": []
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        channel_id = r.json()["id"]
        print(f"  ✓ OK - Channel {channel_id[:8]}... created")
    except Exception as e:
        errors.append(f"CreateChannel: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 6. List Channels
    print("[6/15] List Channels...")
    try:
        r = requests.get(f"{BASE_URL}/channels", headers=headers)
        assert r.status_code == 200
        channels = r.json()
        print(f"  ✓ OK - {len(channels)} channel(s)")
    except Exception as e:
        errors.append(f"ListChannels: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 7. Send Message
    print("[7/15] Send Message...")
    try:
        r = requests.post(f"{BASE_URL}/messages/channels/{channel_id}", headers=headers, json={
            "content": "Hello, this is a test message!"
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        message_id = r.json()["id"]
        print(f"  ✓ OK - Message {message_id[:8]}... sent")
    except Exception as e:
        errors.append(f"SendMessage: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 8. Get Messages
    print("[8/15] Get Messages...")
    try:
        r = requests.get(f"{BASE_URL}/messages/channels/{channel_id}", headers=headers)
        assert r.status_code == 200
        messages = r.json()
        print(f"  ✓ OK - {len(messages)} message(s)")
    except Exception as e:
        errors.append(f"GetMessages: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 9. Add Reaction
    print("[9/15] Add Reaction...")
    try:
        r = requests.post(f"{BASE_URL}/reactions/messages/{message_id}", headers=headers, json={
            "emoji": "👍"
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        print(f"  ✓ OK - Reaction added")
    except Exception as e:
        errors.append(f"AddReaction: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 10. Get Reactions
    print("[10/15] Get Reactions...")
    try:
        r = requests.get(f"{BASE_URL}/reactions/messages/{message_id}", headers=headers)
        assert r.status_code == 200
        reactions = r.json()
        print(f"  ✓ OK - {len(reactions)} reaction(s)")
    except Exception as e:
        errors.append(f"GetReactions: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 11. Mark Read
    print("[11/15] Mark Read...")
    try:
        r = requests.post(f"{BASE_URL}/read-receipts/channels/{channel_id}", headers=headers, json={
            "last_read_message_id": message_id
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        print(f"  ✓ OK - Read receipt marked")
    except Exception as e:
        errors.append(f"MarkRead: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 12. Thread Reply
    print("[12/15] Thread Reply...")
    try:
        r = requests.post(f"{BASE_URL}/threads/{message_id}/replies", headers=headers, json={
            "content": "This is a reply!"
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        print(f"  ✓ OK - Reply sent")
    except Exception as e:
        errors.append(f"ThreadReply: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 13. Create Post (Flow)
    print("[13/15] Create Post (Flow)...")
    try:
        r = requests.post(f"{BASE_URL}/flows/testflow/posts", headers=headers, json={
            "content": "This is a post in the flow!"
        })
        if r.status_code == 200:
            print(f"  ✓ OK - Post created")
        else:
            print(f"  ⚠ Got {r.status_code}: {r.text[:50]}...")
            errors.append(f"CreatePost: {r.status_code}")
    except Exception as e:
        errors.append(f"CreatePost: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 14. Get Notifications
    print("[14/15] Get Notifications...")
    try:
        r = requests.get(f"{BASE_URL}/notifications", headers=headers)
        assert r.status_code == 200
        notifications = r.json()
        print(f"  ✓ OK - {len(notifications)} notification(s)")
    except Exception as e:
        errors.append(f"GetNotifications: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # 15. Presence
    print("[15/15] Get Presence...")
    try:
        r = requests.get(f"{BASE_URL}/presence/status", headers=headers)
        assert r.status_code == 200
        print(f"  ✓ OK - Presence status retrieved")
    except Exception as e:
        errors.append(f"GetPresence: {e}")
        print(f"  ✗ FAIL: {e}")
    
    # Summary
    print("\n" + "="*50)
    passed = 15 - len(errors)
    if errors:
        print(f"  Result: {passed}/15 passed, {len(errors)} failed")
        print("\n  Failed tests:")
        for e in errors:
            print(f"    - {e}")
        return 1
    else:
        print(f"  Result: ALL 15 TESTS PASSED! ✓")
        return 0

if __name__ == "__main__":
    sys.exit(test_full_flow())
