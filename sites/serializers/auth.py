"""Serializers for user authentication and registration."""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    """Validate user login credentials."""

    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class RegisterSerializer(serializers.Serializer):
    """Validate and create new user accounts."""

    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True)

    def validate_username(self, value):
        """Ensure the username is not already registered.

        Args:
            value: Username provided during registration.

        Raises:
            serializers.ValidationError: If the username already exists.

        Returns:
            str: The validated username.
        """
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with that username already exists.")
        return value

    def validate_password(self, value):
        """Validate the password against configured password validators.

        Args:
            value: Password provided during registration.

        Returns:
            str: The validated password.
        """
        validate_password(value)
        return value

    def create(self, validated_data):
        """Create a user account using the validated registration data.

        Args:
            validated_data: Validated username, email, and password.

        Returns:
            User: The newly created user instance.
        """
        return User.objects.create_user(**validated_data)