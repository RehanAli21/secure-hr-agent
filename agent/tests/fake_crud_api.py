# A fake CRUD API for the tests, so they run without the real backend and database.
#
# httpx.MockTransport lets us answer HTTP requests with a plain Python function instead of
# sending them over the network. Our fake knows one user and remembers every request it got,
# so a test can check exactly what the agent service sent.

import json

import httpx

EMAIL = "ravi@test.local"
PASSWORD = "Password123!"
REFRESH_TOKEN = "refresh-token-1"
ME = {"id": 3, "email": EMAIL, "kind": "user", "employee_id": 7, "role": "employee"}


class FakeCrudApi:
    def __init__(self) -> None:
        self.access_token = "access-token-1"  # the only access token that works right now
        self.refresh_works = True             # set to False to pretend the login has ended
        self.requests: list[httpx.Request] = []

    def expire_access_token(self) -> None:
        """Pretend 15 minutes have passed: the old access token stops working."""
        self.access_token = "access-token-2"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})

        if path == "/auth/login":
            body = json.loads(request.content)
            if body == {"email": EMAIL, "password": PASSWORD}:
                return httpx.Response(200, json={"access_token": self.access_token, "refresh_token": REFRESH_TOKEN})
            return httpx.Response(401, json={"detail": "Wrong email or password"})

        if path == "/auth/refresh":
            body = json.loads(request.content)
            if self.refresh_works and body == {"refresh_token": REFRESH_TOKEN}:
                return httpx.Response(200, json={"access_token": self.access_token})
            return httpx.Response(401, json={"detail": "Invalid token"})

        if path == "/me":
            if request.headers.get("Authorization") == f"Bearer {self.access_token}":
                return httpx.Response(200, json=ME)
            return httpx.Response(401, json={"detail": "Invalid or expired token"})

        return httpx.Response(404, json={"detail": "Not Found"})

    def client(self) -> httpx.Client:
        """An httpx client that sends every request to handle() instead of the network."""
        return httpx.Client(transport=httpx.MockTransport(self.handle), base_url="http://crud-api")
