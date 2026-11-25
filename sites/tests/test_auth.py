"""Provide test cases for auth"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


pytestmark = pytest.mark.django_db

User = get_user_model()

STRONG_PASSWORD = "Str0ng-Pass!2026"
REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"


def register_payload(**overrides):
    """
    Build the minimum valid payload for registering a user.

    Args:
        **overrides: Fields that replace the default values.

    Returns:
        dict: Registration data.
    """
    data = {"username": "newuser", "password": STRONG_PASSWORD}
    data.update(overrides)
    return data


def bearer(client, token):
    """
    Attach a bearer access token to an API client.

    Args:
        client: APIClient to configure.
        token: Raw access token string.

    Returns:
        APIClient: The same client, now carrying the Authorization header.
    """
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def refresh_token(owner):
    """
    Create and return a refresh token for the primary test user.
    """
    return RefreshToken.for_user(owner)



def test_register_minimal_user(anon_client):
    """
    Verify that a user can register with only a username and password.

    Checks the response envelope, returned tokens, and that the
    password is stored hashed.
    """
    response = anon_client.post(REGISTER_URL, register_payload(), format="json")

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Registration successful"
    data = body["data"]
    assert data["username"] == "newuser"
    assert data["access"]
    assert data["refresh"]
    assert "password" not in data

    user = User.objects.get(username="newuser")
    assert data["id"] == user.pk
    assert user.password != STRONG_PASSWORD
    assert user.check_password(STRONG_PASSWORD)


def test_register_with_email(anon_client):
    """
    Verify that an optional email is stored on the created user.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(email="new@example.com"), format="json"
    )

    assert response.status_code == 201, response.json()
    assert User.objects.get(username="newuser").email == "new@example.com"


def test_register_with_blank_email_allowed(anon_client):
    """
    Verify that a blank email is accepted.
    """
    response = anon_client.post(REGISTER_URL, register_payload(email=""), format="json")

    assert response.status_code == 201, response.json()


def test_register_returns_working_tokens(anon_client):
    """
    Verify that tokens issued at registration can access protected endpoints.
    """
    data = anon_client.post(REGISTER_URL, register_payload(), format="json").json()["data"]

    response = bearer(anon_client, data["access"]).get(ME_URL)

    assert response.status_code == 200
    assert response.json()["data"]["username"] == "newuser"


def test_register_duplicate_username_rejected(anon_client, owner):
    """
    Verify that registering an existing username returns a validation error.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(username="owner"), format="json"
    )

    assert response.status_code == 400, response.json()
    assert response.json()["success"] is False
    assert list(response.json()["errors"]) == ["username"]
    assert User.objects.filter(username__iexact="owner").count() == 1


def test_register_duplicate_username_is_case_insensitive(anon_client, owner):
    """
    Verify that usernames differing only by letter case count as duplicates.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(username="OWNER"), format="json"
    )

    assert response.status_code == 400, response.json()
    assert "username" in response.json()["errors"]


@pytest.mark.parametrize("missing", ["username", "password"])
def test_register_missing_required_field(anon_client, missing):
    """
    Verify that registration fails when a required field is missing.

    Args:
        missing: Required field omitted from the request payload.
    """
    payload = register_payload()
    del payload[missing]

    response = anon_client.post(REGISTER_URL, payload, format="json")

    assert response.status_code == 400, response.json()
    assert list(response.json()["errors"]) == [missing]
    assert User.objects.count() == 0


@pytest.mark.parametrize("field", ["username", "password"])
def test_register_blank_required_field_rejected(anon_client, field):
    """
    Verify that blank values are rejected for required fields.

    Args:
        field: Field submitted as an empty string.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(**{field: ""}), format="json"
    )

    assert response.status_code == 400, response.json()
    assert field in response.json()["errors"]
    assert User.objects.count() == 0


def test_register_username_too_long_rejected(anon_client):
    """
    Verify that usernames longer than 150 characters are rejected.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(username="u" * 151), format="json"
    )

    assert response.status_code == 400, response.json()
    assert list(response.json()["errors"]) == ["username"]


def test_register_username_at_max_length_allowed(anon_client):
    """
    Verify that a username of exactly 150 characters is accepted.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(username="u" * 150), format="json"
    )

    assert response.status_code == 201, response.json()


def test_register_invalid_email_rejected(anon_client):
    """
    Verify that a malformed email address is rejected.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(email="not-an-email"), format="json"
    )

    assert response.status_code == 400, response.json()
    assert list(response.json()["errors"]) == ["email"]
    assert User.objects.count() == 0


@pytest.mark.parametrize("password", ["short1", "12345678", "password", "newuser"])
def test_register_weak_password_rejected(anon_client, password):
    """
    Verify that passwords failing Django's validators are rejected.

    Relies on AUTH_PASSWORD_VALIDATORS being configured in the test settings.

    Args:
        password: Weak password submitted in the request.
    """
    response = anon_client.post(
        REGISTER_URL, register_payload(password=password), format="json"
    )

    assert response.status_code == 400, response.json()
    assert "password" in response.json()["errors"]
    assert User.objects.count() == 0


def test_register_ignores_privilege_fields(anon_client):
    """
    Verify that clients cannot grant themselves staff or superuser status.
    """
    response = anon_client.post(
        REGISTER_URL,
        register_payload(is_staff=True, is_superuser=True),
        format="json",
    )

    assert response.status_code == 201, response.json()
    user = User.objects.get(username="newuser")
    assert user.is_staff is False
    assert user.is_superuser is False


def test_register_ignores_invalid_authorization_header(anon_client):
    """
    Verify that registration does not authenticate the request, so a bad
    token in the header does not block it.
    """
    anon_client.credentials(HTTP_AUTHORIZATION="Bearer garbage")

    response = anon_client.post(REGISTER_URL, register_payload(), format="json")

    assert response.status_code == 201, response.json()



def test_login_success(anon_client, owner):
    """
    Verify that valid credentials return the user and a token pair.
    """
    response = anon_client.post(
        LOGIN_URL, {"username": "owner", "password": "pass12345"}, format="json"
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Login successful"
    data = body["data"]
    assert data["id"] == owner.pk
    assert data["username"] == "owner"
    assert data["access"]
    assert data["refresh"]
    assert "password" not in data


def test_login_tokens_authenticate_the_user(anon_client, owner):
    """
    Verify that the access token from login identifies the logging-in user.
    """
    data = anon_client.post(
        LOGIN_URL, {"username": "owner", "password": "pass12345"}, format="json"
    ).json()["data"]

    response = bearer(anon_client, data["access"]).get(ME_URL)

    assert response.status_code == 200
    assert response.json()["data"]["id"] == owner.pk


def test_login_wrong_password_returns_401(anon_client, owner):
    """
    Verify that an incorrect password is rejected.
    """
    response = anon_client.post(
        LOGIN_URL, {"username": "owner", "password": "wrong-pass"}, format="json"
    )

    assert response.status_code == 401, response.json()
    assert response.json()["success"] is False
    assert response.json()["message"] == "Invalid username or password."


def test_login_unknown_user_returns_401(anon_client):
    """
    Verify that an unknown username returns the same error as a wrong
    password, so account existence is not revealed.
    """
    response = anon_client.post(
        LOGIN_URL, {"username": "ghost", "password": "pass12345"}, format="json"
    )

    assert response.status_code == 401, response.json()
    assert response.json()["message"] == "Invalid username or password."


def test_login_inactive_user_is_rejected(anon_client, owner):
    """
    Verify that a disabled account cannot log in.

    Django's default ModelBackend returns None for inactive users, so the
    request is rejected with 401 before the view's 403 branch is reached.
    """
    owner.is_active = False
    owner.save()

    response = anon_client.post(
        LOGIN_URL, {"username": "owner", "password": "pass12345"}, format="json"
    )

    assert response.status_code in (401, 403), response.json()
    assert "access" not in (response.json().get("data") or {})


@pytest.mark.parametrize("missing", ["username", "password"])
def test_login_missing_field(anon_client, owner, missing):
    """
    Verify that login fails when a required field is missing.

    Args:
        missing: Required field omitted from the request payload.
    """
    payload = {"username": "owner", "password": "pass12345"}
    del payload[missing]

    response = anon_client.post(LOGIN_URL, payload, format="json")

    assert response.status_code == 400, response.json()
    assert list(response.json()["errors"]) == [missing]


def test_login_empty_body_rejected(anon_client):
    """
    Verify that an empty login request lists both missing fields.
    """
    response = anon_client.post(LOGIN_URL, {}, format="json")

    assert response.status_code == 400, response.json()
    assert set(response.json()["errors"]) == {"username", "password"}



def test_refresh_returns_new_access_token(anon_client, refresh_token):
    """
    Verify that a valid refresh token returns a new access token.
    """
    response = anon_client.post(REFRESH_URL, {"refresh": str(refresh_token)}, format="json")

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Token refreshed"
    assert body["data"]["access"]


def test_refreshed_access_token_works(anon_client, owner, refresh_token):
    """
    Verify that the access token returned by refresh authenticates requests.
    """
    access = anon_client.post(
        REFRESH_URL, {"refresh": str(refresh_token)}, format="json"
    ).json()["data"]["access"]

    response = bearer(anon_client, access).get(ME_URL)

    assert response.status_code == 200
    assert response.json()["data"]["id"] == owner.pk


@pytest.mark.parametrize("payload", [{}, {"refresh": ""}, {"refresh": None}])
def test_refresh_missing_token_returns_400(anon_client, payload):
    """
    Verify that a missing or empty refresh token is rejected.

    Args:
        payload: Request body without a usable refresh value.
    """
    response = anon_client.post(REFRESH_URL, payload, format="json")

    assert response.status_code == 400, response.json()
    assert response.json()["message"] == "Refresh token missing."


def test_refresh_garbage_token_returns_401(anon_client):
    """
    Verify that a malformed refresh token is rejected.
    """
    response = anon_client.post(REFRESH_URL, {"refresh": "not-a-jwt"}, format="json")

    assert response.status_code == 401, response.json()
    assert response.json()["message"] == "Invalid or expired refresh token."


def test_refresh_with_access_token_returns_401(anon_client, refresh_token):
    """
    Verify that an access token cannot be used in place of a refresh token.
    """
    response = anon_client.post(
        REFRESH_URL, {"refresh": str(refresh_token.access_token)}, format="json"
    )

    assert response.status_code == 401, response.json()


def test_refresh_expired_token_returns_401(anon_client, refresh_token):
    """
    Verify that an expired refresh token is rejected.
    """
    refresh_token.set_exp(lifetime=-timedelta(seconds=10))

    response = anon_client.post(REFRESH_URL, {"refresh": str(refresh_token)}, format="json")

    assert response.status_code == 401, response.json()


def test_refresh_blacklisted_token_returns_401(anon_client, refresh_token):
    """
    Verify that a blacklisted refresh token can no longer be used.
    """
    refresh_token.blacklist()

    response = anon_client.post(REFRESH_URL, {"refresh": str(refresh_token)}, format="json")

    assert response.status_code == 401, response.json()



def test_logout_blacklists_refresh_token(anon_client, refresh_token):
    """
    Verify that logging out revokes the refresh token.
    """
    response = anon_client.post(LOGOUT_URL, {"refresh": str(refresh_token)}, format="json")

    assert response.status_code == 200, response.json()
    assert response.json()["success"] is True
    assert response.json()["message"] == "Logged out"

    after = anon_client.post(REFRESH_URL, {"refresh": str(refresh_token)}, format="json")
    assert after.status_code == 401


def test_logout_twice_is_idempotent(anon_client, refresh_token):
    """
    Verify that logging out with an already revoked token still succeeds.
    """
    payload = {"refresh": str(refresh_token)}
    anon_client.post(LOGOUT_URL, payload, format="json")

    response = anon_client.post(LOGOUT_URL, payload, format="json")

    assert response.status_code == 200


def test_logout_without_token_succeeds(anon_client):
    """
    Verify that logout without a refresh token is a harmless no-op.
    """
    response = anon_client.post(LOGOUT_URL, {}, format="json")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_logout_with_invalid_token_succeeds(anon_client):
    """
    Verify that logout silently ignores an invalid refresh token.
    """
    response = anon_client.post(LOGOUT_URL, {"refresh": "not-a-jwt"}, format="json")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_logout_does_not_require_valid_access_token(anon_client, refresh_token):
    """
    Verify that logout works even when the access token has expired.
    """
    expired = AccessToken.for_user(User.objects.get())
    expired.set_exp(lifetime=-timedelta(seconds=10))
    bearer(anon_client, str(expired))

    response = anon_client.post(LOGOUT_URL, {"refresh": str(refresh_token)}, format="json")

    assert response.status_code == 200


def test_logout_only_revokes_the_given_token(anon_client, owner, refresh_token):
    """
    Verify that logging out one session leaves other sessions valid.
    """
    other_session = RefreshToken.for_user(owner)

    anon_client.post(LOGOUT_URL, {"refresh": str(refresh_token)}, format="json")
    response = anon_client.post(REFRESH_URL, {"refresh": str(other_session)}, format="json")

    assert response.status_code == 200



def test_me_returns_current_user(auth_client, owner):
    """
    Verify that the me endpoint returns the authenticated user's identity.
    """
    response = auth_client.get(ME_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"id": owner.pk, "username": "owner"}


def test_me_with_bearer_token(anon_client, owner, refresh_token):
    """
    Verify that a real JWT access token authenticates the me endpoint.
    """
    response = bearer(anon_client, str(refresh_token.access_token)).get(ME_URL)

    assert response.status_code == 200
    assert response.json()["data"]["id"] == owner.pk


def test_me_requires_login(anon_client):
    """
    Verify that unauthenticated users cannot access the me endpoint.
    """
    assert anon_client.get(ME_URL).status_code in (401, 403)


def test_me_garbage_token_rejected(anon_client):
    """
    Verify that a malformed access token is rejected.
    """
    assert bearer(anon_client, "garbage").get(ME_URL).status_code in (401, 403)


def test_me_expired_token_rejected(anon_client, owner):
    """
    Verify that an expired access token is rejected.
    """
    token = AccessToken.for_user(owner)
    token.set_exp(lifetime=-timedelta(seconds=10))

    assert bearer(anon_client, str(token)).get(ME_URL).status_code in (401, 403)


def test_me_refresh_token_is_not_an_access_token(anon_client, refresh_token):
    """
    Verify that a refresh token cannot be used as an access token.
    """
    response = bearer(anon_client, str(refresh_token)).get(ME_URL)

    assert response.status_code in (401, 403)


def test_me_token_of_deleted_user_rejected(anon_client, owner):
    """
    Verify that a token issued to a since-deleted user stops working.
    """
    token = str(AccessToken.for_user(owner))
    owner.delete()

    assert bearer(anon_client, token).get(ME_URL).status_code in (401, 403)


def test_me_token_of_inactive_user_rejected(anon_client, owner):
    """
    Verify that deactivating a user invalidates their existing access token.
    """
    token = str(AccessToken.for_user(owner))
    owner.is_active = False
    owner.save()

    assert bearer(anon_client, token).get(ME_URL).status_code in (401, 403)