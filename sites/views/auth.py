from django.contrib.auth import authenticate, get_user_model
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from sites.serializers.auth import LoginSerializer, RegisterSerializer

from sites.exceptions import APIError
from sites.utils.responses import success_response

User = get_user_model()


def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}



class RegisterView(APIView):
    """POST /auth/register/  -> creates the user and returns tokens."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return success_response(
            {"id": user.pk, "username": user.get_username(), **issue_tokens(user)},
            "Registration successful",
            status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """POST /auth/login/  -> returns access + refresh tokens in the body."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(request, **serializer.validated_data)
        if user is None:
            raise APIError("Invalid username or password.", 401)
        if not user.is_active:
            raise APIError("This account is disabled.", 403)

        return success_response(
            {"id": user.pk, "username": user.get_username(), **issue_tokens(user)},
            "Login successful",
        )


class RefreshView(APIView):
    """POST /auth/refresh/  body: {"refresh": "..."} -> new access (+ rotated refresh)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw = request.data.get("refresh")
        if not raw:
            raise APIError("Refresh token missing.", 400)

        serializer = TokenRefreshSerializer(data={"refresh": raw})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError:
            raise APIError("Invalid or expired refresh token.", 401)

        return success_response(serializer.validated_data, "Token refreshed")


class LogoutView(APIView):
    """POST /auth/logout/  body: {"refresh": "..."} -> revokes the refresh token."""

    permission_classes = [AllowAny]  # works even if the access token already expired
    authentication_classes = []

    def post(self, request):
        raw = request.data.get("refresh")
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass  
        return success_response(message="Logged out")


class MeView(APIView):
    """GET /auth/me/  -> who am I."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return success_response({"id": user.pk, "username": user.get_username()})