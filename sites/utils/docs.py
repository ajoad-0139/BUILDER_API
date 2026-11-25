"""Helpers that describe the success_response envelope for API docs."""

from rest_framework import serializers
from drf_spectacular.utils import inline_serializer
from drf_spectacular.openapi import AutoSchema


class PrestoAutoSchema(AutoSchema):
    """Name GET operations on *List views '<...>_list' instead of '<...>_retrieve'."""

    def get_operation_id(self):
        op_id = super().get_operation_id()
        if (
            self.method == "GET"
            and self.view.__class__.__name__.endswith("List")
            and op_id.endswith("_retrieve")
        ):
            return op_id[: -len("_retrieve")] + "_list"
        return op_id

def envelope(name, data=None):
    """Describe {"message": ..., "data": ...}. Edit to match success_response."""
    fields = {"message": serializers.CharField()}
    if data is not None:
        fields["data"] = data
    return inline_serializer(name=name, fields=fields)


def paginated(name, item_serializer):
    """Describe a paginated list. Edit to match DefaultPagination.get_paginated_data."""
    page = inline_serializer(
        name=f"{name}Page",
        fields={
            "count": serializers.IntegerField(),
            "next": serializers.CharField(allow_null=True),
            "previous": serializers.CharField(allow_null=True),
            "results": item_serializer(many=True),
        },
    )
    return envelope(name, page)

def _user_fields():
    return {"id": serializers.IntegerField(), "username": serializers.CharField()}


def _token_fields():
    return {"access": serializers.CharField(), "refresh": serializers.CharField()}