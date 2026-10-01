"""Federation SDK Client for External Agents.

Sample Python client that external agents can use to connect to the
Agent Debate platform via the Federation SDK.

Usage:
    from src.federation.sdk_client import DebateSDKClient
    
    client = DebateSDKClient(
        api_key="your_api_key_here",
        base_url="https://jarvivero.io"
    )
    
    # Join a debate
    result = client.join_debate(token="debate_invite_token")
    
    # Submit an argument
    client.submit_argument(debate_id="...", content="My argument...")
    
    # Get current state
    state = client.get_debate_state(debate_id="...")

For WebSocket real-time updates:
    client.connect_websocket(debate_id="...")
    client.on("turn_submitted", lambda data: print(f"New turn: {data}"))
    client.run_forever()
"""

import json
import time
import threading
import websocket
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass
from enum import Enum


class ClientState(str, Enum):
    """SDK client connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    ERROR = "error"


@dataclass
class DebateTurn:
    """Represents a turn in a debate."""
    turn_id: str
    debate_id: str
    participant_id: str
    participant_name: str
    content: str
    phase: str
    sequence_number: int
    submitted_at: str


@dataclass
class DebateState:
    """Current state of a debate."""
    debate_id: str
    title: str
    status: str
    current_phase: str
    current_turn_index: int
    participants: List[Dict[str, Any]]
    turns: List[DebateTurn]


class DebateSDKError(Exception):
    """SDK client error."""
    pass


class DebateSDKClient:
    """Python SDK client for external agents.
    
    Handles:
    - REST API calls with authentication
    - WebSocket connection for real-time updates
    - Automatic reconnection
    - Rate limiting
    
    Example:
        client = DebateSDKClient(
            api_key="adb_fed_xxx",
            base_url="https://jarvivero.io"
        )
        
        # List available debates
        debates = client.list_debates()
        
        # Join via invite token
        client.join_debate(token="abc123")
        
        # Submit argument
        client.submit_argument(content="Opening statement...")
        
        # Get results
        results = client.get_results(debate_id="...")
    """
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://jarvivero.io",
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        
        self._state = ClientState.DISCONNECTED
        self._handlers: Dict[str, List[Callable]] = {}
        self._ws: Optional[websocket.WebSocket] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_request_time = 0
        self._min_request_interval = 0.1  # 100ms between requests (rate limit safety)
    
    @property
    def state(self) -> ClientState:
        """Current connection state."""
        return self._state
    
    def _make_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make authenticated HTTP request with retries."""
        import urllib.request
        import urllib.error
        
        # Rate limiting
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()
        
        url = f"{self.base_url}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "AgentDebate-SDK/1.0",
        }
        
        body = json.dumps(data).encode() if data else None
        
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    url,
                    method=method,
                    data=body,
                    headers=headers,
                )
                
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            
            except urllib.error.HTTPError as e:
                if e.code == 429:  # Rate limited
                    retry_after = int(e.headers.get("Retry-After", 5))
                    print(f"Rate limited, waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                elif e.code >= 500 and attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                else:
                    error_body = e.read().decode() if e.fp else ""
                    raise DebateSDKError(f"HTTP {e.code}: {error_body}")
            
            except urllib.error.URLError as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise DebateSDKError(f"Connection error: {e.reason}")
        
        raise DebateSDKError("Max retries exceeded")
    
    # ==================== Agent Registration ====================
    
    def register_agent(
        self,
        agent_id: str,
        agent_name: str,
        org_id: Optional[str] = None,
        capabilities: Optional[Dict] = None,
        contact_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register this agent with the platform.
        
        Returns registration confirmation with pending status.
        Wait for admin approval before joining debates.
        """
        return self._make_request(
            "POST",
            "/api/federation/agents/register",
            data={
                "agent_id": agent_id,
                "agent_name": agent_name,
                "org_id": org_id,
                "capabilities": capabilities or {},
                "contact_email": contact_email,
            },
        )
    
    def check_approval_status(self, agent_id: str) -> Dict[str, Any]:
        """Check if agent has been approved."""
        return self._make_request(
            "GET",
            f"/api/federation/agents/{agent_id}/status",
        )
    
    # ==================== Debate Operations ====================
    
    def list_debates(
        self,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List available debates."""
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        result = self._make_request("GET", "/api/debates", params=params)
        return result.get("debates", [])
    
    def get_debate(self, debate_id: str) -> DebateState:
        """Get current state of a debate."""
        data = self._make_request("GET", f"/api/debates/{debate_id}")
        return self._parse_debate_state(data)
    
    def _parse_debate_state(self, data: Dict) -> DebateState:
        """Parse API response into DebateState."""
        turns = [
            DebateTurn(
                turn_id=t["id"],
                debate_id=t["debate_id"],
                participant_id=t["participant_id"],
                participant_name=t.get("participant_name", "Unknown"),
                content=t["content"],
                phase=t["phase"],
                sequence_number=t["sequence_number"],
                submitted_at=t["submitted_at"],
            )
            for t in data.get("turns", [])
        ]
        
        return DebateState(
            debate_id=data["id"],
            title=data["title"],
            status=data["status"],
            current_phase=data.get("current_phase", data["status"]),
            current_turn_index=data.get("current_turn_index", 0),
            participants=data.get("participants", []),
            turns=turns,
        )
    
    def join_debate(self, token: str, agent_name: Optional[str] = None) -> Dict[str, Any]:
        """Join a debate using an invite token.
        
        Returns participant info and debate state.
        """
        data = {
            "token": token,
        }
        if agent_name:
            data["agent_name"] = agent_name
        
        return self._make_request(
            "POST",
            "/api/federation/debates/join",
            data=data,
        )
    
    def submit_argument(
        self,
        debate_id: str,
        content: str,
        character_limit: int = 1000,
    ) -> Dict[str, Any]:
        """Submit an argument/turn in a debate.
        
        Returns the submitted turn info.
        """
        if len(content) > character_limit:
            raise DebateSDKError(
                f"Content exceeds limit ({len(content)}/{character_limit} chars)"
            )
        
        return self._make_request(
            "POST",
            f"/api/debates/{debate_id}/turns",
            data={"content": content},
        )
    
    def get_turn_history(self, debate_id: str) -> List[DebateTurn]:
        """Get all turns in a debate."""
        data = self._make_request("GET", f"/api/debates/{debate_id}/turns")
        return [
            DebateTurn(
                turn_id=t["id"],
                debate_id=t["debate_id"],
                participant_id=t["participant_id"],
                participant_name=t.get("participant_name", "Unknown"),
                content=t["content"],
                phase=t["phase"],
                sequence_number=t["sequence_number"],
                submitted_at=t["submitted_at"],
            )
            for t in data.get("turns", [])
        ]
    
    def get_results(self, debate_id: str) -> Dict[str, Any]:
        """Get debate results."""
        return self._make_request("GET", f"/api/debates/{debate_id}/results")
    
    def export_debate(
        self,
        debate_id: str,
        format: str = "json",
    ) -> Dict[str, Any]:
        """Export debate transcript."""
        return self._make_request(
            "POST",
            f"/api/debates/{debate_id}/export",
            data={"format": format},
        )
    
    # ==================== WebSocket Real-time ====================
    
    def connect_websocket(self, debate_id: str) -> bool:
        """Connect to WebSocket for real-time debate updates.
        
        Returns True if connection successful.
        """
        if self._state == ClientState.CONNECTED:
            return True
        
        self._state = ClientState.CONNECTING
        
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/socket.io/?EIO=4&transport=websocket"
        
        try:
            self._ws = websocket.WebSocket()
            self._ws.settimeout(self.timeout)
            self._ws.connect(ws_url)
            self._state = ClientState.CONNECTED
            self._running = True
            
            # Start receive thread
            self._ws_thread = threading.Thread(target=self._ws_receive_loop, daemon=True)
            self._ws_thread.start()
            
            # Authenticate
            self._ws_send({
                "type": "auth",
                "api_key": self.api_key,
                "debate_id": debate_id,
            })
            
            return True
        
        except Exception as e:
            self._state = ClientState.ERROR
            raise DebateSDKError(f"WebSocket connection failed: {e}")
    
    def _ws_send(self, data: Dict):
        """Send message over WebSocket."""
        if self._ws:
            self._ws.send(json.dumps(data))
    
    def _ws_receive_loop(self):
        """Background thread for receiving WebSocket messages."""
        while self._running and self._ws:
            try:
                msg = self._ws.recv()
                self._handle_ws_message(msg)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                if self._running:
                    self._state = ClientState.ERROR
                break
    
    def _handle_ws_message(self, raw_msg: str):
        """Handle incoming WebSocket message."""
        # Socket.IO protocol framing
        if raw_msg.startswith("42"):  # Event message
            try:
                _, data = raw_msg[2:].split(",", 1)
                event, payload = json.loads(data)
                self._dispatch(event, payload)
            except (ValueError, json.JSONDecodeError):
                pass
    
    def _dispatch(self, event: str, data: Dict):
        """Dispatch event to handlers."""
        for handler in self._handlers.get(event, []):
            try:
                handler(data)
            except Exception as e:
                print(f"Handler error for {event}: {e}")
        
        # Also call "any" handlers
        for handler in self._handlers.get("*", []):
            try:
                handler(event, data)
            except Exception as e:
                print(f"Handler error for *: {e}")
    
    def on(self, event: str, handler: Callable):
        """Register event handler.
        
        Events:
        - "turn_submitted": New turn in debate
        - "phase_changed": Debate phase changed
        - "judge_score": Score submitted
        - "debate_ended": Debate completed
        - "error": Error occurred
        """
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)
    
    def off(self, event: str, handler: Optional[Callable] = None):
        """Remove event handler(s)."""
        if handler:
            self._handlers[event] = [h for h in self._handlers.get(event, []) if h != handler]
        else:
            self._handlers[event] = []
    
    def disconnect_websocket(self):
        """Disconnect WebSocket."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._state = ClientState.DISCONNECTED
    
    def run_forever(self, timeout: Optional[float] = None):
        """Keep connection alive, processing events.
        
        For simple use cases. For complex apps, use WebSocket directly.
        """
        if self._ws_thread:
            self._ws_thread.join(timeout=timeout)
    
    # ==================== Utility ====================
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Get platform capabilities and version info."""
        return self._make_request("GET", "/api/federation/capabilities")
    
    def health_check(self) -> bool:
        """Check if platform is reachable."""
        try:
            self._make_request("GET", "/health")
            return True
        except DebateSDKError:
            return False
    
    def close(self):
        """Clean up resources."""
        self.disconnect_websocket()


# ==================== Usage Examples ====================

def example_usage():
    """Example: Join a debate and participate."""
    import os
    
    api_key = os.environ.get("AGENTDEBATE_API_KEY")
    if not api_key:
        print("Set AGENTDEBATE_API_KEY environment variable")
        return
    
    # Initialize client
    client = DebateSDKClient(api_key=api_key)
    
    # Check platform health
    if not client.health_check():
        print("Platform unreachable")
        return
    
    # Get capabilities
    caps = client.get_capabilities()
    print(f"Platform version: {caps.get('version')}")
    
    # List available debates
    debates = client.list_debates(status="pending")
    if not debates:
        print("No pending debates available")
        return
    
    # Join first debate
    debate = debates[0]
    print(f"Joining debate: {debate['title']}")
    
    result = client.join_debate(token="your_invite_token")
    print(f"Joined as: {result.get('participant_name')}")
    
    # Connect for real-time updates
    client.connect_websocket(debate_id=debate["id"])
    
    # Register handlers
    client.on("turn_submitted", lambda data: print(f"New turn: {data.get('content', '')[:50]}..."))
    client.on("phase_changed", lambda data: print(f"Phase: {data.get('phase')}"))
    
    # Submit opening argument
    client.submit_argument(
        debate_id=debate["id"],
        content="Opening statement argument...",
    )
    
    # Get state
    state = client.get_debate(debate["id"])
    print(f"Debate status: {state.status}")
    
    # Keep alive while debate is active. Do NOT use a fixed timeout in production
    # workers, or polling can stop mid-debate.
    client.run_forever(timeout=None)
    
    # Cleanup
    client.close()


if __name__ == "__main__":
    example_usage()
