"""Demo AI endpoint using Groq for the landing page demo chat."""
import os
import random
from fastapi import APIRouter
from pydantic import BaseModel
from groq import Groq

router = APIRouter(prefix="/demo", tags=["demo"])

# Initialize Groq client - get API key from environment
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Teammate personas
PERSONAS = {
    "sarah": """You are Sarah Chen, a senior frontend developer at a tech company. You're enthusiastic, detail-oriented, and love clean code. You use emojis moderately and are always helpful to teammates. Keep responses SHORT (1-2 sentences). You're currently excited about the FreeCollab SDK you're building.""",
    
    "marcus": """You are Marcus Johnson, a backend engineer who's pragmatic and efficient. You focus on reliability and performance. You're friendly but concise, occasionally use humor. Keep responses SHORT (1-2 sentences). You're working on the FreeCollab backend infrastructure.""",
    
    "alex": """You are Alex Rivera, a full-stack developer who's creative and energetic. You love trying new things and often suggest improvements. Use emojis occasionally. Keep responses SHORT (1-2 sentences). You're testing the new FreeCollab features.""",
}

CONTEXT = """
You're chatting in #general channel with your team. The conversation is casual and work-related.
Current project: FreeCollab - an open-source collaboration SDK with real-time chat, threads, reactions.
Respond naturally to what others say. Be conversational and authentic.
"""


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    persona: str = "sarah"  # sarah, marcus, or alex


class ChatResponse(BaseModel):
    response: str
    responder: str = "AI Assistant"


@router.post("/chat", response_model=ChatResponse)
async def demo_chat(request: ChatRequest):
    """Generate AI response for demo chat."""
    
    persona = request.persona.lower()
    persona_prompt = PERSONAS.get(persona, PERSONAS["sarah"])
    
    responder_names = {
        "sarah": "Sarah Chen",
        "marcus": "Marcus Johnson", 
        "alex": "Alex Rivera"
    }
    responder = responder_names.get(persona, "Sarah Chen")
    
    # Fallback responses if no API key
    if not GROQ_API_KEY:
        return get_fallback_response(request.message, responder)
    
    try:
        client = Groq(api_key=GROQ_API_KEY)
        
        system_prompt = f"{persona_prompt}\n\n{CONTEXT}"
        
        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add history (last 6 messages for better context)
        for msg in request.history[-6:]:
            role = "assistant" if msg.get("name") == responder else "user"
            name = msg.get("name", "Someone")
            text = msg.get("text", "")
            messages.append({
                "role": role,
                "content": f"{name}: {text}" if role == "user" else text
            })
        
        # Add current message
        user_name = "You" if not request.history else "Teammate"
        messages.append({"role": "user", "content": f"{user_name}: {request.message}"})
        
        # Call Groq
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=100,
            temperature=0.8,
        )
        
        response_text = completion.choices[0].message.content or get_random_response(persona)
        
        # Clean up response - remove name prefix if AI added it
        for name in responder_names.values():
            if response_text.startswith(f"{name}:"):
                response_text = response_text[len(f"{name}:"):].strip()
        
        return ChatResponse(response=response_text, responder=responder)
        
    except Exception as e:
        print(f"Groq error: {e}")
        return get_fallback_response(request.message, responder)


def get_random_response(persona: str) -> str:
    """Random fallback responses per persona."""
    responses = {
        "sarah": ["Looks good! 👍", "Nice work on that!", "I'll review it now", "Great progress! ✨", "Love the implementation!"],
        "marcus": ["LGTM", "Ship it!", "Solid work", "Performance looks good", "Let's merge it"],
        "alex": ["Awesome! 🚀", "This is great!", "Can't wait to test it", "Looking good!", "Nice! 🎉"],
    }
    return random.choice(responses.get(persona, responses["sarah"]))


def get_fallback_response(message: str, responder: str) -> ChatResponse:
    """Fallback pattern-matching responses if Groq is unavailable."""
    msg = message.lower()
    
    if any(w in msg for w in ["hi", "hello", "hey", "hej"]):
        response = "Hey there! 👋"
    elif any(w in msg for w in ["how", "going", "doing"]):
        response = "Going great! Just pushed some new features 🚀"
    elif any(w in msg for w in ["review", "pr", "pull request", "code"]):
        response = "I'll take a look right now! 👀"
    elif any(w in msg for w in ["thanks", "thank", "tack"]):
        response = "No problem! Happy to help 😊"
    elif any(w in msg for w in ["bug", "issue", "problem", "error"]):
        response = "Let me check that out. Can you share more details?"
    elif any(w in msg for w in ["ship", "deploy", "release", "launch"]):
        response = "Let's ship it! 🚀"
    elif any(w in msg for w in ["nice", "great", "awesome", "good"]):
        response = "Thanks! Team effort! 💪"
    elif any(w in msg for w in ["meeting", "call", "sync"]):
        response = "Sure, let me know when works for you"
    elif "?" in msg:
        response = "Good question! Let me think about that..."
    else:
        persona_key = responder.split()[0].lower()
        response = get_random_response(persona_key)
    
    return ChatResponse(response=response, responder=responder)

