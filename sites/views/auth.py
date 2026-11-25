"""Provide API views for user registration, authentication, token management, and profile retrieval."""

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
    """Generate access and refresh tokens for a user.

    Args:
        user: User instance for which tokens are generated.

    Returns:
        dict: Generated access and refresh tokens.
    """
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class RegisterView(APIView):
    """Handle user registration and initial token generation.

    Allows unauthenticated users to create an account and receive
    access and refresh tokens.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        """Register a new user and return authentication tokens.

        Args:
            request: HTTP request containing registration data.

        Returns:
            Response: Registration details and generated authentication tokens.

        Raises:
            ValidationError: If the registration data is invalid.
        """
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return success_response(
            {"id": user.pk, "username": user.get_username(), **issue_tokens(user)},
            "Registration successful",
            status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """Handle user authentication and token generation.

    Allows unauthenticated users to log in using their credentials
    and receive access and refresh tokens.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        """Authenticate a user and return authentication tokens.

        Args:
            request: HTTP request containing login credentials.

        Returns:
            Response: User details and generated authentication tokens.

        Raises:
            ValidationError: If the login data is invalid.
            APIError: If the credentials are invalid or the account is disabled.
        """
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
    """Handle refresh token validation and access token renewal.

    Supports refresh token rotation when enabled in the JWT configuration.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        """Validate a refresh token and return renewed authentication tokens.

        Args:
            request: HTTP request containing the refresh token.

        Returns:
            Response: Newly generated token data.

        Raises:
            APIError: If the refresh token is missing, invalid, or expired.
        """
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
    """Handle user logout by revoking the supplied refresh token.

    Does not require an active access token, allowing logout even
    when the access token has expired.
    """

    permission_classes = [AllowAny]  # works even if the access token already expired
    authentication_classes = []

    def post(self, request):
        """Blacklist the supplied refresh token and complete logout.

        Args:
            request: HTTP request optionally containing a refresh token.

        Returns:
            Response: Logout confirmation.
        """
        raw = request.data.get("refresh")
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass
        return success_response(message="Logged out")


class MeView(APIView):
    """Provide the authenticated user's profile information."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Retrieve the authenticated user's basic profile information.

        Args:
            request: Authenticated HTTP request.

        Returns:
            Response: User ID and username.
        """
        user = request.user
        return success_response({"id": user.pk, "username": user.get_username()})